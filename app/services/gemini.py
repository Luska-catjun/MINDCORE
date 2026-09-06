import asyncio
import json
import logging
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from app.config import Settings
from app.services.llm_errors import LLMError
from app.services.prompt_loader import (
    PromptLoadError,
    load_persona_identity_prompt,
    load_memory_extraction_prompt,
)
from app.services.runtime_diagnostics import record_llm_call

logger = logging.getLogger("diana.gemini")


class GeminiError(LLMError):
    pass


def _sanitize_for_log(text: str, settings: Settings) -> str:
    sanitized = text
    if settings.gemini_api_key:
        sanitized = sanitized.replace(settings.gemini_api_key, "[REDACTED_GEMINI_API_KEY]")
    return sanitized


def _classify_http_error(status_code: int, body: str) -> str:
    lowered = body.lower()
    if status_code in {401, 403}:
        return "authentication"
    if status_code == 404:
        return "model_or_api_version"
    if status_code == 400:
        if "api key" in lowered or "key" in lowered:
            return "authentication"
        if "model" in lowered:
            return "model"
        return "request_json"
    if status_code == 429:
        return "quota_or_rate_limit"
    if status_code == 408:
        return "timeout"
    if status_code >= 500:
        return "gemini_server"
    return "gemini_http"


def _extract_text(response: dict[str, Any]) -> str:
    parts: list[str] = []
    for candidate in response.get("candidates", []):
        content = candidate.get("content") or {}
        for part in content.get("parts", []):
            text = part.get("text")
            if text:
                parts.append(text)

    text = "\n".join(parts).strip()
    if not text:
        raise GeminiError("Gemini returned an empty response.", category="empty_response")
    return text


def build_generate_content_payload(
    user_message: str,
    system_instruction: str,
    *,
    dynamic_context: str | None = None,
    thinking_level: str | None = None,
) -> dict[str, Any]:
    message_text = user_message
    if dynamic_context:
        message_text = f"{dynamic_context}\n\n[CURRENT USER MESSAGE]\n{user_message}"
    generation_config: dict[str, Any] = {
        "temperature": 0.7,
        "maxOutputTokens": 1024,
    }
    if thinking_level:
        generation_config["thinkingConfig"] = {"thinkingLevel": thinking_level}

    return {
        "systemInstruction": {
            "parts": [{"text": system_instruction}],
        },
        "contents": [
            {
                "role": "user",
                "parts": [{"text": message_text}],
            }
        ],
        "generationConfig": generation_config,
    }


