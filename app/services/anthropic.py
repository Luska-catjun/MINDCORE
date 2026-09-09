"""Anthropic Messages API adapter for MindCore's stateless LLM contract."""

from __future__ import annotations

import asyncio
from typing import Any

from app.config import Settings
from app.services.llm_errors import LLMError
from app.services.prompt_loader import PromptLoadError, load_memory_extraction_prompt, load_persona_identity_prompt
from app.services.rest_llm import record_success, request_json


def build_anthropic_payload(user_message: str, system_instruction: str, *, dynamic_context: str | None = None, memory: bool = False) -> dict[str, Any]:
    content = user_message if not dynamic_context else f"{dynamic_context}\n\n[CURRENT USER MESSAGE]\n{user_message}"
    return {
        "model": None,
        "system": system_instruction,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 256 if memory else 1024,
        "temperature": 0 if memory else 0.7,
    }


def _extract_text(response: dict[str, Any]) -> str:
    text = "\n".join(
        str(item.get("text", ""))
        for item in response.get("content", [])
        if isinstance(item, dict) and item.get("type") == "text"
    ).strip()
    if not text:
        raise LLMError("Anthropic returned an empty response.", category="empty_response")
    return text


def _request(settings: Settings, payload: dict[str, Any], request_kind: str) -> str:
    payload["model"] = settings.anthropic_model
    response, latency_ms = request_json(
        provider="anthropic", model=settings.anthropic_model,
        api_base_url=settings.anthropic_api_base_url, path="messages",
        api_key=settings.anthropic_api_key,
        headers={"x-api-key": settings.anthropic_api_key or "", "anthropic-version": "2023-06-01"},
        payload=payload, timeout=settings.anthropic_timeout_seconds,
        max_retries=settings.anthropic_max_retries, request_kind=request_kind,
    )
    text = _extract_text(response)
    record_success(provider="anthropic", model=settings.anthropic_model, request_kind=request_kind,
                   usage=response.get("usage") or {}, latency_ms=latency_ms)
    return text


def _identity(settings: Settings, identity_prompt: str | None) -> str:
    if identity_prompt is not None:
        return identity_prompt
    try:
        return load_persona_identity_prompt(settings)
    except PromptLoadError as exc:
        raise LLMError(str(exc), category="prompt_configuration", model=settings.anthropic_model,
                       api_base_url=settings.anthropic_api_base_url) from None


def _memory_prompt(settings: Settings) -> str:
    try:
        return load_memory_extraction_prompt()
    except PromptLoadError as exc:
        raise LLMError(
            str(exc),
            category="prompt_configuration",
            model=settings.anthropic_model,
            api_base_url=settings.anthropic_api_base_url,
        ) from None


async def generate_reply(settings: Settings, user_message: str, *, dynamic_context: str | None = None, identity_prompt: str | None = None) -> str:
    payload = build_anthropic_payload(user_message, _identity(settings, identity_prompt), dynamic_context=dynamic_context)
    return await asyncio.to_thread(_request, settings, payload, "main")


async def generate_memory_candidate(settings: Settings, user_content: str, diana_content: str) -> str:
    payload = build_anthropic_payload(
        f"[USER MESSAGE]\n{user_content}\n\n[PERSONA RESPONSE]\n{diana_content}",
        _memory_prompt(settings), memory=True,
    )
    return await asyncio.to_thread(_request, settings, payload, "memory_extraction")
