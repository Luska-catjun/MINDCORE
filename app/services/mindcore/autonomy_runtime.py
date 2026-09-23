"""M8 active-backend autonomy cycle and conservative safety policy.

The scheduler is deliberately a small wait/clock/call loop.  This module owns
one deterministic orchestration at a caller-supplied aware ``now`` and stores
only ephemeral coordination state; cooldown and ignored suppression are
derived from durable turn/message provenance.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
import logging
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from app.config import Settings
from app.models.autonomy_decision import ActionClass
from app.models.proactive_execution import ProactiveExecutionResult
from app.services.mindcore.autonomy_decision import decide_autonomy
from app.services.mindcore.autonomy_intention import derive_autonomy_intention
from app.services.mindcore.proactive_execution import (
    ProactiveExecutionError,
    execute_proactive_intention,
)
from app.services.mindcore.autonomy_execution_store import (
    get_execution_gate,
)
from app.services.mindcore.trigger_context import get_trigger_snapshot


logger = logging.getLogger("diana.autonomy.runtime")
EVALUATION_INTERVAL_SECONDS = 60  # M3's smallest meaningful idle unit is minutes.
STARTUP_GRACE_SECONDS = 120
IGNORED_COOLDOWN_MULTIPLIERS = (1, 2, 4)
MAX_IGNORED_STREAK = 3
FAILURE_BACKOFF_SECONDS = (60, 120, 300, 900)
MAX_FAILURE_BACKOFF_SECONDS = 900


@dataclass(frozen=True, slots=True)
class DurableAutonomyState:
    conversation_id: str | None
    latest_user_message_id: str | None
    latest_user_activity_at: datetime | None
    last_proactive_at: datetime | None
    ignored_streak: int


@dataclass(frozen=True, slots=True)
class AutonomyCycleResult:
    status: str
    reason: str | None = None
    action_class: str | None = None
    intention_key: str | None = None
    execution: ProactiveExecutionResult | None = None


@dataclass(slots=True)
class AutonomyRuntimeState:
    started_at: datetime
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    dirty: bool = False
    last_successful_intention_key: str | None = None
    last_successful_intention_at: datetime | None = None
    last_successful_intention_user_message_id: str | None = None
    consecutive_failures: int = 0
    retry_after: datetime | None = None
    failure_user_message_id: str | None = None
    current_user_message_id: str | None = None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("autonomy_now_must_be_timezone_aware")
    return value.astimezone(timezone.utc)


def _parse_clock(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def is_quiet_time(settings: Settings, now: datetime) -> bool:
    if not settings.proactive_quiet_hours_enabled:
        return False
    local_now = _utc(now).astimezone(ZoneInfo(settings.diana_timezone)).timetz().replace(tzinfo=None)
    start = _parse_clock(settings.proactive_quiet_start)
    end = _parse_clock(settings.proactive_quiet_end)
    if start < end:
        return start <= local_now < end
    # Overnight interval, e.g. 23:00 through but excluding 07:00.
    return local_now >= start or local_now < end


async def load_durable_autonomy_state(pool: Any) -> DurableAutonomyState:
    """Select the latest user-active conversation and durable suppression facts."""
    async with pool.acquire() as connection:
        user = await connection.fetchrow(
            """select id,conversation_id,created_at from messages
               where role='user' order by created_at desc,id desc limit 1"""
        )
        if user is None:
            return DurableAutonomyState(None, None, None, None, 0)
        conversation_id = str(user["conversation_id"])
        last_user_at = _as_datetime(user["created_at"])
        last_proactive = await connection.fetchrow(
            """select message.created_at from chat_turns turn
               join messages message on message.id=turn.assistant_message_id
               where turn.initiator_actor='persona'
                 and turn.trigger_type='autonomy_decision'
                 and turn.input_source='internal'
                 and turn.status='complete'
               order by message.created_at desc,message.id desc limit 1"""
        )
        ignored_rows = await connection.fetch(
            """select message.id,message.created_at from chat_turns turn
               join messages message on message.id=turn.assistant_message_id
               where turn.initiator_actor='persona'
                 and turn.trigger_type='autonomy_decision'
                 and turn.input_source='internal'
                 and turn.status='complete'
                 and (message.created_at > $1 or (message.created_at = $1 and message.id > $2))
               order by message.created_at desc,message.id desc limit $3""",
            user["created_at"], str(user["id"]), MAX_IGNORED_STREAK,
        )
    return DurableAutonomyState(
        conversation_id=conversation_id,
        latest_user_message_id=str(user["id"]),
        latest_user_activity_at=last_user_at,
        last_proactive_at=_as_datetime(last_proactive["created_at"]) if last_proactive else None,
        ignored_streak=len(ignored_rows),
    )


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def effective_cooldown_seconds(base: int, ignored_streak: int) -> int | None:
    if ignored_streak >= MAX_IGNORED_STREAK:
        return None
    multiplier = IGNORED_COOLDOWN_MULTIPLIERS[max(0, ignored_streak)]
    return base * multiplier


async def _still_eligible(
    pool: Any,
    settings: Settings,
    now: datetime,
    runtime: AutonomyRuntimeState,
    intention_key: str,
    expected_user_message_id: str,
) -> bool:
    """Cheap final gate run inside M7 immediately before provider invocation."""
    current = _utc(now)
    if not settings.proactive_enabled or is_quiet_time(settings, current):
        return False
    state = await load_durable_autonomy_state(pool)
    if state.latest_user_message_id != expected_user_message_id:
        return False
    if state.ignored_streak >= MAX_IGNORED_STREAK:
        return False
    cooldown = effective_cooldown_seconds(settings.proactive_cooldown_seconds, state.ignored_streak)
    if cooldown is None or (state.last_proactive_at and
            (current - state.last_proactive_at).total_seconds() < cooldown):
        return False
    if runtime.last_successful_intention_key == intention_key and (
        runtime.last_successful_intention_user_message_id == state.latest_user_message_id
    ):
        return False
    return True


async def _run_cycle_once(
    *,
    pool: Any,
    settings: Settings,
    identity_prompt: str,
    now: datetime,
    runtime: AutonomyRuntimeState,
    executor: Callable[..., Awaitable[ProactiveExecutionResult]] = execute_proactive_intention,
) -> AutonomyCycleResult:
    current = _utc(now)
    if not settings.proactive_enabled:
        return AutonomyCycleResult("skipped", "disabled")
    if (current - _utc(runtime.started_at)).total_seconds() < STARTUP_GRACE_SECONDS:
        return AutonomyCycleResult("skipped", "startup_grace")
    if is_quiet_time(settings, current):
        return AutonomyCycleResult("skipped", "quiet_hours")
    durable = await load_durable_autonomy_state(pool)
    runtime.current_user_message_id = durable.latest_user_message_id
    if runtime.retry_after is not None and current < runtime.retry_after:
        if durable.latest_user_message_id == runtime.failure_user_message_id:
            return AutonomyCycleResult("skipped", "failure_backoff")
        # Meaningful durable user activity clears only ephemeral failure
        # backoff, never the success cooldown or ignored-response policy.
        runtime.consecutive_failures = 0
        runtime.retry_after = None
        runtime.failure_user_message_id = None
    if durable.conversation_id is None or durable.latest_user_message_id is None:
        return AutonomyCycleResult("skipped", "no_user_conversation")
    cooldown = effective_cooldown_seconds(settings.proactive_cooldown_seconds, durable.ignored_streak)
    if cooldown is None:
        return AutonomyCycleResult("skipped", "ignored_streak")
    if durable.last_proactive_at is not None and (
        current - durable.last_proactive_at
    ).total_seconds() < cooldown:
        return AutonomyCycleResult("skipped", "cooldown")

    temporal, motivation, triggers = await get_trigger_snapshot(
        pool,
        timezone_name=settings.diana_timezone,
        now=current,
        conversation_id=durable.conversation_id,
    )
    decision = decide_autonomy(motivation, temporal, triggers)
    if decision.action_class != ActionClass.ACT:
        return AutonomyCycleResult("evaluated", action_class=str(decision.action_class))
    intention = derive_autonomy_intention(
        decision, motivation, temporal, triggers, persona_id=settings.persona_id,
    )
    if intention is None:
        return AutonomyCycleResult("evaluated", "no_intention", str(decision.action_class))
    if runtime.last_successful_intention_key == intention.intention_key and (
        runtime.last_successful_intention_user_message_id == durable.latest_user_message_id
    ):
        return AutonomyCycleResult("skipped", "same_intention", str(decision.action_class), intention.intention_key)

    durable_blocked, durable_retry_after = await get_execution_gate(
        pool,
        persona_id=str(settings.persona_id),
        intention_key=intention.intention_key,
        user_activity_anchor_message_id=durable.latest_user_message_id,
        now=current,
    )
    if durable_blocked:
        return AutonomyCycleResult(
            "skipped", "same_intention_durable", str(decision.action_class), intention.intention_key
        )
    if durable_retry_after is not None and current < durable_retry_after:
        return AutonomyCycleResult(
            "skipped", "failure_backoff_durable", str(decision.action_class), intention.intention_key
        )

    guard = lambda: _still_eligible(
        pool, settings, current, runtime, intention.intention_key,
        durable.latest_user_message_id or "",
    )
    try:
        result = await executor(
            pool=pool,
            settings=settings,
            conversation_id=durable.conversation_id,
            intention=intention,
            identity_prompt=identity_prompt,
            now=current,
            pre_provider_guard=guard,
            autonomy_context={
                "user_activity_anchor_message_id": durable.latest_user_message_id,
            },
        )
    except ProactiveExecutionError as error:
        if error.category == "autonomy_execution_duplicate":
            return AutonomyCycleResult(
                "skipped", "same_intention_durable", str(decision.action_class), intention.intention_key
            )
        if error.category == "execution_gate_changed":
            return AutonomyCycleResult("skipped", "user_or_policy_changed", str(decision.action_class), intention.intention_key)
        raise
    runtime.last_successful_intention_key = intention.intention_key
    runtime.last_successful_intention_at = result.created_at
    runtime.last_successful_intention_user_message_id = durable.latest_user_message_id
    runtime.consecutive_failures = 0
    runtime.retry_after = None
    runtime.failure_user_message_id = None
    return AutonomyCycleResult("executed", action_class=str(decision.action_class), intention_key=intention.intention_key, execution=result)


async def run_autonomy_cycle(
    *,
    pool: Any,
    settings: Settings,
    identity_prompt: str,
    now: datetime,
    runtime: AutonomyRuntimeState,
    executor: Callable[..., Awaitable[ProactiveExecutionResult]] = execute_proactive_intention,
) -> AutonomyCycleResult:
    """Run one cycle at one fixed logical time; concurrent requests coalesce."""
    _utc(now)
    if runtime.lock.locked():
        runtime.dirty = True
        return AutonomyCycleResult("coalesced", "evaluation_in_flight")
    async with runtime.lock:
        runtime.dirty = False
        try:
            result = await _run_cycle_once(
                pool=pool, settings=settings, identity_prompt=identity_prompt,
                now=now, runtime=runtime, executor=executor,
            )
            if runtime.dirty:
                # Coalesce any number of concurrent requests into at most one
                # bounded re-evaluation. Durable cooldown and intention gates
                # prevent a successful first pass from producing another turn.
                runtime.dirty = False
                result = await _run_cycle_once(
                    pool=pool, settings=settings, identity_prompt=identity_prompt,
                    now=now, runtime=runtime, executor=executor,
                )
            logger.info("AUTONOMY_CYCLE status=%s reason=%s action=%s intention=%s",
                        result.status, result.reason or "none", result.action_class or "none",
                        result.intention_key or "none")
            return result
        except asyncio.CancelledError:
            raise
        except Exception as error:
            runtime.consecutive_failures += 1
            delay = FAILURE_BACKOFF_SECONDS[min(runtime.consecutive_failures - 1, len(FAILURE_BACKOFF_SECONDS) - 1)]
            delay = min(delay, MAX_FAILURE_BACKOFF_SECONDS)
            runtime.retry_after = _utc(now) + timedelta(seconds=delay)
            runtime.failure_user_message_id = runtime.current_user_message_id
            logger.warning("AUTONOMY_CYCLE status=failed category=%s retry_seconds=%s",
                           type(error).__name__, delay)
            return AutonomyCycleResult("failed", type(error).__name__)


async def run_autonomy_scheduler(
    *,
    pool: Any,
    settings: Settings,
    identity_prompt: str,
    runtime: AutonomyRuntimeState,
    sleeper: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> None:
    """Active backend lifetime only: wait, acquire one aware time, run."""
    while True:
        await sleeper(EVALUATION_INTERVAL_SECONDS)
        now = clock()
        _utc(now)
        await run_autonomy_cycle(
            pool=pool, settings=settings, identity_prompt=identity_prompt,
            now=now, runtime=runtime,
        )
