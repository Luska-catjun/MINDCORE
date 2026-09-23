"""Pure M6 mapping from an authorized M5 ACT decision to one structured intent."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.models.autonomy_decision import (
    ActionClass,
    AutonomyDecision,
    AutonomyReasonCode as Reason,
)
from app.models.autonomy_intention import (
    AutonomyIntention,
    DEFAULT_INTENTION_CONSTRAINTS,
    IntentionType,
    TargetKind,
)
from app.models.motivation import GoalState, MotivationalSnapshot, NeedState
from app.models.temporal_context import TemporalContext
from app.models.trigger_context import TriggerSignal, TriggerSnapshot, TriggerType
from app.services.mindcore.autonomy_decision import (
    ACTIONABLE_DEADLINE_SECONDS,
    NEED_ACTIVATION_THRESHOLD,
    STALE_GOAL_SECONDS,
)
from app.services.mindcore.trigger_context import TRIGGER_ACTIVE_EPSILON
from app.services.mindcore.trigger_context import GOAL_DEADLINE_HORIZON_SECONDS


@dataclass(frozen=True, slots=True)
class _Candidate:
    intention_type: IntentionType
    target_kind: TargetKind
    target_key: str
    related_need_keys: tuple[str, ...]
    related_goal_keys: tuple[str, ...]
    source_signal: TriggerSignal | None
    relevant_reasons: tuple[Reason, ...]
    rank: tuple[object, ...]


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def _check_time_consistency(
    decision: AutonomyDecision,
    motivation: MotivationalSnapshot,
    temporal: TemporalContext,
    triggers: TriggerSnapshot,
) -> datetime:
    now = _utc(temporal.now)
    if any(_utc(value) != now for value in (
        decision.evaluated_at, motivation.generated_at, triggers.generated_at,
    )):
        raise ValueError("autonomy_intention_snapshots_must_share_generated_at")
    return now


def _active_goals(motivation: MotivationalSnapshot) -> tuple[GoalState, ...]:
    return tuple(goal for goal in motivation.goals if goal.status.casefold() == "active")


def _strong_needs(motivation: MotivationalSnapshot) -> tuple[NeedState, ...]:
    return tuple(need for need in motivation.needs if need.activation >= NEED_ACTIVATION_THRESHOLD)


def _trigger_for(
    triggers: tuple[TriggerSignal, ...],
    trigger_type: TriggerType,
    target_key: str,
) -> TriggerSignal | None:
    matches = (
        signal for signal in triggers
        if signal.trigger_type == trigger_type
        and (signal.key == target_key
             or target_key in signal.related_need_keys
             or target_key in signal.related_goal_keys)
    )
    return min(matches, key=lambda signal: (
        -signal.strength, -signal.freshness, str(signal.trigger_type), signal.key,
        signal.source, _utc(signal.occurred_at).isoformat(), signal.source_refs,
    ), default=None)


def _candidate_goals(
    motivation: MotivationalSnapshot,
    decision: AutonomyDecision,
    triggers: tuple[TriggerSignal, ...],
    now: datetime,
) -> tuple[list[_Candidate], list[_Candidate]]:
    reasons = set(decision.reason_codes)
    stale_candidates: list[_Candidate] = []
    deadline_candidates: list[_Candidate] = []
    active = _active_goals(motivation)
    stale_by_key: dict[str, GoalState] = {}
    if Reason.STALE_GOAL in reasons:
        for goal in active:
            age = max(0.0, (now - _utc(goal.updated_at)).total_seconds())
            if (age >= STALE_GOAL_SECONDS
                    and (goal.activation >= NEED_ACTIVATION_THRESHOLD
                         or goal.urgency >= NEED_ACTIVATION_THRESHOLD)):
                stale_by_key[goal.goal_key] = goal
                stale_candidates.append(_Candidate(
                    IntentionType.REVISIT_GOAL, TargetKind.GOAL, goal.goal_key,
                    (goal.origin_need,), (goal.goal_key,),
                    _trigger_for(triggers, TriggerType.GOAL_STALENESS, goal.goal_key),
                    (Reason.STALE_GOAL,),
                    (-goal.urgency, -age, goal.goal_key),
                ))
    if Reason.DEADLINE_PRESSURE in reasons:
        for goal in active:
            if goal.expires_at is None:
                continue
            remaining = (_utc(goal.expires_at) - now).total_seconds()
            if 0.0 < remaining <= ACTIONABLE_DEADLINE_SECONDS:
                age = max(0.0, (now - _utc(goal.updated_at)).total_seconds())
                related_reasons = [Reason.DEADLINE_PRESSURE]
                if goal.goal_key in stale_by_key:
                    related_reasons.append(Reason.STALE_GOAL)
                deadline_candidates.append(_Candidate(
                    IntentionType.REMIND_DEADLINE, TargetKind.GOAL, goal.goal_key,
                    (goal.origin_need,), (goal.goal_key,),
                    _trigger_for(triggers, TriggerType.GOAL_DEADLINE, goal.goal_key),
                    tuple(related_reasons),
                    (remaining, -goal.urgency, -age, goal.goal_key),
                ))
    return stale_candidates, deadline_candidates


def derive_autonomy_intention(
    decision: AutonomyDecision,
    motivation: MotivationalSnapshot,
    temporal: TemporalContext,
    triggers: TriggerSnapshot,
) -> AutonomyIntention | None:
    """Select exactly one intention only when M5 has already authorized ACT.

    This function does not re-evaluate action policy. M5's action class is the
    authority; M6 only checks timestamp coherence and resolves a content-free
    target from the corresponding reason and durable snapshot facts.
    """
    now = _check_time_consistency(decision, motivation, temporal, triggers)
    if decision.action_class != ActionClass.ACT:
        return None
    if decision.suppression_reasons:
        raise ValueError("autonomy_intention_act_has_suppression_reasons")

    signals = tuple(sorted(triggers.triggers, key=lambda signal: (
        str(signal.trigger_type), signal.key, signal.source,
        -signal.strength, -signal.freshness, _utc(signal.occurred_at).isoformat(), signal.source_refs,
    )))
    reasons = set(decision.reason_codes)
    stale_candidates, deadline_candidates = _candidate_goals(motivation, decision, signals, now)
    if Reason.STALE_GOAL in reasons and not stale_candidates:
        raise ValueError("autonomy_intention_stale_goal_target_missing")
    if Reason.STRONG_NEED in reasons and not _strong_needs(motivation):
        raise ValueError("autonomy_intention_need_target_missing")
    if Reason.SYSTEM_EVENT in reasons and not any(
        signal.trigger_type == TriggerType.SYSTEM_EVENT
        and signal.strength > TRIGGER_ACTIVE_EPSILON
        for signal in signals
    ):
        raise ValueError("autonomy_intention_system_event_target_missing")
    if Reason.DEADLINE_PRESSURE in reasons and not any(
        goal.expires_at is not None
        and (_utc(goal.expires_at) - now).total_seconds() <= GOAL_DEADLINE_HORIZON_SECONDS
        for goal in _active_goals(motivation)
    ):
        raise ValueError("autonomy_intention_deadline_target_missing")

    # Selection is a semantic hierarchy, not maximum trigger strength.
    if deadline_candidates:
        candidates = sorted(deadline_candidates, key=lambda item: item.rank)
        selected = candidates[0]
    elif stale_candidates:
        candidates = sorted(stale_candidates, key=lambda item: item.rank)
        selected = candidates[0]
    elif Reason.STRONG_NEED in reasons:
        needs = _strong_needs(motivation)
        if needs:
            need = min(needs, key=lambda item: (
                -item.activation, -item.urgency, -item.persistence, item.key,
            ))
            selected = _Candidate(
                IntentionType.CHECK_IN,
                TargetKind.NEED,
                need.key,
                (need.key,),
                tuple(need.related_goal_keys),
                _trigger_for(signals, TriggerType.NEED_ACTIVATION, need.key),
                (Reason.STRONG_NEED,),
                (-need.activation, -need.urgency, -need.persistence, need.key),
            )
        else:
            selected = None
    elif Reason.SYSTEM_EVENT in reasons:
        event = min(
            (signal for signal in signals
             if signal.trigger_type == TriggerType.SYSTEM_EVENT
             and signal.strength > TRIGGER_ACTIVE_EPSILON),
            key=lambda item: (-item.strength, -item.freshness, str(item.trigger_type), item.key),
            default=None,
        )
        selected = _Candidate(
            IntentionType.ACKNOWLEDGE_EVENT, TargetKind.SYSTEM_EVENT,
            event.key, event.related_need_keys, event.related_goal_keys, event,
            (Reason.SYSTEM_EVENT,),
            (-event.strength, -event.freshness, event.key),
        ) if event else None
    else:
        selected = None

    if selected is None:
        raise ValueError("autonomy_intention_act_has_no_actionable_target")

    propagated = set(selected.relevant_reasons)
    for contextual in (Reason.LONG_IDLE, Reason.CONFLICTING_SIGNALS):
        if contextual in reasons:
            propagated.add(contextual)
    return AutonomyIntention(
        intention_type=selected.intention_type,
        target_kind=selected.target_kind,
        target_key=selected.target_key,
        reason_codes=tuple(propagated),
        source_trigger_type=selected.source_signal.trigger_type if selected.source_signal else None,
        source_trigger_key=selected.source_signal.key if selected.source_signal else None,
        related_need_keys=selected.related_need_keys,
        related_goal_keys=selected.related_goal_keys,
        constraints=DEFAULT_INTENTION_CONSTRAINTS,
        urgency=decision.urgency,
        confidence=decision.confidence,
        evaluated_at=_utc(decision.evaluated_at),
        intention_key=f"{selected.intention_type}:{selected.target_key}",
    )
