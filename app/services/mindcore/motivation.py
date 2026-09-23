"""Read-only, deterministic projections of durable Need and Goal pressure.

Scoring contract (all scores are clamped to [0, 1]):

* Need effective value converges from the durable value toward its stored
  baseline with the existing per-Need half-life. Repeated recent events add a
  bounded persistence multiplier to that half-life.
* Need activation is the positive baseline-relative effective value divided
  by the remaining headroom above baseline. Need urgency is activation scaled
  by the linear recency of its last trigger over six hours.
* Goal importance is the existing durable priority. Activation combines
  priority/confidence, remaining progress, open status, and origin-Need
  activation. Urgency is elapsed fraction of its created-to-expiry interval;
  without an expiry it follows the origin Need activation.

Ordering is stable: Needs by activation DESC, urgency DESC, key ASC; Goals by
activation DESC, importance DESC, urgency DESC, goal_key ASC. Dominant keys are
the first three items with activation >= 0.20. These represent pressure only;
they are not an autonomy/action decision.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from typing import Any, Mapping, Sequence

import asyncpg

from app.models.motivation import (
    GoalState,
    MotivationEvidenceRef,
    MotivationalSnapshot,
    NeedState,
)
from app.services.mindcore.goals import BASELINES, HALF_LIVES


SCORE_MIN = 0.0
SCORE_MAX = 1.0
NEED_PERSISTENCE_WINDOW_DAYS = 30.0
NEED_PERSISTENCE_HALF_LIFE_DAYS = 7.0
NEED_PERSISTENCE_EVENT_SATURATION = 5.0
NEED_MAX_EVENTS_READ = 512
NEED_MAX_EVIDENCE_REFS = 8
NEED_URGENCY_HORIZON_HOURS = 6.0
UNKNOWN_NEED_HALF_LIFE_HOURS = 24.0
PERSISTENCE_HALF_LIFE_MULTIPLIER = 1.0
GOAL_CANDIDATE_ACTIVATION_FACTOR = 0.65
GOAL_PRIORITY_WEIGHT = 0.65
GOAL_CONFIDENCE_WEIGHT = 0.35
GOAL_ORIGIN_NEED_WEIGHT = 0.25
DOMINANT_ACTIVATION_THRESHOLD = 0.20
DOMINANT_MAX_ITEMS = 3
OPEN_GOAL_STATUSES = frozenset({"candidate", "active"})


def _clamp(value: Any, *, warning: str, warnings: set[str]) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        warnings.add(warning)
        return SCORE_MIN
    if not math.isfinite(numeric):
        warnings.add(warning)
        return SCORE_MIN
    bounded = max(SCORE_MIN, min(SCORE_MAX, numeric))
    if bounded != numeric:
        warnings.add(warning)
    return bounded


def _utc(value: Any, *, field: str, now: datetime, warnings: set[str]) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            warnings.add(f"invalid_timestamp:{field}")
            return now
    else:
        warnings.add(f"invalid_timestamp:{field}")
        return now
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        # Historical MindCore timestamps without an offset are UTC throughout
        # the existing persistence layer; retain that compatibility explicitly.
        warnings.add(f"naive_timestamp_normalized:{field}")
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _bounded_age_hours(when: datetime, now: datetime) -> float:
    return max(0.0, (now - when).total_seconds() / 3600.0)


def _persistence_for_events(
    events: Sequence[Mapping[str, Any]], *, now: datetime, need_key: str, warnings: set[str]
) -> float:
    weighted = 0.0
    cutoff = now - timedelta(days=NEED_PERSISTENCE_WINDOW_DAYS)
    for event in events:
        if str(event.get("need_key") or "") != need_key:
            continue
        created = _utc(event.get("created_at"), field="need_event_created_at", now=now, warnings=warnings)
        if created < cutoff:
            continue
        age_days = max(0.0, (now - created).total_seconds() / 86400.0)
        weighted += 0.5 ** (age_days / NEED_PERSISTENCE_HALF_LIFE_DAYS)
    return max(SCORE_MIN, min(SCORE_MAX, weighted / NEED_PERSISTENCE_EVENT_SATURATION))


def _need_event_refs(
    events: Sequence[Mapping[str, Any]], *, now: datetime, need_key: str, warnings: set[str]
) -> tuple[MotivationEvidenceRef, ...]:
    matching: list[tuple[datetime, MotivationEvidenceRef]] = []
    for event in events:
        if str(event.get("need_key") or "") != need_key or not event.get("id"):
            continue
        created = _utc(event.get("created_at"), field="need_event_created_at", now=now, warnings=warnings)
        matching.append((created, MotivationEvidenceRef(
            kind="need_event",
            id=str(event["id"]),
            source_type=str(event["source_type"]) if event.get("source_type") is not None else None,
            source_id=str(event["source_id"]) if event.get("source_id") is not None else None,
            occurred_at=created,
        )))
    matching.sort(key=lambda item: (-item[0].timestamp(), item[1].id))
    return tuple(ref for _, ref in matching[:NEED_MAX_EVIDENCE_REFS])


def _make_need_states(
    need_rows: Sequence[Mapping[str, Any]],
    need_event_rows: Sequence[Mapping[str, Any]],
    *,
    now: datetime,
    related_goal_keys: Mapping[str, tuple[str, ...]],
    warnings: set[str],
) -> tuple[NeedState, ...]:
    states: list[NeedState] = []
    seen: set[str] = set()
    for row in need_rows:
        key = str(row.get("need_key") or "")
        if not key:
            warnings.add("need_key_missing")
            continue
        if key not in BASELINES:
            warnings.add("unknown_need_key")
        if key in seen:
            warnings.add("duplicate_need_key")
            continue
        seen.add(key)
        default_baseline = BASELINES.get(key, SCORE_MIN)
        if row.get("baseline") is None:
            warnings.add("need_baseline_missing")
            baseline = default_baseline
        else:
            baseline = _clamp(row.get("baseline"), warning="need_baseline_clamped", warnings=warnings)
        value = _clamp(row.get("value"), warning="need_value_clamped", warnings=warnings)
        updated_at = _utc(row.get("updated_at"), field="need_updated_at", now=now, warnings=warnings)
        event_refs = _need_event_refs(need_event_rows, now=now, need_key=key, warnings=warnings)
        last_triggered_value = row.get("last_triggered_at")
        last_triggered_at = (
            _utc(last_triggered_value, field="need_last_triggered_at", now=now, warnings=warnings)
            if last_triggered_value is not None
            else (event_refs[0].occurred_at if event_refs else None)
        )
        persistence = _persistence_for_events(need_event_rows, now=now, need_key=key, warnings=warnings)
        half_life = HALF_LIVES.get(key, UNKNOWN_NEED_HALF_LIFE_HOURS) * (
            1.0 + PERSISTENCE_HALF_LIFE_MULTIPLIER * persistence
        )
        effective = baseline + (value - baseline) * 0.5 ** (
            _bounded_age_hours(updated_at, now) / half_life
        )
        effective = max(SCORE_MIN, min(SCORE_MAX, effective))
        headroom = 1.0 - baseline
        activation = (
            max(0.0, effective - baseline) / headroom
            if headroom > 0.0
            else 0.0
        )
        trigger_recency = (
            max(0.0, 1.0 - _bounded_age_hours(last_triggered_at, now) / NEED_URGENCY_HORIZON_HOURS)
            if last_triggered_at is not None
            else 0.0
        )
        states.append(NeedState(
            key=key,
            baseline=baseline,
            effective=effective,
            activation=activation,
            urgency=activation * trigger_recency,
            persistence=persistence,
            last_triggered_at=last_triggered_at,
            related_goal_keys=related_goal_keys.get(key, ()),
            evidence_refs=event_refs,
        ))
    return tuple(sorted(states, key=lambda item: (-item.activation, -item.urgency, item.key)))


def _goal_refs(row: Mapping[str, Any], *, created_at: datetime) -> tuple[MotivationEvidenceRef, ...]:
    source_id = row.get("source_id")
    return (MotivationEvidenceRef(
        kind="goal_source",
        id=str(row.get("id") or row.get("goal_key") or "unknown"),
        source_type=str(row["source_type"]) if row.get("source_type") is not None else None,
        source_id=str(source_id) if source_id is not None else None,
        occurred_at=created_at,
    ),)


def _make_goal_states(
    goal_rows: Sequence[Mapping[str, Any]],
    *,
    now: datetime,
    need_states: Mapping[str, NeedState],
    warnings: set[str],
) -> tuple[GoalState, ...]:
    states: list[GoalState] = []
    seen: set[str] = set()
    for row in goal_rows:
        key = str(row.get("goal_key") or "")
        if not key:
            warnings.add("goal_key_missing")
            continue
        if key in seen:
            warnings.add("duplicate_goal_key")
            continue
        seen.add(key)
        status = str(row.get("status") or "unknown").strip().casefold()
        if status not in {"candidate", "active", "satisfied", "abandoned", "expired"}:
            warnings.add("unknown_goal_status")
        priority = _clamp(row.get("priority"), warning="goal_priority_clamped", warnings=warnings)
        confidence = _clamp(row.get("confidence"), warning="goal_confidence_clamped", warnings=warnings)
        progress = _clamp(row.get("progress"), warning="goal_progress_clamped", warnings=warnings)
        origin_need = str(row.get("origin_need") or "")
        if not origin_need:
            warnings.add("goal_origin_need_missing")
            continue
        if origin_need not in need_states:
            warnings.add("goal_origin_need_unavailable")
        origin_activation = need_states.get(origin_need).activation if origin_need in need_states else 0.0
        created_at = _utc(row.get("created_at"), field="goal_created_at", now=now, warnings=warnings)
        updated_at = _utc(row.get("updated_at"), field="goal_updated_at", now=now, warnings=warnings)
        expires_at = (
            _utc(row.get("expires_at"), field="goal_expires_at", now=now, warnings=warnings)
            if row.get("expires_at") is not None
            else None
        )
        if expires_at is None:
            urgency = origin_activation
        elif now >= expires_at:
            urgency = 1.0
        elif now <= created_at:
            urgency = 0.0
        else:
            duration = (expires_at - created_at).total_seconds()
            urgency = (
                max(0.0, min(1.0, (now - created_at).total_seconds() / duration))
                if duration > 0
                else 1.0
            )
        status_factor = (
            1.0 if status == "active"
            else GOAL_CANDIDATE_ACTIVATION_FACTOR if status == "candidate"
            else 0.0
        )
        confidence_weighted_importance = (
            GOAL_PRIORITY_WEIGHT * priority + GOAL_CONFIDENCE_WEIGHT * confidence
        )
        activation = status_factor * (
            confidence_weighted_importance * (1.0 - progress)
            + GOAL_ORIGIN_NEED_WEIGHT * origin_activation
        )
        states.append(GoalState(
            goal_key=key,
            status=status,
            priority=priority,
            importance=priority,
            urgency=urgency,
            activation=activation,
            origin_need=origin_need,
            created_at=created_at,
            updated_at=updated_at,
            expires_at=expires_at,
            evidence_refs=_goal_refs(row, created_at=created_at),
        ))
    return tuple(sorted(
        states,
        key=lambda item: (-item.activation, -item.importance, -item.urgency, item.goal_key),
    ))


def build_motivational_snapshot(
    *,
    need_rows: Sequence[Mapping[str, Any]],
    need_event_rows: Sequence[Mapping[str, Any]],
    goal_rows: Sequence[Mapping[str, Any]],
    now: datetime,
) -> MotivationalSnapshot:
    """Purely project durable rows at ``now``; never reads clocks or writes storage."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("motivational_snapshot_now_must_be_timezone_aware")
    current = now.astimezone(timezone.utc)
    warnings: set[str] = set()
    related: dict[str, list[str]] = {}
    for row in goal_rows:
        if str(row.get("status") or "").strip().casefold() in OPEN_GOAL_STATUSES:
            key = str(row.get("origin_need") or "")
            goal_key = str(row.get("goal_key") or "")
            if key and goal_key:
                related.setdefault(key, []).append(goal_key)
    related_goal_keys = {
        key: tuple(sorted(set(goal_keys))) for key, goal_keys in related.items()
    }
    needs = _make_need_states(
        need_rows,
        need_event_rows,
        now=current,
        related_goal_keys=related_goal_keys,
        warnings=warnings,
    )
    need_by_key = {need.key: need for need in needs}
    goals = _make_goal_states(goal_rows, now=current, need_states=need_by_key, warnings=warnings)
    dominant_needs = tuple(
        item.key for item in needs if item.activation >= DOMINANT_ACTIVATION_THRESHOLD
    )[:DOMINANT_MAX_ITEMS]
    dominant_goals = tuple(
        item.goal_key for item in goals if item.activation >= DOMINANT_ACTIVATION_THRESHOLD
    )[:DOMINANT_MAX_ITEMS]
    return MotivationalSnapshot(
        generated_at=current,
        needs=needs,
        goals=goals,
        dominant_need_keys=dominant_needs,
        dominant_goal_keys=dominant_goals,
        data_warnings=tuple(sorted(warnings)),
    )


async def get_motivational_snapshot(
    pool: asyncpg.Pool, *, now: datetime
) -> MotivationalSnapshot:
    """Load the durable inputs and return a read-only projection at fixed ``now``.

    This does not call ``get_need_snapshot`` because that existing helper may
    repair missing canonical Need rows. A motivation read must never provision
    or otherwise mutate durable state.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("motivational_snapshot_now_must_be_timezone_aware")
    current = now.astimezone(timezone.utc)
    async with pool.acquire() as connection:
        need_rows = await connection.fetch(
            "select need_key,value,baseline,updated_at,last_triggered_at from diana_needs order by need_key"
        )
        need_event_rows = await connection.fetch(
            """select id,need_key,before_value,after_value,source_type,source_id,created_at
               from diana_need_events order by created_at desc limit $1""",
            NEED_MAX_EVENTS_READ,
        )
        goal_rows = await connection.fetch(
            """select id,goal_key,origin_need,priority,status,progress,confidence,
                      source_type,source_id,created_at,updated_at,expires_at
               from diana_goals order by goal_key"""
        )
    return build_motivational_snapshot(
        need_rows=need_rows,
        need_event_rows=need_event_rows,
        goal_rows=goal_rows,
        now=current,
    )
