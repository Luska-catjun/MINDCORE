"""Immutable output contract for deterministic autonomy policy evaluation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import math

from app.models.temporal_context import require_aware
from app.models.trigger_context import TriggerType


class ActionClass(StrEnum):
    """What the current facts permit; ACT never executes an action."""

    ACT = "ACT"
    DO_NOT_ACT = "DO_NOT_ACT"
    DEFER = "DEFER"


class AutonomyReasonCode(StrEnum):
    """Stable, content-free reason vocabulary shared with the M4 contract."""

    RECENT_USER_ACTIVITY = "RECENT_USER_ACTIVITY"
    INSUFFICIENT_IDLE = "INSUFFICIENT_IDLE"
    LONG_IDLE = "LONG_IDLE"
    STRONG_NEED = "STRONG_NEED"
    STALE_GOAL = "STALE_GOAL"
    DEADLINE_PRESSURE = "DEADLINE_PRESSURE"
    SYSTEM_EVENT = "SYSTEM_EVENT"
    NO_DURABLE_ACTIVITY = "NO_DURABLE_ACTIVITY"
    CONFLICTING_SIGNALS = "CONFLICTING_SIGNALS"
    NO_ACTIONABLE_MOTIVATION = "NO_ACTIONABLE_MOTIVATION"


def _unit_interval(value: float, field: str) -> float:
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValueError(f"autonomy_decision_{field}_out_of_range")
    return result


@dataclass(frozen=True, slots=True)
class AutonomyDecision:
    """Safe structured rationale for a decision, with no user/message content."""

    action_class: ActionClass
    reason_codes: tuple[AutonomyReasonCode, ...]
    suppression_reasons: tuple[AutonomyReasonCode, ...]
    confidence: float
    urgency: float
    primary_trigger_type: TriggerType | None
    primary_trigger_key: str | None
    evaluated_at: datetime
    decision_factors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "action_class", ActionClass(self.action_class))
        reasons = tuple(sorted({AutonomyReasonCode(item) for item in self.reason_codes}, key=str))
        suppressions = tuple(sorted(
            {AutonomyReasonCode(item) for item in self.suppression_reasons}, key=str
        ))
        object.__setattr__(self, "reason_codes", reasons)
        object.__setattr__(self, "suppression_reasons", suppressions)
        object.__setattr__(self, "confidence", _unit_interval(self.confidence, "confidence"))
        object.__setattr__(self, "urgency", _unit_interval(self.urgency, "urgency"))
        if self.primary_trigger_type is not None:
            object.__setattr__(self, "primary_trigger_type", TriggerType(self.primary_trigger_type))
        if (self.primary_trigger_type is None) != (self.primary_trigger_key is None):
            raise ValueError("autonomy_decision_primary_trigger_incomplete")
        require_aware(self.evaluated_at, "autonomy_decision_evaluated_at")
        object.__setattr__(self, "decision_factors", tuple(sorted(set(self.decision_factors))))
