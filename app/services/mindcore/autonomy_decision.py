"""Pure deterministic policy: motivational, temporal, and trigger facts to decision.

Hierarchy: (1) durable-activity sanity, (2) global recent-user timing guard,
(3) two-hour idle eligibility, (4) independent motivational evidence classes,
(5) deadline urgency, (6) system-event context, (7) deterministic conflict
annotation, (8) final class. This module deliberately has no runtime wiring.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math

from app.models.autonomy_decision import (
    ActionClass,
    AutonomyDecision,
    AutonomyReasonCode as Reason,
)
from app.models.motivation import GoalState, MotivationalSnapshot, NeedState
from app.models.temporal_context import TemporalContext
from app.models.trigger_context import TriggerSignal, TriggerSnapshot, TriggerType
from app.services.mindcore.temporal_context import IDLE_LONG_SECONDS, IDLE_RECENT_SECONDS
from app.services.mindcore.trigger_context import (
    GOAL_STALENESS_START_SECONDS,
    TRIGGER_ACTIVE_EPSILON,
)


# Central policy boundaries. Need activation follows the normalized 0..1 M2
# scale; 0.70 is the minimum considered a strong activation. Recent-user
# suppression follows M3's 15-minute "recent" window. Idle eligibility follows
# M3's two-hour long-idle boundary. Goal staleness begins at M3's 24-hour mark.
NEED_ACTIVATION_THRESHOLD = 0.70
RECENT_USER_SUPPRESSION_SECONDS = IDLE_RECENT_SECONDS
MIN_ACTION_IDLE_SECONDS = IDLE_LONG_SECONDS
STALE_GOAL_SECONDS = GOAL_STALENESS_START_SECONDS
ACTIONABLE_DEADLINE_SECONDS = 24 * 60 * 60.0
DEADLINE_HORIZON_SECONDS = 7 * 24 * 60 * 60.0

def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def _latest_trigger(triggers: tuple[TriggerSignal, ...], trigger_type: TriggerType) -> TriggerSignal | None:
    matches = (item for item in triggers if item.trigger_type == trigger_type)
    return min(matches, key=lambda item: (
        -item.strength,
        -item.freshness,
        str(item.trigger_type),
        item.key,
        item.source,
        _utc(item.occurred_at).isoformat(),
        item.source_refs,
    ), default=None)


def _strong_needs(motivation: MotivationalSnapshot) -> tuple[NeedState, ...]:
    return tuple(sorted(
        (need for need in motivation.needs if need.activation >= NEED_ACTIVATION_THRESHOLD),
        key=lambda need: need.key,
    ))


def _active_goals(motivation: MotivationalSnapshot) -> tuple[GoalState, ...]:
    return tuple(goal for goal in motivation.goals if goal.status.casefold() == "active")


def _stale_goals(motivation: MotivationalSnapshot, now: datetime) -> tuple[GoalState, ...]:
    return tuple(sorted(
        (goal for goal in _active_goals(motivation)
         if max(0.0, (now - _utc(goal.updated_at)).total_seconds()) >= STALE_GOAL_SECONDS
         and (goal.activation >= NEED_ACTIVATION_THRESHOLD or goal.urgency >= NEED_ACTIVATION_THRESHOLD)),
        key=lambda goal: goal.goal_key,
    ))


def _deadline_goals(motivation: MotivationalSnapshot, now: datetime) -> tuple[tuple[GoalState, float], ...]:
    result: list[tuple[GoalState, float]] = []
    for goal in _active_goals(motivation):
        if goal.expires_at is None:
            continue
        remaining = (_utc(goal.expires_at) - now).total_seconds()
        if remaining <= DEADLINE_HORIZON_SECONDS:
            result.append((goal, max(0.0, min(1.0, 1.0 - max(0.0, remaining) / DEADLINE_HORIZON_SECONDS))))
    return tuple(sorted(result, key=lambda item: item[0].goal_key))


def _check_snapshot_times(
    motivation: MotivationalSnapshot, temporal: TemporalContext, triggers: TriggerSnapshot,
) -> datetime:
    now = _utc(temporal.now)
    if _utc(motivation.generated_at) != now or _utc(triggers.generated_at) != now:
        raise ValueError("autonomy_snapshots_must_share_generated_at")
    idle_seconds = float(temporal.idle_duration_seconds)
    idle_pressure = float(temporal.idle_pressure)
    if (not math.isfinite(idle_seconds) or idle_seconds < 0.0
            or not math.isfinite(idle_pressure) or not 0.0 <= idle_pressure <= 1.0):
        raise ValueError("autonomy_temporal_context_invalid")
    return now


def decide_autonomy(
    motivation: MotivationalSnapshot,
    temporal: TemporalContext,
    triggers: TriggerSnapshot,
) -> AutonomyDecision:
    """Return an explainable policy result; does not read clocks or perform I/O.

    The engine combines evidence classes rather than selecting a winning trigger:
    a strong Need, stale active Goal, and near deadline are independently
    evaluated; recent global user activity and insufficient idle retain policy
    precedence over any of them. ACT means "eligible for later consideration"
    and never invokes an action.
    """
    now = _check_snapshot_times(motivation, temporal, triggers)
    signals = tuple(sorted(triggers.triggers, key=lambda item: (
        str(item.trigger_type), item.key, item.source,
        -item.strength, -item.freshness, _utc(item.occurred_at).isoformat(), item.source_refs,
    )))
    active_signals = tuple(item for item in signals if item.strength > TRIGGER_ACTIVE_EPSILON)
    user_signal = _latest_trigger(active_signals, TriggerType.USER_ACTIVITY)
    system_signal = _latest_trigger(active_signals, TriggerType.SYSTEM_EVENT)
    need_signals = tuple(item for item in active_signals if item.trigger_type == TriggerType.NEED_ACTIVATION)

    strong_needs = _strong_needs(motivation)
    stale_goals = _stale_goals(motivation, now)
    deadlines = _deadline_goals(motivation, now)
    # A deadline inside one day is actionable context; overdue items remain
    # explicit pressure but are held for review below.
    actionable_deadline = tuple(
        item for item in deadlines
        if 0.0 < (_utc(item[0].expires_at) - now).total_seconds() <= ACTIONABLE_DEADLINE_SECONDS
    )
    user_age = (
        max(0.0, (now - _utc(user_signal.occurred_at)).total_seconds())
        if user_signal is not None else None
    )
    # Exclusive boundary: at exactly 15 minutes the M3 temporal projection
    # has left its `recent` band, so only younger activity suppresses.
    global_recent = user_age is not None and user_age < RECENT_USER_SUPPRESSION_SECONDS
    idle_seconds = max(0.0, float(temporal.idle_duration_seconds))
    long_idle = idle_seconds >= MIN_ACTION_IDLE_SECONDS
    enough_idle = long_idle
    has_motivation = bool(strong_needs or stale_goals or actionable_deadline)
    overdue_deadline = any(
        (_utc(goal.expires_at) - now).total_seconds() <= 0.0
        for goal, _ in deadlines
    )
    has_context_support = system_signal is not None

    reasons: set[Reason] = set()
    suppressions: set[Reason] = set()
    if user_signal is not None:
        reasons.add(Reason.RECENT_USER_ACTIVITY)
    if system_signal is not None:
        reasons.add(Reason.SYSTEM_EVENT)
    if long_idle:
        reasons.add(Reason.LONG_IDLE)
    else:
        reasons.add(Reason.INSUFFICIENT_IDLE)
    if strong_needs:
        reasons.add(Reason.STRONG_NEED)
    if stale_goals:
        reasons.add(Reason.STALE_GOAL)
    if deadlines:
        reasons.add(Reason.DEADLINE_PRESSURE)

    durable_activity = (
        temporal.conversation_has_activity
        or temporal.user_has_ever_spoken
        or temporal.persona_has_ever_spoken
    )
    if not durable_activity:
        reasons.add(Reason.NO_DURABLE_ACTIVITY)
        action = ActionClass.DO_NOT_ACT
        confidence = 1.0
    elif global_recent and (has_motivation or has_context_support):
        suppressions.add(Reason.RECENT_USER_ACTIVITY)
        action = ActionClass.DEFER
        confidence = 0.95
    elif overdue_deadline:
        # Deadline pressure remains visible, but an already-missed due date
        # does not authorize an immediate proactive action.
        action = ActionClass.DEFER
        confidence = 0.9
    elif not enough_idle:
        if has_motivation or has_context_support:
            action = ActionClass.DEFER
            confidence = 0.9
        else:
            action = ActionClass.DO_NOT_ACT
            confidence = 0.85
    elif has_motivation:
        action = ActionClass.ACT
        confidence = 0.9 if sum(bool(group) for group in (strong_needs, stale_goals, actionable_deadline)) == 1 else 0.95
    elif has_context_support:
        # A system event adds context but cannot independently authorize action.
        action = ActionClass.DEFER
        confidence = 0.75
    else:
        # Long idle opens an opportunity, but without motivation it is not
        # permission to initiate; DEFER preserves M4's explicit idle-only band.
        reasons.add(Reason.NO_ACTIONABLE_MOTIVATION)
        action = ActionClass.DEFER
        confidence = 0.8

    evidence_classes = sum(bool(group) for group in (strong_needs, stale_goals, deadlines, bool(system_signal)))
    if evidence_classes > 1 or len(strong_needs) > 1 or len(need_signals) > 1 or (global_recent and long_idle):
        reasons.add(Reason.CONFLICTING_SIGNALS)

    # Urgency is the strongest normalized urgency fact (not a composite score
    # and never an action threshold); confidence is a discrete policy-clarity
    # tier assigned by the branch that produced the decision, not probability.
    urgency_values = [temporal.idle_pressure]
    urgency_values.extend(need.urgency for need in strong_needs)
    urgency_values.extend(goal.urgency for goal in stale_goals)
    urgency_values.extend(score for _, score in deadlines)
    urgency_values.extend(signal.strength for signal in (user_signal, system_signal) if signal is not None)
    urgency = max((min(1.0, max(0.0, float(value))) for value in urgency_values), default=0.0)

    primary = min(active_signals, key=lambda item: (
        -item.strength, -item.freshness, str(item.trigger_type), item.key,
    ), default=None)
    factors = (
        f"activity={'present' if durable_activity else 'absent'}",
        f"idle_band={'long' if long_idle else 'under_two_hours'}",
        f"motivation_classes={evidence_classes}",
        f"global_recent_user={'yes' if global_recent else 'no'}",
    )
    return AutonomyDecision(
        action_class=action,
        reason_codes=tuple(reasons),
        suppression_reasons=tuple(suppressions),
        confidence=confidence,
        urgency=urgency,
        primary_trigger_type=primary.trigger_type if primary else None,
        primary_trigger_key=primary.key if primary else None,
        evaluated_at=now,
        decision_factors=factors,
    )
