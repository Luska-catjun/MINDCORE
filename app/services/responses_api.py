"""OpenAI-compatible Responses API adapter used by OpenAI and xAI."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.services.llm_errors import LLMError
from app.services.prompt_loader import PromptLoadError, load_memory_extraction_prompt, load_persona_identity_prompt
from app.services.rest_llm import record_success, request_json


@dataclass(frozen=True)
class ResponsesProvider:
    provider: str
    model: str
    api_key: str | None
    api_base_url: str
    timeout: float
    max_retries: int


def provider_config(settings: Settings, provider: str) -> ResponsesProvider:
    return ResponsesProvider(
        provider=provider,
        model=str(getattr(settings, f"{provider}_model")),
        api_key=getattr(settings, f"{provider}_api_key"),
        api_base_url=str(getattr(settings, f"{provider}_api_base_url")),
        timeout=float(getattr(settings, f"{provider}_timeout_seconds")),
        max_retries=int(getattr(settings, f"{provider}_max_retries")),
    )


def build_responses_payload(user_message: str, system_instruction: str, *, dynamic_context: str | None = None, memory: bool = False) -> dict[str, Any]:
    content = user_message if not dynamic_context else f"{dynamic_context}\n\n[CURRENT USER MESSAGE]\n{user_message}"
    return {
        "model": None,
        "instructions": system_instruction,
        "input": content,
        "max_output_tokens": 256 if memory else 1024,
        "store": False,
    }


def extract_output_text(response: dict[str, Any]) -> str:
    parts: list[str] = []
    for output in response.get("output", []):
        if not isinstance(output, dict) or output.get("type") != "message":
            continue
        for item in output.get("content", []):
            if isinstance(item, dict) and item.get("type") == "output_text" and item.get("text"):
                parts.append(str(item["text"]))
    text = "\n".join(parts).strip()
    if not text:
        raise LLMError("Responses API returned an empty response.", category="empty_response")
    return text


def _request(config: ResponsesProvider, payload: dict[str, Any], request_kind: str) -> str:
    payload["model"] = config.model
    response, latency_ms = request_json(
        provider=config.provider, model=config.model, api_base_url=config.api_base_url,
        path="responses", api_key=config.api_key,
        headers={"Authorization": f"Bearer {config.api_key or ''}"}, payload=payload,
        timeout=config.timeout, max_retries=config.max_retries, request_kind=request_kind,
    )
    text = extract_output_text(response)
    record_success(provider=config.provider, model=config.model, request_kind=request_kind,
                   usage=response.get("usage") or {}, latency_ms=latency_ms)
    return text


def _identity(settings: Settings, config: ResponsesProvider, identity_prompt: str | None) -> str:
    if identity_prompt is not None:
        return identity_prompt
    try:
        return load_persona_identity_prompt(settings)
    except PromptLoadError as exc:
        raise LLMError(str(exc), category="prompt_configuration", model=config.model,
                       api_base_url=config.api_base_url) from exc


def _memory_prompt(config: ResponsesProvider) -> str:
    try:
        return load_memory_extraction_prompt()
    except PromptLoadError as exc:
        raise LLMError(
            str(exc),
            category="prompt_configuration",
            model=config.model,
            api_base_url=config.api_base_url,
        ) from exc


async def generate_reply_for(provider: str, settings: Settings, user_message: str, *, dynamic_context: str | None = None, identity_prompt: str | None = None) -> str:
    config = provider_config(settings, provider)
    payload = build_responses_payload(user_message, _identity(settings, config, identity_prompt), dynamic_context=dynamic_context)
    return await asyncio.to_thread(_request, config, payload, "main")


async def generate_memory_candidate_for(provider: str, settings: Settings, user_content: str, diana_content: str) -> str:
    config = provider_config(settings, provider)
    payload = build_responses_payload(
        f"[USER MESSAGE]\n{user_content}\n\n[PERSONA RESPONSE]\n{diana_content}",
        _memory_prompt(config), memory=True,
    )
    return await asyncio.to_thread(_request, config, payload, "memory_extraction")
