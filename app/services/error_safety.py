"""Small, allow-list based helpers for production error diagnostics.

Exception messages are deliberately not returned from this module. Provider
SDKs and database drivers may include request bodies, connection URLs, or
credentials in their string representations.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class SafeDiagnostic:
    category: str
    error_type: str
    status_code: int | None = None
    url_scheme: str = "unavailable"
    url_present: bool = False
    host_present: bool = False


def safe_error_type(error: BaseException) -> str:
    """Return only the Python exception class, never its message or repr."""

    name = type(error).__name__
    return name if name.replace("_", "").isalnum() else "Exception"


def safe_url_metadata(url: str | None) -> tuple[str, bool, bool]:
    """Describe a URL without returning any authority, path, query, or token."""

    if not url:
        return "unavailable", False, False
    try:
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower() if parsed.scheme else "invalid"
        return scheme, True, bool(parsed.hostname)
    except (TypeError, ValueError):
        return "invalid", True, False


def database_error_category(error: BaseException) -> str:
    """Classify common driver failures while keeping their text process-local."""

    text = str(error).casefold()
    name = safe_error_type(error).casefold()
    if any(marker in text for marker in ("unauthorized", "authentication", "permission denied")):
        return "database_authentication"
    if "timeout" in text or "timeout" in name:
        return "database_timeout"
    if any(marker in text for marker in ("connection", "unavailable", "network", "dns")):
        return "database_unavailable"
    if any(marker in text for marker in ("invalid url", "configuration", "malformed")):
        return "database_configuration"
    return "database_error"


def safe_database_diagnostic(error: BaseException, database_url: str | None = None) -> SafeDiagnostic:
    scheme, present, host_present = safe_url_metadata(database_url)
    return SafeDiagnostic(
        category=database_error_category(error),
        error_type=safe_error_type(error),
        url_scheme=scheme,
        url_present=present,
        host_present=host_present,
    )
