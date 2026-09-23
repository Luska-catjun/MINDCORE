"""Immutable trigger signals derived from durable context."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.models.temporal_context import require_aware


class TriggerType(StrEnum):
    USER_ACTIVITY = "user_activity"
    IDLE_TIME = "idle_time"
    NEED_ACTIVATION = "need_activation"
    GOAL_STALENESS = "goal_staleness"
    GOAL_DEADLINE = "goal_deadline"
    SYSTEM_EVENT = "system_event"


@dataclass(frozen=True, slots=True)
class TriggerSignal:
    """One normalized signal, not a priority or instruction to act."""

    trigger_type: TriggerType
    source: str
    key: str
    strength: float
    freshness: float
    persistence: float
    occurred_at: datetime
    age_seconds: float
    related_need_keys: tuple[str, ...] = ()
    related_goal_keys: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "trigger_type", TriggerType(self.trigger_type))
        if not self.source or not self.key:
            raise ValueError("trigger_identity_invalid")
        require_aware(self.occurred_at, "trigger_occurred_at")
        for field in ("strength", "freshness", "persistence"):
            value = float(getattr(self, field))
            if value != value or value in (float("inf"), float("-inf")):
                value = 0.0
            object.__setattr__(self, field, max(0.0, min(1.0, value)))
        object.__setattr__(self, "age_seconds", max(0.0, float(self.age_seconds)))
        for field in ("related_need_keys", "related_goal_keys", "source_refs"):
            object.__setattr__(self, field, tuple(sorted(set(getattr(self, field)))))


@dataclass(frozen=True, slots=True)
class TriggerSnapshot:
    """Deterministic trigger view; it does not select or execute an action."""

    generated_at: datetime
    triggers: tuple[TriggerSignal, ...] = ()
    strongest_trigger_types: tuple[TriggerType, ...] = ()
    strongest_trigger_keys: tuple[str, ...] = ()
    has_active_triggers: bool = False
    data_warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_aware(self.generated_at, "trigger_snapshot_generated_at")
        object.__setattr__(self, "triggers", tuple(self.triggers))
        object.__setattr__(self, "strongest_trigger_types", tuple(self.strongest_trigger_types))
        object.__setattr__(self, "strongest_trigger_keys", tuple(self.strongest_trigger_keys))
        object.__setattr__(self, "data_warnings", tuple(sorted(set(self.data_warnings))))
