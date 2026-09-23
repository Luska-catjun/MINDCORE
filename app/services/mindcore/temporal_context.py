"""Bounded durable activity loading and pure temporal/idle projection."""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import asyncpg

from app.models.temporal_context import (
    ActivityPoint,
    Daypart,
    IdleLevel,
    TemporalContext,
    TemporalInputs,
)


# Local-hour partitions are presentation facts only; they never authorize action.
DAYPART_BOUNDARIES = (5, 12, 17, 21)
IDLE_ACTIVE_SECONDS = 2 * 60
IDLE_RECENT_SECONDS = 15 * 60
IDLE_LONG_SECONDS = 2 * 60 * 60
IDLE_PRESSURE_START_SECONDS = 2 * 60
IDLE_PRESSURE_SATURATION_SECONDS = 24 * 60 * 60
IDLE_PRESSURE_CURVE = 3.0
TEMPORAL_ACTIVITY_READ_LIMIT = 256


def _aware_utc(value: Any, *, field: str, warnings: set[str]) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            warnings.add(f"invalid_timestamp:{field}")
            return None
    if not isinstance(value, datetime):
        warnings.add(f"invalid_timestamp:{field}")
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        # Legacy SQLite timestamp strings are UTC by the storage contract.
        warnings.add(f"legacy_naive_timestamp_assumed_utc:{field}")
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _daypart(local_now: datetime) -> Daypart:
    hour = local_now.hour
    if hour < DAYPART_BOUNDARIES[0]:
        return "late_night"
    if hour < DAYPART_BOUNDARIES[1]:
        return "morning"
    if hour < DAYPART_BOUNDARIES[2]:
        return "afternoon"
    if hour < DAYPART_BOUNDARIES[3]:
        return "evening"
    return "night"


def _idle_level(seconds: float) -> IdleLevel:
    if seconds < IDLE_ACTIVE_SECONDS:
        return "active"
    if seconds < IDLE_RECENT_SECONDS:
        return "recent"
    if seconds < IDLE_LONG_SECONDS:
        return "idle"
    return "long_idle"


def _idle_pressure(seconds: float) -> float:
    elapsed = max(0.0, seconds - IDLE_PRESSURE_START_SECONDS)
    span = IDLE_PRESSURE_SATURATION_SECONDS - IDLE_PRESSURE_START_SECONDS
    if elapsed <= 0:
        return 0.0
    if elapsed >= span:
        return 1.0
    # Normalize an exponential saturation curve so it is smooth, monotonic,
    # and reaches exactly one at the centralized 24-hour horizon.
    ratio = elapsed / span
    denominator = 1.0 - math.exp(-IDLE_PRESSURE_CURVE)
    return max(0.0, min(1.0, (1.0 - math.exp(-IDLE_PRESSURE_CURVE * ratio)) / denominator))


