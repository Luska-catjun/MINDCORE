"""Immutable, derived motivational projections; never durable authority."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math


def _score(value: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(numeric):
        return 0.0
    return max(0.0, min(1.0, numeric))


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field}_must_be_timezone_aware")


@dataclass(frozen=True, slots=True)
class MotivationEvidenceRef:
    """Content-free reference to durable evidence behind a projection."""

    kind: str
    id: str
    source_type: str | None = None
    source_id: str | None = None
    occurred_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.kind or not self.id:
            raise ValueError("motivation_evidence_reference_invalid")
        if self.occurred_at is not None:
            _require_aware(self.occurred_at, "motivation_evidence_occurred_at")


@dataclass(frozen=True, slots=True)
class NeedState:
    """Current normalized pressure for one durable Need key."""

    key: str
    baseline: float
    effective: float
    activation: float
    urgency: float
    persistence: float
    last_triggered_at: datetime | None
    related_goal_keys: tuple[str, ...] = ()
    evidence_refs: tuple[MotivationEvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("need_state_key_invalid")
        for field in ("baseline", "effective", "activation", "urgency", "persistence"):
            object.__setattr__(self, field, _score(getattr(self, field)))
        if self.last_triggered_at is not None:
            _require_aware(self.last_triggered_at, "need_state_last_triggered_at")
        object.__setattr__(self, "related_goal_keys", tuple(self.related_goal_keys))
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))


@dataclass(frozen=True, slots=True)
class GoalState:
    """Motivational view of a durable goal, not an execution instruction."""

    goal_key: str
    status: str
    priority: float
    importance: float
    urgency: float
    activation: float
    origin_need: str
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None
    evidence_refs: tuple[MotivationEvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        if not self.goal_key or not self.status or not self.origin_need:
            raise ValueError("goal_state_identity_invalid")
        for field in ("priority", "importance", "urgency", "activation"):
            object.__setattr__(self, field, _score(getattr(self, field)))
        _require_aware(self.created_at, "goal_state_created_at")
        _require_aware(self.updated_at, "goal_state_updated_at")
        if self.expires_at is not None:
            _require_aware(self.expires_at, "goal_state_expires_at")
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))


@dataclass(frozen=True, slots=True)
class MotivationalSnapshot:
    """Current motivational pressure. It does not decide whether to act."""

    generated_at: datetime
    needs: tuple[NeedState, ...] = ()
    goals: tuple[GoalState, ...] = ()
    dominant_need_keys: tuple[str, ...] = ()
    dominant_goal_keys: tuple[str, ...] = ()
    data_warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_aware(self.generated_at, "motivational_snapshot_generated_at")
        for field in (
            "needs", "goals", "dominant_need_keys", "dominant_goal_keys", "data_warnings"
        ):
            object.__setattr__(self, field, tuple(getattr(self, field)))
