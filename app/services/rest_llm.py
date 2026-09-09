"""Secret-safe synchronous JSON transport shared by REST LLM adapters."""

from __future__ import annotations

import json
import logging
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.services.llm_errors import LLMError
from app.services.runtime_diagnostics import record_llm_call

logger = logging.getLogger("diana.rest_llm")


def _category(provider: str, status: int, body: str) -> str:
    lowered = body.lower()
    if status in {401, 403}:
        return "authentication"
    if status == 404:
        return "model_or_api_version"
    if status == 400 and ("model" in lowered or "version" in lowered):
        return "model"
    if status == 400:
        return "configuration"
    if status == 408:
        return "timeout"
    if status == 429:
        return "quota_or_rate_limit"
    if status >= 500:
        return f"{provider}_server"
    return f"{provider}_http"


def request_json(
    *,
    provider: str,
    model: str,
    api_base_url: str,
    path: str,
    api_key: str | None,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float,
    max_retries: int,
    request_kind: str,
) -> tuple[dict[str, Any], float]:
    if not api_key:
        raise LLMError(
            f"{provider.upper()} API key is not configured.",
            category="configuration",
            model=model,
            api_base_url=api_base_url,
        )
    request = Request(
        f"{api_base_url.rstrip('/')}/{path.lstrip('/')}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    started_at = time.perf_counter()
    for attempt in range(max_retries + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
            break
        except HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            category = _category(provider, exc.code, error_body)
            if category == f"{provider}_server" and attempt < max_retries:
                time.sleep(0.25 * (attempt + 1))
                continue
            record_llm_call(request_kind=request_kind, provider=provider, model=model,
                            input_tokens=None, output_tokens=None, total_tokens=None,
                            latency_ms=(time.perf_counter() - started_at) * 1000, success=False,
                            error_category=category)
            logger.error("LLM HTTP error provider=%s category=%s status=%s model=%s", provider, category, exc.code, model)
            raise LLMError(
                f"{provider} API request failed.", category=category, status_code=exc.code,
                model=model, api_base_url=api_base_url,
            ) from None
        except TimeoutError as exc:
            if attempt < max_retries:
                time.sleep(0.25 * (attempt + 1))
                continue
            category = "timeout"
            record_llm_call(request_kind=request_kind, provider=provider, model=model,
                            input_tokens=None, output_tokens=None, total_tokens=None,
                            latency_ms=(time.perf_counter() - started_at) * 1000, success=False,
                            error_category=category)
            raise LLMError(f"{provider} API request timed out.", category=category, model=model, api_base_url=api_base_url) from None
        except URLError as exc:
            category = "timeout" if isinstance(exc.reason, TimeoutError) else "network"
            if attempt < max_retries:
                time.sleep(0.25 * (attempt + 1))
                continue
            record_llm_call(request_kind=request_kind, provider=provider, model=model,
                            input_tokens=None, output_tokens=None, total_tokens=None,
                            latency_ms=(time.perf_counter() - started_at) * 1000, success=False,
                            error_category=category)
            logger.error("LLM connection error provider=%s category=%s model=%s error_type=%s", provider, category, model, type(exc).__name__)
            raise LLMError(f"{provider} API connection failed.", category=category, model=model, api_base_url=api_base_url) from None
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise LLMError(f"{provider} API returned invalid JSON.", category="response_json", model=model, api_base_url=api_base_url) from None
    if not isinstance(parsed, dict):
        raise LLMError(f"{provider} API returned an invalid response.", category="response_json", model=model, api_base_url=api_base_url)
    return parsed, (time.perf_counter() - started_at) * 1000


def record_success(*, provider: str, model: str, request_kind: str, usage: dict[str, Any], latency_ms: float) -> None:
    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
    output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
    total_tokens = usage.get("total_tokens")
    if total_tokens is None and isinstance(input_tokens, int) and isinstance(output_tokens, int):
        total_tokens = input_tokens + output_tokens
    record_llm_call(request_kind=request_kind, provider=provider, model=model,
                    input_tokens=input_tokens, output_tokens=output_tokens,
                    total_tokens=total_tokens, latency_ms=latency_ms, success=True)