def _request_gemini_sync(settings: Settings, payload: dict[str, Any], request_kind: str) -> str:
    if not settings.gemini_api_key:
        raise GeminiError(
            "GEMINI_API_KEY is not configured.",
            category="configuration",
            model=settings.gemini_model,
            api_base_url=settings.gemini_api_base_url,
        )

    model = quote(settings.gemini_model, safe="")
    url = f"{settings.gemini_api_base_url}/models/{model}:generateContent?key={settings.gemini_api_key}"
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    started_at = time.perf_counter()
    for attempt in range(settings.gemini_max_retries + 1):
        try:
            with urlopen(request, timeout=settings.gemini_timeout_seconds) as response:
                body = response.read().decode("utf-8")
            break
        except HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            safe_body = _sanitize_for_log(error_body, settings)
            category = _classify_http_error(exc.code, safe_body)
            if category == "gemini_server" and attempt < settings.gemini_max_retries:
                logger.warning(
                    "Gemini API retry provider=gemini request_kind=%s attempt=%s category=%s status=%s model=%s",
                    request_kind,
                    attempt + 1,
                    category,
                    exc.code,
                    settings.gemini_model,
                )
                time.sleep(0.25 * (attempt + 1))
                continue
            logger.error(
                "Gemini API HTTP error status=%s category=%s model=%s api_base_url=%s response_body=%s",
                exc.code,
                category,
                settings.gemini_model,
                settings.gemini_api_base_url,
                safe_body,
            )
            raise GeminiError(
                f"Gemini API failed with HTTP {exc.code}.",
                category=category,
                status_code=exc.code,
                model=settings.gemini_model,
                api_base_url=settings.gemini_api_base_url,
            ) from exc
        except TimeoutError as exc:
            if attempt < settings.gemini_max_retries:
                logger.warning(
                    "Gemini API retry provider=gemini request_kind=%s attempt=%s category=timeout model=%s",
                    request_kind,
                    attempt + 1,
                    settings.gemini_model,
                )
                time.sleep(0.25 * (attempt + 1))
                continue
            logger.error(
                "Gemini API timeout category=timeout model=%s api_base_url=%s",
                settings.gemini_model,
                settings.gemini_api_base_url,
            )
            raise GeminiError(
                "Gemini API request timed out.",
                category="timeout",
                model=settings.gemini_model,
                api_base_url=settings.gemini_api_base_url,
            ) from exc
        except URLError as exc:
            safe_reason = _sanitize_for_log(str(exc.reason), settings)
            if attempt < settings.gemini_max_retries:
                logger.warning(
                    "Gemini API retry provider=gemini request_kind=%s attempt=%s category=network model=%s error=%s",
                    request_kind,
                    attempt + 1,
                    settings.gemini_model,
                    safe_reason,
                )
                time.sleep(0.25 * (attempt + 1))
                continue
            logger.error(
                "Gemini API connection error category=network model=%s api_base_url=%s error=%s",
                settings.gemini_model,
                settings.gemini_api_base_url,
                safe_reason,
            )
            raise GeminiError(
                f"Gemini API connection failed: {safe_reason}",
                category="network",
                model=settings.gemini_model,
                api_base_url=settings.gemini_api_base_url,
            ) from exc
    else:  # pragma: no cover - the loop exits by break or exception.
        raise AssertionError("Gemini request retry loop ended unexpectedly.")

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        logger.error(
            "Gemini API returned invalid JSON category=response_json model=%s api_base_url=%s response_body=%s",
            settings.gemini_model,
            settings.gemini_api_base_url,
            _sanitize_for_log(body, settings),
        )
        raise GeminiError(
            "Gemini API returned invalid JSON.",
            category="response_json",
            model=settings.gemini_model,
            api_base_url=settings.gemini_api_base_url,
        ) from exc

    usage = parsed.get("usageMetadata") or {}
    latency_ms = (time.perf_counter() - started_at) * 1000
    record_llm_call(request_kind=request_kind, provider="gemini", model=settings.gemini_model,
                    input_tokens=usage.get("promptTokenCount"), output_tokens=usage.get("candidatesTokenCount"),
                    total_tokens=usage.get("totalTokenCount"), latency_ms=latency_ms, success=True)
    logger.info(
        "LLM usage provider=gemini model=%s request_kind=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s latency_ms=%.1f",
        settings.gemini_model,
        request_kind,
        usage.get("promptTokenCount"),
        usage.get("candidatesTokenCount"),
        usage.get("totalTokenCount"),
        latency_ms,
    )
    return _extract_text(parsed)


def _call_gemini_sync(
    settings: Settings,
    prompt: str,
    dynamic_context: str | None = None,
    identity_prompt: str | None = None,
) -> str:
    if identity_prompt is None:
        try:
            identity_prompt = load_persona_identity_prompt(settings)
        except PromptLoadError as exc:
            raise GeminiError(
                str(exc),
                category="prompt_configuration",
                model=settings.gemini_model,
                api_base_url=settings.gemini_api_base_url,
            ) from exc

    payload = build_generate_content_payload(
        prompt,
        identity_prompt,
        dynamic_context=dynamic_context,
        thinking_level=settings.gemini_thinking_level,
    )
    return _request_gemini_sync(settings, payload, "main")


def _extract_memory_candidate_sync(settings: Settings, user_content: str, diana_content: str) -> str:
    try:
        system_instruction = load_memory_extraction_prompt()
    except PromptLoadError as exc:
        raise GeminiError(
            str(exc),
            category="prompt_configuration",
            model=settings.gemini_model,
            api_base_url=settings.gemini_api_base_url,
        ) from exc

    payload = build_generate_content_payload(
        f"[USER MESSAGE]\n{user_content}\n\n[PERSONA RESPONSE]\n{diana_content}",
        system_instruction,
    )
    payload["generationConfig"] = {
        "temperature": 0,
        "maxOutputTokens": 256,
        "responseMimeType": "application/json",
        "thinkingConfig": {"thinkingLevel": settings.gemini_thinking_level},
    }
    return _request_gemini_sync(settings, payload, "memory_extraction")


async def generate_reply(
    settings: Settings,
    user_message: str,
    *,
    dynamic_context: str | None = None,
    identity_prompt: str | None = None,
) -> str:
    return await asyncio.to_thread(
        _call_gemini_sync, settings, user_message, dynamic_context, identity_prompt
    )


async def generate_memory_candidate(settings: Settings, user_content: str, diana_content: str) -> str:
    return await asyncio.to_thread(_extract_memory_candidate_sync, settings, user_content, diana_content)
