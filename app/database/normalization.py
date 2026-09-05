"""Provider-neutral decoding for explicitly typed database values.

Both asyncpg and libSQL return mappings, but libSQL stores JSON and timestamps
as TEXT. Keep conversion column-aware: ordinary TEXT is never guessed to be
JSON, and offsetless legacy timestamps retain the project's UTC meaning.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


JSON_OBJECT_COLUMNS = frozenset({
    "core_identity", "personality", "speech_style", "preferences",
    "initial_relationship", "emotion_vector", "previous_state",
    "new_state", "new_value", "state_before", "state_after", "metadata",
})
JSON_ARRAY_COLUMNS = frozenset({
    "aliases", "source_episode_ids", "activated_memory_ids",
    "conflict_history", "shared_experience",
})
JSON_OBJECT_COLUMNS_BY_TABLE = frozenset({("relationship_log", "delta")})
NUMERIC_COLUMNS_BY_TABLE = frozenset({("emotion_attributions", "delta")})


def normalize_utc_datetime(value: Any) -> Any:
    """Return ISO-8601 DB text as an aware UTC datetime; leave other values intact."""
    if not isinstance(value, str):
        return value
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def normalize_json_value(value: Any, default: Any = None) -> Any:
    """Decode provider TEXT JSON without applying a heuristic to arbitrary text."""
    if value is None:
        return default
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def normalize_json_object(value: Any) -> dict[str, Any]:
    decoded = normalize_json_value(value, {})
    return decoded if isinstance(decoded, dict) else {}


def normalize_json_array(value: Any) -> list[Any]:
    decoded = normalize_json_value(value, [])
    return decoded if isinstance(decoded, list) else []


def normalize_numeric_value(value: Any) -> Any:
    """Preserve native numerics and decode libSQL TEXT numerics explicitly."""
    if not isinstance(value, str):
        return value
    try:
        return float(value)
    except ValueError:
        return value


def normalize_column_value(
    name: str,
    value: Any,
    *,
    source_tables: frozenset[str] = frozenset(),
) -> Any:
    """Normalize only DB columns with an explicit cross-provider type contract."""
    if ("relationship_log", name) in JSON_OBJECT_COLUMNS_BY_TABLE and source_tables == {"relationship_log"}:
        return normalize_json_object(value) if value is not None else None
    if ("emotion_attributions", name) in NUMERIC_COLUMNS_BY_TABLE and source_tables == {"emotion_attributions"}:
        return normalize_numeric_value(value)
    if name in JSON_OBJECT_COLUMNS:
        return normalize_json_object(value) if value is not None else None
    if name in JSON_ARRAY_COLUMNS:
        return normalize_json_array(value) if value is not None else None
    if name.endswith("_at") or name in {"started_at", "ended_at"}:
        return normalize_utc_datetime(value)
    return value
