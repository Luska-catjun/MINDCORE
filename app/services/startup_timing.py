"""Secret-safe timing events for desktop/backend startup profiling."""

from __future__ import annotations

import logging


_ALLOWED_PHASES = {
    "settings", "identity", "database_connect", "schema", "hydration",
    "recovery", "uvicorn", "health", "frontend",
}
_ALLOWED_OPERATIONS = {
    "settings_load", "identity_load", "pool_create", "pool_acquire", "select_1",
    "fast_schema_snapshot_start", "fast_schema_snapshot_end", "ledger_validation",
    "fallback_full_classify_start", "fallback_full_classify_end",
    "schema_authority_complete", "narrative_start", "narrative_end",
    "self_model_start", "self_model_end", "recovery_task_schedule",
    "lifespan_complete", "ready_route_registered", "database_check",
    "native_ready", "authenticated_session", "chat_ready",
}


def emit_startup_timing(phase: str, operation: str, elapsed_ms: int) -> None:
    """Log only fixed vocabulary and a duration; never accepts arbitrary labels."""
    if phase not in _ALLOWED_PHASES or operation not in _ALLOWED_OPERATIONS:
        return
    logging.getLogger("diana.startup").info(
        "MINDCORE_STARTUP_TIMING phase=%s operation=%s elapsed_ms=%d",
        phase,
        operation,
        max(0, int(elapsed_ms)),
    )
