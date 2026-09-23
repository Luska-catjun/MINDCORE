"""Immutable structured intent for a later executor; contains no dialogue."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import math

from app.models.autonomy_decision import AutonomyReasonCode
from app.models.temporal_context import require_aware
from app.models.trigger_context import TriggerType


class IntentionType(StrEnum):
    CHECK_IN = "CHECK_IN"
    REVISIT_GOAL = "REVISIT_GOAL"
    REMIND_DEADLINE = "REMIND_DEADLINE"
    ACKNOWLEDGE_EVENT = "ACKNOWLEDGE_EVENT"


class TargetKind(StrEnum):
    NONE = "NONE"
    NEED = "NEED"
    GOAL = "GOAL"
    SYSTEM_EVENT = "SYSTEM_EVENT"


class IntentionConstraint(StrEnum):
    NO_DUPLICATE_TOPIC = "NO_DUPLICATE_TOPIC"
    CONCISE_OPENING = "CONCISE_OPENING"
    DO_NOT_CLAIM_USER_REQUEST = "DO_NOT_CLAIM_USER_REQUEST"
    DO_NOT_IMPLY_EXTERNAL_EVENT_IF_UNVERIFIED = "DO_NOT_IMPLY_EXTERNAL_EVENT_IF_UNVERIFIED"
    TARGET_SINGLE_TOPIC = "TARGET_SINGLE_TOPIC"
    NO_ACTION_EXECUTION = "NO_ACTION_EXECUTION"


@dataclass(frozen=True, slots=True)
class AutonomyIntention:
    """One content-free intention, distinct from foreground response intention."""

    intention_type: IntentionType
    target_kind: TargetKind
    target_key: str | None
    reason_codes: tuple[AutonomyReasonCode, ...]
    source_trigger_type: TriggerType | None
    source_trigger_key: str | None
    related_need_keys: tuple[str, ...]
    related_goal_keys: tuple[str, ...]
    constraints: tuple[IntentionConstraint, ...]
    urgency: float
    confidence: float
    evaluated_at: datetime
    intention_key: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "intention_type", IntentionType(self.intention_type))
        object.__setattr__(self, "target_kind", TargetKind(self.target_kind))
        expected_target = {
            IntentionType.CHECK_IN: TargetKind.NEED,
            IntentionType.REVISIT_GOAL: TargetKind.GOAL,
            IntentionType.REMIND_DEADLINE: TargetKind.GOAL,
            IntentionType.ACKNOWLEDGE_EVENT: TargetKind.SYSTEM_EVENT,
        }[self.intention_type]
        if self.target_kind != expected_target:
            raise ValueError("autonomy_intention_target_kind_mismatch")
        object.__setattr__(self, "reason_codes", tuple(sorted(
            {AutonomyReasonCode(item) for item in self.reason_codes}, key=str,
        )))
        object.__setattr__(self, "related_need_keys", tuple(sorted(set(self.related_need_keys))))
        object.__setattr__(self, "related_goal_keys", tuple(sorted(set(self.related_goal_keys))))
        object.__setattr__(self, "constraints", tuple(sorted(
            {IntentionConstraint(item) for item in self.constraints}, key=str,
        )))
        if self.source_trigger_type is not None:
            object.__setattr__(self, "source_trigger_type", TriggerType(self.source_trigger_type))
        if (self.source_trigger_type is None) != (self.source_trigger_key is None):
            raise ValueError("autonomy_intention_source_trigger_incomplete")
        if self.source_trigger_key is not None and not self.source_trigger_key.strip():
            raise ValueError("autonomy_intention_source_trigger_key_invalid")
        if self.source_trigger_type is not None:
            expected_trigger = {
                IntentionType.CHECK_IN: TriggerType.NEED_ACTIVATION,
                IntentionType.REVISIT_GOAL: TriggerType.GOAL_STALENESS,
                IntentionType.REMIND_DEADLINE: TriggerType.GOAL_DEADLINE,
                IntentionType.ACKNOWLEDGE_EVENT: TriggerType.SYSTEM_EVENT,
            }[self.intention_type]
            if self.source_trigger_type != expected_trigger:
                raise ValueError("autonomy_intention_source_trigger_mismatch")
        if self.target_kind == TargetKind.NONE:
            if self.target_key is not None:
                raise ValueError("autonomy_intention_none_target_has_key")
        elif not self.target_key:
            raise ValueError("autonomy_intention_target_key_required")
        expected_key = f"{self.intention_type}:{self.target_key or ''}"
        if self.intention_key != expected_key:
            raise ValueError("autonomy_intention_key_mismatch")
        if not self.reason_codes:
            raise ValueError("autonomy_intention_reason_required")
        for field in ("urgency", "confidence"):
            value = float(getattr(self, field))
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"autonomy_intention_{field}_out_of_range")
            object.__setattr__(self, field, value)
        require_aware(self.evaluated_at, "autonomy_intention_evaluated_at")
        object.__setattr__(self, "evaluated_at", self.evaluated_at.astimezone(timezone.utc))


DEFAULT_INTENTION_CONSTRAINTS = tuple(sorted((
    IntentionConstraint.NO_DUPLICATE_TOPIC,
    IntentionConstraint.CONCISE_OPENING,
    IntentionConstraint.DO_NOT_CLAIM_USER_REQUEST,
    IntentionConstraint.DO_NOT_IMPLY_EXTERNAL_EVENT_IF_UNVERIFIED,
    IntentionConstraint.TARGET_SINGLE_TOPIC,
    IntentionConstraint.NO_ACTION_EXECUTION,
), key=str))