def compute_temporal_context(
    inputs: TemporalInputs,
    *,
    timezone_name: str,
    now: datetime,
) -> TemporalContext:
    """Pure deterministic temporal projection at a caller-supplied aware time."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("temporal_context_now_must_be_timezone_aware")
    current = now.astimezone(timezone.utc)
    try:
        local_now = current.astimezone(ZoneInfo(timezone_name))
    except (ZoneInfoNotFoundError, ValueError, TypeError) as error:
        raise ValueError("temporal_context_timezone_invalid") from error

    warnings = set(inputs.data_warnings)
    latest: dict[str, ActivityPoint] = {}
    for point in inputs.activity:
        occurred_at = point.occurred_at.astimezone(timezone.utc)
        if occurred_at > current:
            warnings.add("future_activity_timestamp_clamped_for_age")
        previous = latest.get(point.actor)
        point_order = (occurred_at, point.provenance == "message_role")
        previous_order = (
            (previous.occurred_at.astimezone(timezone.utc), previous.provenance == "message_role")
            if previous is not None else None
        )
        if (
            previous is None
            or point_order > previous_order
            or (point_order == previous_order and point.source_ref < previous.source_ref)
        ):
            latest[point.actor] = point
    user_point = latest.get("user")
    persona_point = latest.get("persona")
    system_point = latest.get("system")
    last_user = user_point.occurred_at.astimezone(timezone.utc) if user_point else None
    last_persona = persona_point.occurred_at.astimezone(timezone.utc) if persona_point else None
    last_system = system_point.occurred_at.astimezone(timezone.utc) if system_point else None
    last_conversation = max((point.occurred_at.astimezone(timezone.utc) for point in latest.values()), default=None)

    def age(value: datetime | None) -> float:
        return max(0.0, (current - value).total_seconds()) if value is not None else 0.0

    idle_duration = age(last_conversation)
    return TemporalContext(
        now=current,
        timezone=timezone_name,
        local_now=local_now,
        local_date=local_now.date(),
        daypart=_daypart(local_now),
        last_user_activity_at=last_user,
        last_persona_activity_at=last_persona,
        last_system_activity_at=last_system,
        last_conversation_activity_at=last_conversation,
        last_user_activity_source_ref=user_point.source_ref if user_point else None,
        last_persona_activity_source_ref=persona_point.source_ref if persona_point else None,
        last_system_activity_source_ref=system_point.source_ref if system_point else None,
        seconds_since_user_activity=age(last_user),
        seconds_since_persona_activity=age(last_persona),
        seconds_since_system_activity=age(last_system),
        seconds_since_conversation_activity=age(last_conversation),
        idle_duration_seconds=idle_duration,
        idle_level=_idle_level(idle_duration),
        idle_pressure=_idle_pressure(idle_duration),
        conversation_has_activity=last_conversation is not None,
        user_has_ever_spoken=inputs.user_has_ever_spoken,
        persona_has_ever_spoken=inputs.persona_has_ever_spoken,
        data_warnings=tuple(sorted(warnings)),
    )


async def load_temporal_inputs(
    pool: asyncpg.Pool,
    *,
    conversation_id: str | None = None,
) -> TemporalInputs:
    """Read recent activity and exact ever-spoken flags without message bodies.

    Each activity stream returns at most ``TEMPORAL_ACTIVITY_READ_LIMIT`` rows.
    The SQL predicates and selected columns are fixed; optional conversation
    scope is bound as a value. M1 turn provenance is read directly, while old
    messages use their durable role as the deterministic fallback.
    """
    async with pool.acquire() as connection:
        if conversation_id is None:
            turns = await connection.fetch(
                """select turn_id,initiator_actor,created_at from chat_turns
                   order by created_at desc,turn_id desc limit $1""",
                TEMPORAL_ACTIVITY_READ_LIMIT,
            )
            messages = await connection.fetch(
                """select id,role,created_at from messages
                   where role in ('user','diana','assistant')
                   order by created_at desc,id desc limit $1""",
                TEMPORAL_ACTIVITY_READ_LIMIT,
            )
            flags = await connection.fetchrow(
                """select exists(select 1 from messages where role='user') as user_spoke,
                          exists(select 1 from messages where role in ('diana','assistant')) as persona_spoke"""
            )
        else:
            turns = await connection.fetch(
                """select turn_id,initiator_actor,created_at from chat_turns
                   where conversation_id=$1 order by created_at desc,turn_id desc limit $2""",
                conversation_id,
                TEMPORAL_ACTIVITY_READ_LIMIT,
            )
            messages = await connection.fetch(
                """select id,role,created_at from messages
                   where conversation_id=$1 and role in ('user','diana','assistant')
                   order by sequence desc,id desc limit $2""",
                conversation_id,
                TEMPORAL_ACTIVITY_READ_LIMIT,
            )
            flags = await connection.fetchrow(
                """select exists(select 1 from messages where conversation_id=$1 and role='user') as user_spoke,
                          exists(select 1 from messages where conversation_id=$1 and role in ('diana','assistant')) as persona_spoke""",
                conversation_id,
            )

    warnings: set[str] = set()
    points: list[ActivityPoint] = []
    for row in turns:
        actor = str(row.get("initiator_actor") or "")
        if actor not in {"user", "persona", "system"}:
            warnings.add("invalid_turn_actor")
            continue
        occurred_at = _aware_utc(row.get("created_at"), field="turn_created_at", warnings=warnings)
        if occurred_at is not None and row.get("turn_id"):
            points.append(ActivityPoint(actor, occurred_at, str(row["turn_id"]), "turn_context"))

    for row in messages:
        role = str(row.get("role") or "")
        actor = "user" if role == "user" else "persona" if role in {"diana", "assistant"} else None
        if actor is None:
            continue
        occurred_at = _aware_utc(row.get("created_at"), field="message_created_at", warnings=warnings)
        if occurred_at is not None and row.get("id"):
            points.append(ActivityPoint(actor, occurred_at, str(row["id"]), "message_role"))
    return TemporalInputs(
        activity=tuple(points),
        user_has_ever_spoken=bool(flags and flags.get("user_spoke")),
        persona_has_ever_spoken=bool(flags and flags.get("persona_spoke")),
        data_warnings=tuple(sorted(warnings)),
    )


async def get_temporal_context(
    pool: asyncpg.Pool,
    *,
    timezone_name: str,
    now: datetime,
    conversation_id: str | None = None,
) -> TemporalContext:
    """Load bounded persisted activity and calculate its temporal projection."""
    inputs = await load_temporal_inputs(pool, conversation_id=conversation_id)
    return compute_temporal_context(inputs, timezone_name=timezone_name, now=now)
