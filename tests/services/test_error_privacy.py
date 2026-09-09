from __future__ import annotations

import json
from io import BytesIO
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.error import HTTPError, URLError
from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import Settings
from app.database.connection import check_database
from app.main import create_app
from app.services import gemini, groq, rest_llm
from app.services.error_safety import safe_database_diagnostic, safe_url_metadata
from app.services.llm_errors import LLMError


SECRET_API_KEY = "SECRET_API_KEY_123"
SECRET_DB_TOKEN = "SECRET_DB_TOKEN_456"
SECRET_USER_PROMPT = "SECRET_USER_PROMPT_789"
SECRET_DB_URL = f"libsql://user-secret-db.example?authToken={SECRET_DB_TOKEN}"


class _Response:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return self.body


class _FailingAcquire:
    async def __aenter__(self):
        raise RuntimeError(f"connection failed {SECRET_DB_URL} token={SECRET_DB_TOKEN}")

    async def __aexit__(self, *_args):
        return None


class _FailingPool:
    def acquire(self):
        return _FailingAcquire()


class ErrorPrivacyTests(TestCase):
    def test_safe_url_metadata_never_returns_url_components_beyond_scheme_and_presence(self) -> None:
        self.assertEqual(safe_url_metadata(SECRET_DB_URL), ("libsql", True, True))
        diagnostic = safe_database_diagnostic(RuntimeError(SECRET_DB_URL), SECRET_DB_URL)
        serialized = repr(diagnostic)
        self.assertNotIn(SECRET_DB_TOKEN, serialized)
        self.assertNotIn("user-secret-db.example", serialized)

    def test_gemini_http_error_does_not_log_upstream_body_prompt_key_or_custom_url(self) -> None:
        upstream_body = json.dumps({
            "error": f"key={SECRET_API_KEY} prompt={SECRET_USER_PROMPT} db={SECRET_DB_URL}",
        }).encode()
        response = HTTPError(
            f"https://provider.example/v1?credential={SECRET_API_KEY}",
            401,
            "unauthorized",
            {},
            BytesIO(upstream_body),
        )
        settings = Settings(
            _env_file=None,
            gemini_api_key=SECRET_API_KEY,
            gemini_api_base_url=f"https://provider.example/v1?credential={SECRET_DB_TOKEN}",
            gemini_max_retries=0,
        )
        with patch("app.services.gemini.urlopen", side_effect=response):
            with self.assertLogs("diana.gemini", level="ERROR") as captured:
                with self.assertRaises(LLMError) as caught:
                    gemini._request_gemini_sync(settings, {"prompt": SECRET_USER_PROMPT}, "main")

        output = "\n".join(captured.output) + repr(caught.exception.safe_detail())
        for secret in (SECRET_API_KEY, SECRET_DB_TOKEN, SECRET_USER_PROMPT, SECRET_DB_URL, "provider.example"):
            self.assertNotIn(secret, output)
        self.assertEqual(caught.exception.safe_detail()["category"], "authentication")
        self.assertNotIn("api_base_url", caught.exception.safe_detail())
        self.assertIsNone(caught.exception.__cause__)

    def test_gemini_invalid_json_and_network_reason_are_not_logged(self) -> None:
        settings = Settings(
            _env_file=None,
            gemini_api_key=SECRET_API_KEY,
            gemini_api_base_url=f"https://provider.example/v1?credential={SECRET_DB_TOKEN}",
            gemini_max_retries=0,
        )
        with patch("app.services.gemini.urlopen", return_value=_Response(SECRET_USER_PROMPT.encode())):
            with self.assertLogs("diana.gemini", level="ERROR") as captured:
                with self.assertRaises(LLMError):
                    gemini._request_gemini_sync(settings, {}, "main")
        self.assertNotIn(SECRET_USER_PROMPT, "\n".join(captured.output))
        self.assertNotIn("provider.example", "\n".join(captured.output))

        with patch(
            "app.services.gemini.urlopen",
            side_effect=URLError(f"network echoed {SECRET_API_KEY} {SECRET_USER_PROMPT}"),
        ):
            with self.assertLogs("diana.gemini", level="ERROR") as captured:
                with self.assertRaises(LLMError) as caught:
                    gemini._request_gemini_sync(settings, {}, "main")
        output = "\n".join(captured.output) + repr(caught.exception.safe_detail())
        self.assertNotIn(SECRET_API_KEY, output)
        self.assertNotIn(SECRET_USER_PROMPT, output)

    def test_rest_provider_errors_are_secret_safe_for_anthropic_xai_and_openai(self) -> None:
        for provider in ("anthropic", "xai", "openai"):
            with self.subTest(provider=provider):
                response = HTTPError(
                    f"https://{provider}.example/v1?key={SECRET_API_KEY}",
                    401,
                    "unauthorized",
                    {},
                    BytesIO(f"echo {SECRET_USER_PROMPT} {SECRET_API_KEY}".encode()),
                )
                with patch("app.services.rest_llm.urlopen", side_effect=response):
                    with self.assertLogs("diana.rest_llm", level="ERROR") as captured:
                        with self.assertRaises(LLMError) as caught:
                            rest_llm.request_json(
                                provider=provider,
                                model=f"{provider}-model",
                                api_base_url=f"https://{provider}.example/v1?key={SECRET_DB_TOKEN}",
                                path="messages",
                                api_key=SECRET_API_KEY,
                                headers={"Authorization": f"Bearer {SECRET_API_KEY}"},
                                payload={"input": SECRET_USER_PROMPT},
                                timeout=1,
                                max_retries=0,
                                request_kind="main",
                            )
                output = "\n".join(captured.output) + repr(caught.exception.safe_detail())
                for secret in (SECRET_API_KEY, SECRET_DB_TOKEN, SECRET_USER_PROMPT):
                    self.assertNotIn(secret, output)
                self.assertNotIn("api_base_url", caught.exception.safe_detail())
                self.assertIsNone(caught.exception.__cause__)

    def test_chat_http_error_detail_is_category_based_and_never_returns_raw_error(self) -> None:
        settings = Settings(
            _env_file=None,
            private_access_password="test-password",
            auth_signing_secret="test-signing-secret",
        )
        app = create_app(settings_override=settings, db_pool_factory=lambda _settings: object())
        raw = f"upstream echoed {SECRET_API_KEY} {SECRET_USER_PROMPT} {SECRET_DB_URL}"
        with TestClient(app) as client:
            self.assertEqual(client.post("/auth/login", json={"password": "test-password"}).status_code, 200)
            with patch(
                "app.routers.chat.ChatTurnCoordinator.execute",
                new=AsyncMock(side_effect=LLMError(
                    raw,
                    category="authentication",
                    status_code=401,
                    model="provider-model",
                    api_base_url=SECRET_DB_URL,
                )),
            ):
                response = client.post(
                    "/chat",
                    json={
                        "conversation_id": str(uuid4()),
                        "role": "user",
                        "content": SECRET_USER_PROMPT,
                    },
                )

        self.assertEqual(response.status_code, 503)
        serialized = response.text
        for secret in (SECRET_API_KEY, SECRET_DB_TOKEN, SECRET_USER_PROMPT, SECRET_DB_URL):
            self.assertNotIn(secret, serialized)
        self.assertEqual(response.json()["detail"]["category"], "authentication")
        self.assertEqual(response.json()["detail"]["message"], "Provider authentication failed.")

    def test_unhandled_runtime_database_error_has_safe_log_and_http_detail(self) -> None:
        settings = Settings(
            _env_file=None,
            private_access_password="test-password",
            auth_signing_secret="test-signing-secret",
        )
        app = create_app(settings_override=settings, db_pool_factory=lambda _settings: object())

        @app.get("/_privacy_test/runtime_db")
        async def runtime_db_failure():
            raise RuntimeError(f"driver failure url={SECRET_DB_URL} prompt={SECRET_USER_PROMPT}")

        with TestClient(app, raise_server_exceptions=False) as client:
            self.assertEqual(client.post("/auth/login", json={"password": "test-password"}).status_code, 200)
            with self.assertLogs("diana.runtime", level="ERROR") as captured:
                response = client.get("/_privacy_test/runtime_db")

        self.assertEqual(response.status_code, 500)
        output = "\n".join(captured.output) + response.text
        for secret in (SECRET_DB_TOKEN, SECRET_USER_PROMPT, SECRET_DB_URL):
            self.assertNotIn(secret, output)
        self.assertEqual(response.json()["detail"]["code"], "INTERNAL_ERROR")


