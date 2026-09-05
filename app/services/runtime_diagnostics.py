"""Ephemeral, safe operational diagnostics for the read-only Observation Surface."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from threading import Lock
from typing import Any

_MAX_LLM_CALLS = 20
_lock = Lock()
_llm_calls: deque[dict[str, Any]] = deque(maxlen=_MAX_LLM_CALLS)
_last_context: dict[str, Any] = {
    "dynamic_context_chars": 0,
    "context_mode": None,
    "updated_at": None,
}
_last_fallback_event: dict[str, Any] | None = None
_last_epistemic: dict[str, Any] = {"items": [], "latency_ms": None, "updated_at": None}
_episode_promotions: deque[dict[str, Any]] = deque(maxlen=20)
_narrative_updates: deque[dict[str, Any]] = deque(maxlen=20)
_self_model_updates: deque[dict[str, Any]] = deque(maxlen=20)
_last_self_model_activation: dict[str, Any] = {"considered": 0, "active": 0, "suppressed": 0, "latency_ms": None, "updated_at": None}
_decision_captures: deque[dict[str, Any]] = deque(maxlen=20)
_emotion_updates: deque[dict[str, Any]] = deque(maxlen=20)
_knowledge_acquisitions: deque[dict[str, Any]] = deque(maxlen=20)
_relationship_captures: deque[dict[str, Any]] = deque(maxlen=20)


def record_context(*, dynamic_context_chars: int, context_mode: str) -> None:
    """Store metrics only; never retain prompt or context text."""
    with _lock:
        _last_context.update({
            "dynamic_context_chars": dynamic_context_chars,
            "context_mode": context_mode,
            "updated_at": datetime.now(timezone.utc),
        })


def record_llm_call(
    *,
    request_kind: str,
    provider: str,
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
    total_tokens: int | None,
    latency_ms: float,
    success: bool,
    error_category: str | None = None,
) -> None:
    """Record metadata only. Prompts, responses, keys, and error text are excluded."""
    with _lock:
        _llm_calls.appendleft({
            "timestamp": datetime.now(timezone.utc),
            "request_kind": request_kind,
            "provider": provider,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "latency_ms": round(latency_ms, 1),
            "success": success,
            "error_category": error_category,
        })


def record_fallback_event(*, primary: str, fallback: str, error_category: str) -> None:
    with _lock:
        global _last_fallback_event
        _last_fallback_event = {
            "timestamp": datetime.now(timezone.utc),
            "primary_provider": primary,
            "fallback_provider": fallback,
            "error_category": error_category,
        }


def record_epistemic(*, items: list[dict[str, Any]], latency_ms: float) -> None:
    """Keep subject keys/statuses only; never store the user's raw message."""
    with _lock:
        _last_epistemic.update({"items": items, "latency_ms": round(latency_ms, 1), "updated_at": datetime.now(timezone.utc)})


def record_episode_promotion(*, status: str, score: float, reasons: list[str]) -> None:
    """Keep compact decision metadata for Debug; never retain turn content."""
    with _lock:
        _episode_promotions.appendleft({
            "timestamp": datetime.now(timezone.utc), "status": status,
            "score": round(score, 3), "reasons": reasons,
        })


def record_narrative_update(*, episode_id: str, accepted: bool, subjects: list[str], latency_ms: float) -> None:
    """Keep only provenance IDs and outcomes; narrative text is never logged."""
    with _lock:
        _narrative_updates.appendleft({
            "timestamp": datetime.now(timezone.utc), "episode_id": episode_id,
            "accepted": accepted, "subjects": subjects[:4], "latency_ms": round(latency_ms, 1),
        })


def record_self_model_update(*, episode_id: str, accepted: bool, claim_count: int, latency_ms: float) -> None:
    """Record shadow update metadata only; no belief text is retained here."""
    with _lock:
        _self_model_updates.appendleft({
            "timestamp": datetime.now(timezone.utc), "episode_id": episode_id,
            "accepted": accepted, "claim_count": claim_count, "latency_ms": round(latency_ms, 1),
        })


def record_self_model_activation(*, considered: int, active: int, suppressed: int, latency_ms: float) -> None:
    """Record counts only; never retain belief or user-message text."""
    with _lock:
        _last_self_model_activation.update({
            "considered": considered, "active": active, "suppressed": suppressed,
            "latency_ms": round(latency_ms, 3), "updated_at": datetime.now(timezone.utc),
        })


def record_decision_capture(
    *,
    context_detected: bool,
    detected: bool,
    chosen: str | None,
    reason_valid: bool | None,
    persisted: bool,
    episode_linked: bool,
    error_category: str | None = None,
    domain: str | None = None,
    confidence: float | None = None,
    result: str | None = None,
    rejection_reason: str | None = None,
) -> None:
    """Keep the deterministic Decision funnel observable without reply text."""
    with _lock:
        _decision_captures.appendleft({"timestamp": datetime.now(timezone.utc), "choice_context_detected": context_detected,
            "assistant_choice_detected": detected, "chosen": chosen, "reason_valid": reason_valid,
            "persisted": persisted, "episode_linked": episode_linked, "error_category": error_category,
            "domain": domain, "confidence": round(confidence, 3) if confidence is not None else None,
            "result": result, "rejection_reason": rejection_reason})


def record_emotion_update(
    *,
    cause_type: str | None,
    raw_delta: dict[str, float],
    applied_delta: dict[str, float],
    recovery: dict[str, Any] | None = None,
) -> None:
    """Record channel/recovery metadata only; never retain user text or output."""
    with _lock:
        _emotion_updates.appendleft({"timestamp": datetime.now(timezone.utc), "cause_type": cause_type,
            "raw_delta": raw_delta, "applied_delta": applied_delta, "changed_channels": list(applied_delta),
            "recovery": dict(recovery) if recovery else None})


def record_knowledge_acquisition(*, status: str, candidate_count: int, created_count: int = 0, reinforced_count: int = 0) -> None:
    """Keep funnel outcomes only; never retain teaching text or fact content."""
    with _lock:
        _knowledge_acquisitions.appendleft({
            "timestamp": datetime.now(timezone.utc), "status": status,
            "candidate_count": candidate_count, "created_count": created_count,
            "reinforced_count": reinforced_count,
        })


def record_relationship_capture(*, eligible: bool, signal: str | None, delta: dict[str, float], result: str, reason: str | None) -> None:
    """Keep relationship funnel metadata only; never retain message text."""
    with _lock:
        _relationship_captures.appendleft({
            "timestamp": datetime.now(timezone.utc), "eligible": eligible,
            "signal": signal, "delta": dict(delta), "result": result, "reason": reason,
        })


def snapshot() -> dict[str, Any]:
    with _lock:
        return {
            "last_context": dict(_last_context),
            "last_fallback_event": dict(_last_fallback_event) if _last_fallback_event else None,
            "last_epistemic": dict(_last_epistemic),
            "episode_promotions": [dict(item) for item in _episode_promotions],
            "narrative_updates": [dict(item) for item in _narrative_updates],
            "self_model_updates": [dict(item) for item in _self_model_updates],
            "last_self_model_activation": dict(_last_self_model_activation),
            "decision_captures": [dict(item) for item in _decision_captures],
            "emotion_updates": [dict(item) for item in _emotion_updates],
            "knowledge_acquisitions": [dict(item) for item in _knowledge_acquisitions],
            "relationship_captures": [dict(item) for item in _relationship_captures],
            "llm_calls": [dict(item) for item in _llm_calls],
        }
