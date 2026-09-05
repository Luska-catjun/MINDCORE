import logging

from app.config import Settings
from app.services.llm_errors import LLMError
from app.services.runtime_diagnostics import record_fallback_event

SUPPORTED_PROVIDERS = {"gemini", "groq"}
FALLBACK_CATEGORIES = {
    "quota_or_rate_limit",
    "timeout",
    "network",
    "gemini_server",
    "groq_server",
}

logger = logging.getLogger("diana.llm")


def _provider(settings: Settings) -> str:
    provider = settings.llm_provider.strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise LLMError(
            f"Unsupported LLM_PROVIDER: {provider}.",
            category="configuration",
        )
    return provider


def _fallback_provider(settings: Settings, primary: str) -> str | None:
    configured = settings.llm_fallback_provider
    if not configured:
        return None

    provider = configured.strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise LLMError(
            f"Unsupported LLM_FALLBACK_PROVIDER: {provider}.",
            category="configuration",
        )
    return None if provider == primary else provider


async def _generate_with_provider(
    provider: str,
    settings: Settings,
    request_kind: str,
    *args: str,
    dynamic_context: str | None = None,
) -> str:
    if provider == "groq":
        from app.services import groq

        if request_kind == "main":
            return await groq.generate_reply(settings, args[0], dynamic_context=dynamic_context)
        return await groq.generate_memory_candidate(settings, args[0], args[1])

    from app.services import gemini

    if request_kind == "main":
        return await gemini.generate_reply(settings, args[0], dynamic_context=dynamic_context)
    return await gemini.generate_memory_candidate(settings, args[0], args[1])


async def _generate_with_fallback(
    settings: Settings,
    request_kind: str,
    *args: str,
    dynamic_context: str | None = None,
) -> str:
    primary = _provider(settings)
    fallback = _fallback_provider(settings, primary)
    try:
        response = await _generate_with_provider(
            primary,
            settings,
            request_kind,
            *args,
            dynamic_context=dynamic_context,
        )
        logger.info("LLM completed provider=%s request_kind=%s fallback_used=false", primary, request_kind)
        return response
    except LLMError as exc:
        if not fallback or exc.category not in FALLBACK_CATEGORIES:
            logger.warning(
                "LLM failed provider=%s request_kind=%s category=%s status=%s fallback_used=false",
                primary,
                request_kind,
                exc.category,
                exc.status_code,
            )
            raise

        record_fallback_event(primary=primary, fallback=fallback, error_category=exc.category)

        logger.warning(
            "LLM primary failed provider=%s request_kind=%s category=%s status=%s fallback_provider=%s",
            primary,
            request_kind,
            exc.category,
            exc.status_code,
            fallback,
        )
        try:
            response = await _generate_with_provider(
                fallback,
                settings,
                request_kind,
                *args,
                dynamic_context=dynamic_context,
            )
        except LLMError as fallback_exc:
            logger.error(
                "LLM fallback failed provider=%s request_kind=%s category=%s status=%s",
                fallback,
                request_kind,
                fallback_exc.category,
                fallback_exc.status_code,
            )
            raise

        logger.info("LLM completed provider=%s request_kind=%s fallback_used=true", fallback, request_kind)
        return response


async def generate_reply(
    settings: Settings,
    user_message: str,
    *,
    dynamic_context: str | None = None,
) -> str:
    return await _generate_with_fallback(
        settings,
        "main",
        user_message,
        dynamic_context=dynamic_context,
    )


async def generate_memory_candidate(settings: Settings, user_content: str, diana_content: str) -> str:
    return await _generate_with_fallback(
        settings,
        "memory_extraction",
        user_content,
        diana_content,
    )
