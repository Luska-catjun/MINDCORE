from __future__ import annotations

from unittest import TestCase

from starlette.requests import Request

from app.config import Settings
from app.routers.auth import SESSION_COOKIE_NAME, create_session_token, request_is_authenticated


class AuthCredentialResolutionTests(TestCase):
    def setUp(self) -> None:
        self.settings = Settings(
            _env_file=None,
            private_access_password="test-private-access-password",
            auth_signing_secret="test-auth-signing-secret",
        )
        self.valid_session = create_session_token(self.settings, max_age_seconds=60)

    @staticmethod
    def _request(*, cookie: str = "", bearer: str = "") -> Request:
        headers: list[tuple[bytes, bytes]] = []
        if cookie:
            headers.append((b"cookie", f"{SESSION_COOKIE_NAME}={cookie}".encode("ascii")))
        if bearer:
            headers.append((b"authorization", f"Bearer {bearer}".encode("ascii")))
        return Request({"type": "http", "method": "GET", "path": "/auth/me", "headers": headers})

    def test_valid_cookie_only_authenticates(self) -> None:
        self.assertEqual(
            request_is_authenticated(self.settings, self._request(cookie=self.valid_session)),
            (True, "cookie"),
        )

    def test_valid_bearer_only_authenticates(self) -> None:
        self.assertEqual(
            request_is_authenticated(self.settings, self._request(bearer=self.valid_session)),
            (True, "bearer"),
        )

    def test_stale_cookie_does_not_shadow_valid_bearer(self) -> None:
        self.assertEqual(
            request_is_authenticated(
                self.settings,
                self._request(cookie="stale-cookie", bearer=self.valid_session),
            ),
            (True, "bearer"),
        )

    def test_valid_cookie_is_not_broken_by_stale_bearer(self) -> None:
        self.assertEqual(
            request_is_authenticated(
                self.settings,
                self._request(cookie=self.valid_session, bearer="stale-bearer"),
            ),
            (True, "cookie"),
        )

    def test_both_invalid_credentials_are_rejected(self) -> None:
        self.assertEqual(
            request_is_authenticated(
                self.settings,
                self._request(cookie="stale-cookie", bearer="stale-bearer"),
            ),
            (False, "bearer"),
        )
