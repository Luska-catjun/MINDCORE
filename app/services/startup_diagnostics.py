"""Secret-safe diagnostics for the private v0.2.2 startup build."""

from __future__ import annotations

import sys


STARTUP_DIAGNOSTIC_PREFIX = "MINDCORE_STARTUP_DIAGNOSTIC"
DIAGNOSTIC_BUILD_LABEL = "v0.2.2-diagnostic-1"
_REPORTED_ATTRIBUTE = "_mindcore_startup_diagnostic_reported"


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
