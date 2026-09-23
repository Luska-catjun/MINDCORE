"""Immutable temporal and idle context derived from durable activity."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

Daypart = Literal["late_night", "morning", "afternoon", "evening", "night"]
IdleLevel = Literal["active", "recent", "idle", "long_idle"]


def require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field}_must_be_timezone_aware")


@dataclass(frozen=True, slots=True)
class ActivityPoint:
    """Content-free durable activity timestamp and provenance."""

    actor: Literal["user", "persona", "system"]
    occurred_at: datetime
    source_ref: str
    provenance: Literal["turn_context", "message_role"]

    def __post_init__(self) -> None:
        if self.actor not in {"user", "persona", "system"}:
            raise ValueError("activity_actor_invalid")
        if not self.source_ref:
            raise ValueError("activity_source_ref_invalid")
        require_aware(self.occurred_at, "activity_occurred_at")


@dataclass(frozen=True, slots=True)
class TemporalInputs:
    """Read-only activity facts loaded from turns and messages."""

    activity: tuple[ActivityPoint, ...] = ()
    user_has_ever_spoken: bool = False
    persona_has_ever_spoken: bool = False
    data_warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "activity", tuple(self.activity))
        object.__setattr__(self, "data_warnings", tuple(sorted(set(self.data_warnings))))


@dataclass(frozen=True, slots=True)
class TemporalContext:
    """Temporal facts and idle pressure; this is not an action decision."""

    now: datetime
    timezone: str
    local_now: datetime
    local_date: date
    daypart: Daypart
    last_user_activity_at: datetime | None = None
    last_persona_activity_at: datetime | None = None
    last_system_activity_at: datetime | None = None
    last_conversation_activity_at: datetime | None = None
    last_user_activity_source_ref: str | None = None
    last_persona_activity_source_ref: str | None = None
    last_system_activity_source_ref: str | None = None
    seconds_since_user_activity: float = 0.0
    seconds_since_persona_activity: float = 0.0
    seconds_since_system_activity: float = 0.0
    seconds_since_conversation_activity: float = 0.0
    idle_duration_seconds: float = 0.0
    idle_level: IdleLevel = "active"
    idle_pressure: float = 0.0
    conversation_has_activity: bool = False
    user_has_ever_spoken: bool = False
    persona_has_ever_spoken: bool = False
    data_warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_aware(self.now, "temporal_context_now")
        require_aware(self.local_now, "temporal_context_local_now")
        for field in (
            "last_user_activity_at", "last_persona_activity_at",
            "last_system_activity_at", "last_conversation_activity_at",
        ):
            value = getattr(self, field)
            if value is not None:
                require_aware(value, f"temporal_context_{field}")
        object.__setattr__(self, "data_warnings", tuple(sorted(set(self.data_warnings))))
