import hashlib
import hmac
import base64
import json
import logging
import secrets
import time

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel

from app.config import Settings


router = APIRouter(prefix="/auth", tags=["auth"])
SESSION_COOKIE_NAME = "diana_session"
logger = logging.getLogger("diana.auth")


class Login(BaseModel):
    password: str
    include_access_token: bool = False


def token(settings: Settings) -> str:
    if not settings.auth_signing_secret:
        raise RuntimeError("AUTH_SIGNING_SECRET is not configured.")
    return hmac.new(
        settings.auth_signing_secret.encode("utf-8"),
        b"diana-private-session",
        hashlib.sha256,
    ).hexdigest()


def _sign(settings: Settings, value: str) -> str:
    assert settings.auth_signing_secret is not None
    return hmac.new(settings.auth_signing_secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


def create_session_token(settings: Settings, *, max_age_seconds: int) -> str:
    """Create a short-lived signed session without retaining server-side state."""
    now = int(time.time())
    payload = {
        "iat": now,
        "exp": now + max_age_seconds,
        "nonce": secrets.token_urlsafe(16),
    }
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii").rstrip("=")
    return f"{encoded}.{_sign(settings, encoded)}"


def is_valid_session_token(settings: Settings, candidate: str) -> bool:
    if not candidate:
        return False
    # Existing cookie sessions remain valid until users naturally re-login.
    if hmac.compare_digest(candidate, token(settings)):
        return True
    encoded, separator, signature = candidate.partition(".")
    if not separator or not hmac.compare_digest(signature, _sign(settings, encoded)):
        return False
    try:
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        return isinstance(payload.get("exp"), int) and payload["exp"] >= int(time.time())
    except (ValueError, json.JSONDecodeError):
        return False


def request_credential(request: Request) -> tuple[str, str]:
    cookie = request.cookies.get(SESSION_COOKIE_NAME, "")
    if cookie:
        return cookie, "cookie"
    authorization = request.headers.get("authorization", "")
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() == "bearer" and value:
        return value, "bearer"
    return "", "none"


def request_is_authenticated(settings: Settings, request: Request) -> tuple[bool, str]:
    credential, source = request_credential(request)
    return is_valid_session_token(settings, credential), source


def require_auth_settings(settings: Settings) -> None:
    if not settings.private_access_password or not settings.auth_signing_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Private access is not configured.",
        )


@router.post("/login")
async def login(payload: Login, request: Request, response: Response) -> dict[str, bool | str]:
    settings: Settings = request.app.state.settings
    logger.info("[AUTH] login requested")
    require_auth_settings(settings)
    if not hmac.compare_digest(payload.password, settings.private_access_password):
        logger.warning("[AUTH] authentication failed reason=invalid_password")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid password.")

    session_token = create_session_token(
        settings,
        max_age_seconds=settings.auth_session_max_age_seconds,
    )
    logger.info("[AUTH] password verified")
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_token,
        httponly=True,
        secure=settings.is_production,
        samesite=settings.auth_cookie_samesite,
        domain=settings.auth_cookie_domain,
        max_age=settings.auth_session_max_age_seconds,
        path="/",
    )
    logger.info(
        "[AUTH] token/session created cookie secure=%s samesite=%s domain_set=%s path=/ httponly=true",
        settings.is_production,
        settings.auth_cookie_samesite,
        bool(settings.auth_cookie_domain),
    )
    result: dict[str, bool | str] = {
        "authenticated": True,
        "persona_id": settings.persona_id or "",
        "persona_display_name": settings.persona_display_name,
    }
    if payload.include_access_token:
        # This is requested only after a cookie-authenticated check fails, for
        # browsers that block the cross-site cookie. Never log this value.
        result["access_token"] = create_session_token(
            settings,
            max_age_seconds=settings.auth_bearer_session_max_age_seconds,
        )
        logger.info("[AUTH] bearer fallback issued")
    return result


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict[str, bool]:
    settings: Settings = request.app.state.settings
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        domain=settings.auth_cookie_domain,
        path="/",
        samesite=settings.auth_cookie_samesite,
    )
    logger.info("[AUTH] logout completed")
    return {"authenticated": False}


@router.get("/me")
async def me(request: Request) -> dict[str, bool | str]:
    settings: Settings = request.app.state.settings
    require_auth_settings(settings)
    authenticated, source = request_is_authenticated(settings, request)
    logger.info("[AUTH] protected request = /auth/me credential received = %s", source)
    if not authenticated:
        logger.warning("[AUTH] authentication failed reason=missing_or_invalid_credential path=/auth/me")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    logger.info("[AUTH] authentication success path=/auth/me credential=%s", source)
    return {
        "authenticated": True,
        "persona_id": settings.persona_id or "",
        "persona_display_name": settings.persona_display_name,
    }
