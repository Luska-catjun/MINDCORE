import logging
import time
from typing import Any

from app.config import Settings
from app.services.llm_errors import LLMError
from app.services.prompt_loader import (
    PromptLoadError,
    load_persona_identity_prompt,
    load_memory_extraction_prompt,
)
from app.services.runtime_diagnostics import record_llm_call

logger = logging.getLogger("diana.groq")


class GroqError(LLMError):
    pass


def build_groq_messages(
    user_message: str,
    system_instruction: str,
    *,
    dynamic_context: str | None = None,
) -> list[dict[str, str]]:
    message_text = user_message
    if dynamic_context:
        message_text = f"{dynamic_context}\n\n[CURRENT USER MESSAGE]\n{user_message}"
    return [
        {"role": "system", "content": system_instruction},
        {"role": "user", "content": message_text},
    ]


def _create_client(settings: Settings) -> Any:
    if not settings.groq_api_key:
        raise GroqError(
            "GROQ_API_KEY is not configured.",
            category="configuration",
            model=settings.groq_model,
            api_base_url=settings.groq_api_base_url,
        )

    try:
        from groq import AsyncGroq
    except ImportError as exc:
        raise GroqError(
            "Groq Python SDK is not installed. Install dependencies from requirements.txt.",
            category="configuration",
            model=settings.groq_model,
            api_base_url=settings.groq_api_base_url,
        ) from exc

    return AsyncGroq(
        api_key=settings.groq_api_key,
        timeout=settings.groq_timeout_seconds,
        max_retries=settings.groq_max_retries,
    )


def _classify_error(error: Exception) -> tuple[str, int | None]:
    status_code = getattr(error, "status_code", None)
    if status_code in {401, 403}:
        return "authentication", status_code
    if status_code == 404:
        return "model_or_api_version", status_code
    if status_code == 429:
        return "quota_or_rate_limit", status_code
    if status_code is not None and status_code >= 500:
        return "groq_server", status_code
    if status_code is not None:
        return "groq_http", status_code
    return "network", None


async def _create_completion(
    settings: Settings,
    messages: list[dict[str, str]],
    *,
    temperature: float,
    max_completion_tokens: int,
    response_format: dict[str, str] | None = None,
    request_kind: str = "main",
) -> str:
    client = _create_client(settings)
    started_at = time.perf_counter()
    request_options: dict[str, Any] = {
        "model": settings.groq_model,
        "messages": messages,
        "temperature": temperature,
        "max_completion_tokens": max_completion_tokens,
        "reasoning_effort": "none",
        "reasoning_format": "hidden",
    }
    if response_format is not None:
        request_options["response_format"] = response_format

    try:
        completion = await client.chat.completions.create(**request_options)
    except Exception as exc:
        category, status_code = _classify_error(exc)
        record_llm_call(request_kind=request_kind, provider="groq", model=settings.groq_model,
                        input_tokens=None, output_tokens=None, total_tokens=None,
                        latency_ms=(time.perf_counter() - started_at) * 1000, success=False,
                        error_category=category)
        logger.error(
            "Groq API error category=%s status=%s model=%s api_base_url=%s error_type=%s error=%s",
            category,
            status_code,
            settings.groq_model,
            settings.groq_api_base_url,
            type(exc).__name__,
            str(exc),
        )
        raise GroqError(
            "Groq API request failed." if status_code else "Groq API connection failed.",
            category=category,
            status_code=status_code,
            model=settings.groq_model,
            api_base_url=settings.groq_api_base_url,
        ) from exc
    finally:
        await client.close()

    usage = completion.usage
    latency_ms = (time.perf_counter() - started_at) * 1000
    record_llm_call(request_kind=request_kind, provider="groq", model=settings.groq_model,
                    input_tokens=getattr(usage, "prompt_tokens", None),
                    output_tokens=getattr(usage, "completion_tokens", None),
                    total_tokens=getattr(usage, "total_tokens", None), latency_ms=latency_ms,
                    success=True)
    logger.info(
        "LLM usage provider=groq model=%s request_kind=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s latency_ms=%.1f",
        settings.groq_model,
        request_kind,
        getattr(usage, "prompt_tokens", None),
        getattr(usage, "completion_tokens", None),
        getattr(usage, "total_tokens", None),
        latency_ms,
    )

    content = completion.choices[0].message.content if completion.choices else None
    if not content or not content.strip():
        raise GroqError(
            "Groq returned an empty response.",
            category="empty_response",
            model=settings.groq_model,
            api_base_url=settings.groq_api_base_url,
        )
    return content.strip()


async def generate_reply(
    settings: Settings,
    user_message: str,
    *,
    dynamic_context: str | None = None,
    identity_prompt: str | None = None,
) -> str:
    if identity_prompt is None:
        try:
            identity_prompt = load_persona_identity_prompt(settings)
        except PromptLoadError as exc:
            raise GroqError(
                str(exc),
                category="prompt_configuration",
                model=settings.groq_model,
                api_base_url=settings.groq_api_base_url,
            ) from exc

    return await _create_completion(
        settings,
        build_groq_messages(
            user_message,
            identity_prompt,
            dynamic_context=dynamic_context,
        ),
        temperature=0.7,
        max_completion_tokens=1024,
        request_kind="main",
    )


async def generate_memory_candidate(settings: Settings, user_content: str, diana_content: str) -> str:
    try:
        system_instruction = load_memory_extraction_prompt()
    except PromptLoadError as exc:
        raise GroqError(
            str(exc),
            category="prompt_configuration",
            model=settings.groq_model,
            api_base_url=settings.groq_api_base_url,
        ) from exc

    return await _create_completion(
        settings,
        build_groq_messages(
            f"[USER MESSAGE]\n{user_content}\n\n[PERSONA RESPONSE]\n{diana_content}",
            system_instruction,
        ),
        temperature=0,
        max_completion_tokens=256,
        response_format={"type": "json_object"},
        request_kind="memory_extraction",
    )