class AsyncErrorPrivacyTests(IsolatedAsyncioTestCase):
    async def test_groq_sdk_exception_repr_is_not_logged_or_returned(self) -> None:
        settings = Settings(
            _env_file=None,
            groq_api_key=SECRET_API_KEY,
            groq_api_base_url=f"https://groq.example/v1?credential={SECRET_DB_TOKEN}",
        )
        client = MagicMock()
        client.chat.completions.create = AsyncMock(
            side_effect=RuntimeError(f"SDK body={SECRET_USER_PROMPT} token={SECRET_API_KEY}")
        )
        client.close = AsyncMock()
        with patch("app.services.groq._create_client", return_value=client):
            with self.assertLogs("diana.groq", level="ERROR") as captured:
                with self.assertRaises(LLMError) as caught:
                    await groq._create_completion(
                        settings,
                        [{"role": "user", "content": SECRET_USER_PROMPT}],
                        temperature=0,
                        max_completion_tokens=1,
                    )
        output = "\n".join(captured.output) + repr(caught.exception.safe_detail())
        for secret in (SECRET_API_KEY, SECRET_DB_TOKEN, SECRET_USER_PROMPT, "groq.example"):
            self.assertNotIn(secret, output)
        self.assertIsNone(caught.exception.__cause__)
        client.close.assert_awaited_once()

    async def test_database_health_log_contains_only_safe_metadata(self) -> None:
        with self.assertLogs("diana.database", level="ERROR") as captured:
            result = await check_database(_FailingPool(), database_url=SECRET_DB_URL)
        self.assertEqual(result, "error")
        output = "\n".join(captured.output)
        self.assertIn("database_url_scheme=libsql", output)
        self.assertIn("database_host_present=true", output)
        self.assertNotIn(SECRET_DB_TOKEN, output)
        self.assertNotIn("user-secret-db.example", output)
