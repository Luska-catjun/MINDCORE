"""Secret-safe diagnostics for the private v0.2.2 startup build."""

from __future__ import annotations

import sys


STARTUP_DIAGNOSTIC_PREFIX = "MINDCORE_STARTUP_DIAGNOSTIC"
STARTUP_PROGRESS_PREFIX = "MINDCORE_STARTUP_PROGRESS"
DIAGNOSTIC_BUILD_LABEL = "v0.2.2-diagnostic-2"
_REPORTED_ATTRIBUTE = "_mindcore_startup_diagnostic_reported"

STARTUP_PROGRESS_PHASES = frozenset(
    {
        "settings",
        "identity",
        "database_connect",
        "schema_classification",
        "schema_authority",
        "migration_ledger",
        "narrative_hydration",
        "self_model_hydration",
        "ready",
    }
)
STARTUP_PROGRESS_OPERATIONS = frozenset(
    {
        "settings_load",
        "identity_load",
        "pool_create",
        "pool_acquire",
        "schema_classify_initial",
        "schema_classify_final",
        "schema_ensure",
        "ledger_check",
        "ledger_create",
        "ledger_baseline_insert",
        "ledger_validate",
        "ledger_commit",
        "narrative_hydrate",
        "self_model_hydrate",
        "lifespan_ready",
        "ready_wait",
    }
)


def _identifier(value: object, *, fallback: str, max_length: int = 96) -> str:
    text = str(value)
    if (
        not text
        or len(text) > max_length
        or not all(character.isalnum() or character in "_.-" for character in text)
    ):
        return fallback
    return text


def startup_category(error: BaseException, *, fallback: str = "runtime") -> str:
    """Classify by exception type only; never inspect a potentially secret message."""
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, ConnectionError):
        return "connection"
    if isinstance(error, OSError):
        return "native_or_network"
    return fallback


def emit_startup_progress(
    *,
    phase: str,
    operation: str,
    schema_version: int | str | None = None,
) -> None:
    """Emit a fixed-vocabulary startup entry marker without runtime data."""
    if phase not in STARTUP_PROGRESS_PHASES:
        raise ValueError("unsupported startup progress phase")
    if operation not in STARTUP_PROGRESS_OPERATIONS:
        raise ValueError("unsupported startup progress operation")
    fields = [
        f"build={DIAGNOSTIC_BUILD_LABEL}",
        f"phase={phase}",
        f"operation={operation}",
    ]
    if schema_version is not None and str(schema_version).isdecimal():
        fields.append(f"schema_version={schema_version}")
    print(f"{STARTUP_PROGRESS_PREFIX} {' '.join(fields)}", file=sys.stderr, flush=True)


def emit_startup_diagnostic(
    *,
    phase: str,
    category: str,
    error: BaseException,
    operation: str | None = None,
    schema_version: int | str | None = None,
) -> None:
    """Emit one allowlisted record and mark the exception to avoid coarser duplicates."""
    if getattr(error, _REPORTED_ATTRIBUTE, False):
        return
    fields = [
        f"build={DIAGNOSTIC_BUILD_LABEL}",
        f"phase={_identifier(phase, fallback='startup')}",
        f"category={_identifier(category, fallback='runtime')}",
    ]
    if operation is not None:
        fields.append(f"operation={_identifier(operation, fallback='unknown')}")
    if schema_version is not None and str(schema_version).isdecimal():
        fields.append(f"schema_version={schema_version}")
    fields.append(
        f"exception_class={_identifier(type(error).__name__, fallback='Exception')}"
    )
    print(f"{STARTUP_DIAGNOSTIC_PREFIX} {' '.join(fields)}", file=sys.stderr, flush=True)
    try:
        setattr(error, _REPORTED_ATTRIBUTE, True)
    except Exception:
        # Some extension exceptions may reject custom attributes. The emitted
        # record is still safe; a later coarser record remains allowlisted.
        pass
