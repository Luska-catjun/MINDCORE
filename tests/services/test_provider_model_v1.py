from __future__ import annotations

import json
import os
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, Mock, patch

from pydantic import ValidationError

from app.config import Settings
from app.services import anthropic, gemini, llm, responses_api, rest_llm
from app.services.llm_errors import LLMError
from app.services.prompt_loader import PromptLoadError
from app.services.runtime_diagnostics import snapshot


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


class ProviderConfigurationTests(TestCase):
    def test_provider_and_model_are_distinct_validated_settings(self) -> None:
        settings = Settings(_env_file=None, llm_provider="openai", openai_model="future-custom-model-1")
        self.assertEqual(settings.llm_provider, "openai")
        self.assertEqual(settings.openai_model, "future-custom-model-1")
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, llm_provider="gemini-foo")
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, openai_model="bad\nmodel")

    def test_provider_defaults_are_current_and_centralized_in_settings(self) -> None:
        settings = Settings(_env_file=None)
        self.assertEqual(settings.gemini_model, "gemini-3.5-flash-lite")
        self.assertEqual(settings.groq_model, "qwen/qwen3.6-27b")
        self.assertEqual(settings.anthropic_model, "claude-sonnet-5")
        self.assertEqual(settings.xai_model, "grok-4.6")
        self.assertEqual(settings.openai_model, "gpt-5.6-luna")

    def test_legacy_env_defaults_missing_models_and_manual_models_survive_reload(self) -> None:
        with TemporaryDirectory() as directory:
            config = Path(directory) / "mindcore.env"
            config.write_text("LLM_PROVIDER=openai\nOPENAI_API_KEY=test-key\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                settings = Settings(_env_file=config)
            self.assertEqual(settings.openai_model, "gpt-5.6-luna")

            config.write_text(
                "LLM_PROVIDER=openai\nOPENAI_API_KEY=test-key\nOPENAI_MODEL=future-model\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                restarted = Settings(_env_file=config)
            self.assertEqual(restarted.llm_provider, "openai")
            self.assertEqual(restarted.openai_model, "future-model")

    def test_anthropic_request_shape_preserves_identity_and_dynamic_context(self) -> None:
        payload = anthropic.build_anthropic_payload("hello", "IDENTITY", dynamic_context="CONTEXT")
        self.assertEqual(payload["system"], "IDENTITY")
        self.assertIn("CONTEXT", payload["messages"][0]["content"])
        self.assertIn("hello", payload["messages"][0]["content"])

    def test_responses_request_shape_is_stateless_and_preserves_context(self) -> None:
        payload = responses_api.build_responses_payload("hello", "IDENTITY", dynamic_context="CONTEXT")
        self.assertEqual(payload["instructions"], "IDENTITY")
        self.assertIn("CONTEXT", payload["input"])
        self.assertIn("hello", payload["input"])
        self.assertIs(payload["store"], False)

    def test_gemini_custom_model_reaches_request_url(self) -> None:
        settings = Settings(_env_file=None, gemini_api_key="secret", gemini_model="gemini-custom-future", gemini_max_retries=0)
        payload = gemini.build_generate_content_payload("hello", "identity")
        response = {"candidates": [{"content": {"parts": [{"text": "ok"}]}}], "usageMetadata": {}}
        with patch("app.services.gemini.urlopen", return_value=_Response(response)) as request:
            self.assertEqual(gemini._request_gemini_sync(settings, payload, "main"), "ok")
        self.assertIn("/models/gemini-custom-future:generateContent", request.call_args.args[0].full_url)

    def test_error_categories_do_not_expose_api_keys(self) -> None:
        error = LLMError("request failed", category="authentication", model="custom-model", api_base_url="https://example.test/v1")
        self.assertNotIn("api_key", error.safe_detail())
        self.assertNotIn("token", error.safe_detail())

    def test_rest_provider_http_errors_are_normalized_without_response_body_leakage(self) -> None:
        secret = "provider-secret-value"
        response = HTTPError(
            "https://api.example.test/v1/responses",
            401,
            "unauthorized",
            {},
            BytesIO(f"invalid token {secret}".encode()),
        )
        with patch("app.services.rest_llm.urlopen", side_effect=response):
            with self.assertLogs("diana.rest_llm", level="ERROR") as logs:
                with self.assertRaises(LLMError) as caught:
                    rest_llm.request_json(
                        provider="openai",
                        model="gpt-test",
                        api_base_url="https://api.example.test/v1",
                        path="responses",
                        api_key=secret,
                        headers={"Authorization": f"Bearer {secret}"},
                        payload={"input": "test"},
                        timeout=1,
                        max_retries=0,
                        request_kind="main",
                    )
        self.assertEqual(caught.exception.category, "authentication")
        self.assertNotIn(secret, caught.exception.message)
        self.assertNotIn(secret, "\n".join(logs.output))


class ProviderRoutingTests(IsolatedAsyncioTestCase):
    async def test_each_provider_routes_main_and_memory_to_the_same_adapter(self) -> None:
        for provider in sorted(llm.SUPPORTED_PROVIDERS):
            with self.subTest(provider=provider):
                main = AsyncMock(return_value="main")
                memory = AsyncMock(return_value='{"memory": "fact"}')
                with patch(f"app.services.{provider}.generate_reply", main), patch(f"app.services.{provider}.generate_memory_candidate", memory):
                    settings = Settings(_env_file=None, llm_provider=provider, llm_fallback_provider=None)
                    self.assertEqual(await llm.generate_reply(settings, "hello", identity_prompt="IDENTITY", dynamic_context="CONTEXT"), "main")
                    self.assertEqual(await llm.generate_memory_candidate(settings, "user", "persona"), '{"memory": "fact"}')
                main.assert_awaited_once()
                memory.assert_awaited_once()

    async def test_cross_provider_fallback_uses_configured_fallback(self) -> None:
        settings = Settings(_env_file=None, llm_provider="openai", llm_fallback_provider="gemini")

        async def generate(provider: str, *_args, **_kwargs) -> str:
            if provider == "openai":
                raise LLMError("temporary", category="timeout")
            return "fallback"

        with patch("app.services.llm._generate_with_provider", new=Mock(side_effect=generate)):
            self.assertEqual(await llm.generate_reply(settings, "hello"), "fallback")

    async def test_model_or_configuration_errors_do_not_trigger_fallback(self) -> None:
        settings = Settings(_env_file=None, llm_provider="anthropic", llm_fallback_provider="groq")
        generate = AsyncMock(side_effect=LLMError("bad model", category="model"))
        with patch("app.services.llm._generate_with_provider", generate):
            with self.assertRaises(LLMError):
                await llm.generate_reply(settings, "hello")
        generate.assert_awaited_once()

    async def test_anthropic_and_responses_adapters_use_configured_models_and_usage(self) -> None:
        anthropic_response = {"content": [{"type": "text", "text": "ok"}], "usage": {"input_tokens": 2, "output_tokens": 1}}
        with patch("app.services.anthropic.request_json", return_value=(anthropic_response, 1.0)) as request:
            result = await anthropic.generate_reply(
                Settings(_env_file=None, anthropic_api_key="secret", anthropic_model="claude-custom"),
                "hello", identity_prompt="IDENTITY",
            )
        self.assertEqual(result, "ok")
        self.assertEqual(request.call_args.kwargs["payload"]["model"], "claude-custom")
        self.assertEqual(request.call_args.kwargs["headers"]["anthropic-version"], "2023-06-01")
        self.assertEqual(snapshot()["llm_calls"][0]["model"], "claude-custom")

        response = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}], "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3}}
        for provider in ("openai", "xai"):
            settings = Settings(_env_file=None, **{f"{provider}_api_key": "secret", f"{provider}_model": f"{provider}-custom"})
            with patch("app.services.responses_api.request_json", return_value=(response, 1.0)) as request:
                self.assertEqual(await responses_api.generate_reply_for(provider, settings, "hello", identity_prompt="IDENTITY"), "ok")
            self.assertEqual(request.call_args.kwargs["payload"]["model"], f"{provider}-custom")
            self.assertIs(request.call_args.kwargs["payload"]["store"], False)
            self.assertEqual(snapshot()["llm_calls"][0]["provider"], provider)

    async def test_new_provider_memory_extraction_uses_the_selected_provider_contract(self) -> None:
        anthropic_response = {"content": [{"type": "text", "text": '{"memory":"fact"}'}], "usage": {}}
        settings = Settings(_env_file=None, anthropic_api_key="secret", anthropic_model="claude-memory")
        with patch("app.services.anthropic.request_json", return_value=(anthropic_response, 1.0)) as request:
            self.assertEqual(await anthropic.generate_memory_candidate(settings, "user", "persona"), '{"memory":"fact"}')
        payload = request.call_args.kwargs["payload"]
        self.assertEqual(payload["model"], "claude-memory")
        self.assertEqual(payload["temperature"], 0)
        self.assertIn("USER MESSAGE", payload["messages"][0]["content"])

        response = {"output": [{"type": "message", "content": [{"type": "output_text", "text": '{"memory":"fact"}'}]}], "usage": {}}
        for provider in ("openai", "xai"):
            settings = Settings(_env_file=None, **{f"{provider}_api_key": "secret", f"{provider}_model": f"{provider}-memory"})
            with patch("app.services.responses_api.request_json", return_value=(response, 1.0)) as request:
                self.assertEqual(await responses_api.generate_memory_candidate_for(provider, settings, "user", "persona"), '{"memory":"fact"}')
            payload = request.call_args.kwargs["payload"]
            self.assertEqual(payload["model"], f"{provider}-memory")
            self.assertEqual(payload["max_output_tokens"], 256)
            self.assertIs(payload["store"], False)

    async def test_new_provider_memory_prompt_errors_use_the_common_llm_contract(self) -> None:
        settings = Settings(_env_file=None, anthropic_model="claude-memory")
        with patch(
            "app.services.anthropic.load_memory_extraction_prompt",
            side_effect=PromptLoadError("missing prompt"),
        ):
            with self.assertRaises(LLMError) as caught:
                await anthropic.generate_memory_candidate(settings, "user", "persona")
        self.assertEqual(caught.exception.category, "prompt_configuration")
        self.assertEqual(caught.exception.model, "claude-memory")

        settings = Settings(_env_file=None, openai_model="gpt-memory")
        with patch(
            "app.services.responses_api.load_memory_extraction_prompt",
            side_effect=PromptLoadError("missing prompt"),
        ):
            with self.assertRaises(LLMError) as caught:
                await responses_api.generate_memory_candidate_for("openai", settings, "user", "persona")
        self.assertEqual(caught.exception.category, "prompt_configuration")
        self.assertEqual(caught.exception.model, "gpt-memory")
