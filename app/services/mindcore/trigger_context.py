"""Pure trigger projections from temporal and motivational snapshots.

Signals remain separate. Their deterministic order is representational only;
this module intentionally has no scoring aggregate, scheduler, or action API.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import asyncpg

from app.models.motivation import MotivationalSnapshot
from app.models.temporal_context import TemporalContext
from app.models.trigger_context import TriggerSignal, TriggerSnapshot, TriggerType
from app.services.mindcore.motivation import get_motivational_snapshot
from app.services.mindcore.temporal_context import get_temporal_context


USER_ACTIVITY_FRESHNESS_HALF_LIFE_SECONDS = 5 * 60.0
NEED_FRESHNESS_HALF_LIFE_SECONDS = 6 * 60 * 60.0
NEED_PERSISTENCE_FRESHNESS_MULTIPLIER = 4.0
SYSTEM_EVENT_FRESHNESS_HALF_LIFE_SECONDS = 30 * 60.0
GOAL_STALENESS_START_SECONDS = 24 * 60 * 60.0
GOAL_STALENESS_SATURATION_SECONDS = 14 * 24 * 60 * 60.0
GOAL_STALENESS_FRESHNESS_HALF_LIFE_SECONDS = 30 * 24 * 60 * 60.0
GOAL_STALENESS_PERSISTENCE_HORIZON_SECONDS = 7 * 24 * 60 * 60.0
GOAL_DEADLINE_HORIZON_SECONDS = 7 * 24 * 60 * 60.0
TRIGGER_ACTIVE_EPSILON = 0.001
STRONGEST_TRIGGER_LIMIT = 3
TERMINAL_GOAL_STATUSES = frozenset({"satisfied", "abandoned", "expired"})


def _freshness(age_seconds: float, half_life_seconds: float) -> float:
    return max(0.0, min(1.0, 0.5 ** (max(0.0, age_seconds) / half_life_seconds)))


def _age(now: datetime, occurred_at: datetime) -> float:
    return max(0.0, (now - occurred_at).total_seconds())


def _signal(
    *, trigger_type: TriggerType, source: str, key: str, strength: float,
    freshness: float, persistence: float, occurred_at: datetime, now: datetime,
    related_need_keys: tuple[str, ...] = (), related_goal_keys: tuple[str, ...] = (),
    source_refs: tuple[str, ...] = (),
) -> TriggerSignal:
    age = _age(now, occurred_at)
    return TriggerSignal(
        trigger_type=trigger_type,
        source=source,
        key=key,
        strength=max(0.0, min(1.0, strength)),
        freshness=freshness,
        persistence=persistence,
        occurred_at=occurred_at.astimezone(timezone.utc),
        age_seconds=age,
        related_need_keys=related_need_keys,
        related_goal_keys=related_goal_keys,
        source_refs=source_refs,
    )


def _coalesce(signals: list[TriggerSignal]) -> list[TriggerSignal]:
    """Collapse duplicate semantic keys without losing source references."""
    groups: dict[tuple[TriggerType, str], list[TriggerSignal]] = {}
    for signal in signals:
        groups.setdefault((signal.trigger_type, signal.key), []).append(signal)
    merged: list[TriggerSignal] = []
    for group in groups.values():
        strongest = max(group, key=lambda item: (
            item.strength,
            item.freshness,
            item.persistence,
            item.source,
            item.occurred_at.isoformat(),
            item.source_refs,
        ))
        merged.append(replace(
            strongest,
            source_refs=tuple(sorted({ref for item in group for ref in item.source_refs})),
            related_need_keys=tuple(sorted({key for item in group for key in item.related_need_keys})),
            related_goal_keys=tuple(sorted({key for item in group for key in item.related_goal_keys})),
        ))
    return merged


def compute_trigger_snapshot(
    *,
    temporal: TemporalContext,
    motivation: MotivationalSnapshot,
) -> TriggerSnapshot:
    """Purely derive normalized triggers at the shared snapshot time."""
    now = temporal.now.astimezone(timezone.utc)
    if motivation.generated_at.astimezone(timezone.utc) != now:
        raise ValueError("trigger_inputs_must_share_generated_at")
    signals: list[TriggerSignal] = []
    warnings = set(temporal.data_warnings) | set(motivation.data_warnings)

    user_at = temporal.last_user_activity_at
    if user_at is not None:
        age = _age(now, user_at)
        fresh = _freshness(age, USER_ACTIVITY_FRESHNESS_HALF_LIFE_SECONDS)
        strength = fresh
        if strength > TRIGGER_ACTIVE_EPSILON:
            signals.append(_signal(
                trigger_type=TriggerType.USER_ACTIVITY,
                source="durable_user_message",
                key="latest_user_activity",
                strength=strength,
                freshness=fresh,
                persistence=0.15,
                occurred_at=user_at,
                now=now,
                source_refs=(temporal.last_user_activity_source_ref or "messages",),
            ))

    if temporal.conversation_has_activity and temporal.idle_pressure > TRIGGER_ACTIVE_EPSILON:
        occurred = temporal.last_conversation_activity_at
        assert occurred is not None
        age = _age(now, occurred)
        signals.append(_signal(
            trigger_type=TriggerType.IDLE_TIME,
            source="durable_conversation_activity",
            key="conversation_idle",
            strength=temporal.idle_pressure,
            freshness=1.0,
            persistence=max(0.0, min(1.0, age / GOAL_STALENESS_PERSISTENCE_HORIZON_SECONDS)),
            occurred_at=occurred,
            now=now,
            source_refs=tuple(ref for ref in (
                temporal.last_user_activity_source_ref,
                temporal.last_persona_activity_source_ref,
                temporal.last_system_activity_source_ref,
            ) if ref is not None),
        ))

    for need in motivation.needs:
        if need.activation <= TRIGGER_ACTIVE_EPSILON:
            continue
        evidence_time = need.last_triggered_at or next(
            (ref.occurred_at for ref in need.evidence_refs if ref.occurred_at is not None), None
        )
        if evidence_time is None:
            warnings.add("need_trigger_timestamp_unavailable")
            continue
        age = _age(now, evidence_time)
        signals.append(_signal(
            trigger_type=TriggerType.NEED_ACTIVATION,
            source="motivational_snapshot",
            key=need.key,
            strength=need.activation,
            freshness=_freshness(
                age,
                NEED_FRESHNESS_HALF_LIFE_SECONDS
                * (1.0 + NEED_PERSISTENCE_FRESHNESS_MULTIPLIER * need.persistence),
            ),
            persistence=need.persistence,
            occurred_at=evidence_time,
            now=now,
            related_need_keys=(need.key,),
            related_goal_keys=need.related_goal_keys,
            source_refs=tuple(ref.id for ref in need.evidence_refs),
        ))

    for goal in motivation.goals:
        if goal.status.casefold() in TERMINAL_GOAL_STATUSES:
            continue
        staleness_age = _age(now, goal.updated_at)
        staleness_strength = max(0.0, min(1.0,
            (staleness_age - GOAL_STALENESS_START_SECONDS)
            / (GOAL_STALENESS_SATURATION_SECONDS - GOAL_STALENESS_START_SECONDS)
        ))
        if staleness_strength > TRIGGER_ACTIVE_EPSILON:
            signals.append(_signal(
                trigger_type=TriggerType.GOAL_STALENESS,
                source="durable_goal_updated_at",
                key=goal.goal_key,
                strength=staleness_strength,
                freshness=_freshness(staleness_age, GOAL_STALENESS_FRESHNESS_HALF_LIFE_SECONDS),
                persistence=max(0.0, min(1.0, staleness_age / GOAL_STALENESS_PERSISTENCE_HORIZON_SECONDS)),
                occurred_at=goal.updated_at,
                now=now,
                related_need_keys=(goal.origin_need,),
                related_goal_keys=(goal.goal_key,),
                source_refs=tuple(ref.id for ref in goal.evidence_refs),
            ))
        if goal.expires_at is not None:
            seconds_to_deadline = (goal.expires_at - now).total_seconds()
            closeness = max(0.0, min(1.0,
                1.0 - max(0.0, seconds_to_deadline) / GOAL_DEADLINE_HORIZON_SECONDS
            ))
            if seconds_to_deadline <= 0:
                closeness = 1.0
            if closeness > TRIGGER_ACTIVE_EPSILON:
                occurred = min(goal.expires_at, now)
                age = _age(now, occurred)
                signals.append(_signal(
                    trigger_type=TriggerType.GOAL_DEADLINE,
                    source="durable_goal_expires_at",
                    key=goal.goal_key,
                    strength=closeness,
                    freshness=_freshness(max(0.0, seconds_to_deadline), GOAL_DEADLINE_HORIZON_SECONDS),
                    persistence=0.75,
                    occurred_at=occurred,
                    now=now,
                    related_need_keys=(goal.origin_need,),
                    related_goal_keys=(goal.goal_key,),
                    source_refs=tuple(ref.id for ref in goal.evidence_refs),
                ))

    system_at = temporal.last_system_activity_at
    if system_at is not None:
        age = _age(now, system_at)
        fresh = _freshness(age, SYSTEM_EVENT_FRESHNESS_HALF_LIFE_SECONDS)
        if fresh > TRIGGER_ACTIVE_EPSILON:
            signals.append(_signal(
                trigger_type=TriggerType.SYSTEM_EVENT,
                source="durable_system_turn",
                key="latest_system_activity",
                strength=fresh,
                freshness=fresh,
                persistence=0.3,
                occurred_at=system_at,
                now=now,
                source_refs=(temporal.last_system_activity_source_ref or "chat_turns",),
            ))

    signals = _coalesce(signals)
    signals.sort(key=lambda item: (-item.strength, -item.freshness, str(item.trigger_type), item.key))
    strongest = signals[:STRONGEST_TRIGGER_LIMIT]
    return TriggerSnapshot(
        generated_at=now,
        triggers=tuple(signals),
        strongest_trigger_types=tuple(item.trigger_type for item in strongest),
        strongest_trigger_keys=tuple(item.key for item in strongest),
        has_active_triggers=any(item.strength > TRIGGER_ACTIVE_EPSILON for item in signals),
        data_warnings=tuple(sorted(warnings)),
    )


async def get_trigger_snapshot(
    pool: asyncpg.Pool,
    *,
    timezone_name: str,
    now: datetime,
    conversation_id: str | None = None,
) -> tuple[TemporalContext, MotivationalSnapshot, TriggerSnapshot]:
    """Build read-only temporal, motivational, and trigger views on demand."""
    temporal = await get_temporal_context(
        pool, timezone_name=timezone_name, now=now, conversation_id=conversation_id
    )
    motivation = await get_motivational_snapshot(pool, now=temporal.now)
    triggers = compute_trigger_snapshot(temporal=temporal, motivation=motivation)
    return temporal, motivation, triggers
