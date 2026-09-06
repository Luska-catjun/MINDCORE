from contextlib import asynccontextmanager
from collections.abc import AsyncIterator, Callable
import logging

from fastapi import FastAPI,Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings, get_settings
from app.database.connection import close_pool, create_pool
from app.routers import auth, chat, conversations, episodes, health, identity, messages, observe, relationship, state
from app.routers.auth import require_auth_settings, request_is_authenticated
from app.services.prompt_loader import load_persona_identity_prompt
from app.services.mindcore.narrative import hydrate_narrative_snapshot
from app.services.mindcore.self_model import hydrate_self_model_snapshot
from app.services.mindcore.snapshot_scope import CognitiveSnapshotScope


def configure_diana_logging() -> None:
    """Make safe operational metrics visible with Uvicorn's default logging."""
    logger = logging.getLogger("diana")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def build_lifespan(
    settings_override: Settings | None = None,
    db_pool_factory: Callable[[Settings], object] | None = None,
):
  @asynccontextmanager
  async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = settings_override or get_settings()
    if settings.is_production:
        missing = []
        if not settings.private_access_password:
            missing.append("PRIVATE_ACCESS_PASSWORD")
        if not settings.auth_signing_secret:
            missing.append("AUTH_SIGNING_SECRET")
        if settings.database_backend.lower() == "turso":
            if not settings.database_url:
                missing.append("DATABASE_URL")
            if not settings.database_auth_token:
                missing.append("DATABASE_AUTH_TOKEN")
        elif not settings.supabase_db_url:
            missing.append("SUPABASE_DB_URL")
        if "*" in settings.cors_origins:
            raise RuntimeError("CORS_ORIGINS cannot include '*' in production.")
        configured_providers = {settings.llm_provider.lower()}
        if settings.llm_fallback_provider:
            configured_providers.add(settings.llm_fallback_provider.lower())
        if "groq" in configured_providers and not settings.groq_api_key:
            missing.append("GROQ_API_KEY")
        if "gemini" in configured_providers and not settings.gemini_api_key:
            missing.append("GEMINI_API_KEY")
        if missing:
            raise RuntimeError(f"Missing required production settings: {', '.join(missing)}")
    app.state.settings = settings
    app.state.diana_identity_prompt = load_persona_identity_prompt(settings)
    pool = db_pool_factory(settings) if db_pool_factory else create_pool(settings)
    app.state.db_pool = await pool if hasattr(pool, "__await__") else pool
    app.state.cognitive_snapshot_scope = CognitiveSnapshotScope()
    # One startup hydration keeps Narrative activation off the foreground chat
    # path.  Isolated ASGI fixtures without a database acquire seam simply use
    # the empty, safe snapshot.
    if hasattr(app.state.db_pool, "acquire"):
        await hydrate_narrative_snapshot(app.state.db_pool, app.state.cognitive_snapshot_scope)
        await hydrate_self_model_snapshot(app.state.db_pool, app.state.cognitive_snapshot_scope)
    try:
        yield
    finally:
        if db_pool_factory is None:
            await close_pool(app.state.db_pool)
  return lifespan


def create_app(*, settings_override: Settings | None = None, db_pool_factory: Callable[[Settings], object] | None = None) -> FastAPI:
    configure_diana_logging()
    settings = settings_override or get_settings()
    app = FastAPI(title=settings.app_name, version="0.1.1", lifespan=build_lifespan(settings_override, db_pool_factory))

    app.include_router(health.router)
    app.include_router(auth.router)
    @app.middleware("http")
    async def private_access(request: Request, call_next):
        if request.method == "OPTIONS" or request.url.path in {"/health", "/_desktop/ready", "/_desktop/shutdown"} or request.url.path.startswith("/auth/"):
            return await call_next(request)
        settings = request.app.state.settings
        try:
            require_auth_settings(settings)
        except Exception as exc:
            return JSONResponse({"detail": getattr(exc, "detail", "Private access is not configured.")}, status_code=503)
        authenticated, source = request_is_authenticated(settings, request)
        logger = logging.getLogger("diana.auth")
        logger.info("[AUTH] protected request = %s credential received = %s", request.url.path, source)
        if not authenticated:
            logger.warning("[AUTH] authentication failed reason=missing_or_invalid_credential path=%s", request.url.path)
            return JSONResponse({"detail": "Authentication required."}, status_code=401)
        logger.info("[AUTH] authentication success path=%s credential=%s", request.url.path, source)
        return await call_next(request)

    # Register CORS last so it wraps authentication's early 401/503 responses too.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(identity.router)
    app.include_router(conversations.router)
    app.include_router(messages.router)
    app.include_router(chat.router)
    app.include_router(episodes.router)
    app.include_router(state.router)
    app.include_router(relationship.router)
    app.include_router(observe.router)
    return app


app = create_app()
