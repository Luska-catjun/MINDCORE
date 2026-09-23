"""Durable idempotency and crash-recovery authority for autonomous turns.

This ledger is intentionally separate from foreground response intentions.
It stores identifiers and lifecycle metadata only; it never stores prompts or
provider content. A partial unique index arbitrates same-intention/same-anchor
reservations across processes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from typing import Any
from uuid import UUID, uuid4


logger = logging.getLogger("diana.autonomy.recovery")
BLOCKING_STATUSES = (
    "RESERVED", "PROVIDER_STARTED", "MESSAGE_PERSISTED", "COMPLETE", "INDETERMINATE"
)
RECOVERABLE_STATUSES = ("RESERVED", "PROVIDER_STARTED", "MESSAGE_PERSISTED")
FAILURE_BACKOFF_SECONDS = (60, 120, 300, 900)
RECOVERY_BATCH_LIMIT = 100
NO_BACKOFF_CATEGORIES = frozenset({"eligibility_changed"})


def _stamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("autonomy_execution_time_must_be_aware")
    return value.astimezone(timezone.utc).isoformat()


def _safe_error_category(error: BaseException) -> str:
    category = getattr(error, "category", None)
    allowed = {
        "provider_failure", "context_prepare_failure", "eligibility_changed",
        "execution_failure", "execution_gate_changed", "provider_invalid_response",
    }
    return category if isinstance(category, str) and category in allowed else "execution_failure"


@dataclass(frozen=True, slots=True)
class AutonomyExecutionReservation:
    execution_id: UUID
    turn_id: UUID


async def reserve_autonomy_execution(
    pool: Any,
    *,
    conversation_id: UUID | str,
    persona_id: str,
    intention: Any,
    user_activity_anchor_message_id: str,
    now: datetime,
) -> AutonomyExecutionReservation | None:
    """Atomically reserve a durable execution key before creating its turn."""
    execution_id, turn_id = uuid4(), uuid4()
    async with pool.acquire() as connection:
        return await reserve_autonomy_execution_with_connection(
            connection,
            conversation_id=conversation_id,
            persona_id=persona_id,
            intention=intention,
            user_activity_anchor_message_id=user_activity_anchor_message_id,
            now=now,
            execution_id=execution_id,
            turn_id=turn_id,
        )


async def reserve_autonomy_execution_with_connection(
    connection: Any,
    *,
    conversation_id: UUID | str,
    persona_id: str,
    intention: Any,
    user_activity_anchor_message_id: str,
    now: datetime,
    execution_id: UUID | None = None,
    turn_id: UUID | None = None,
) -> AutonomyExecutionReservation | None:
    """Insert a reservation on a caller-owned connection/transaction."""
    execution_id, turn_id = execution_id or uuid4(), turn_id or uuid4()
    timestamp = _stamp(now)
    cursor = await connection.execute(
        """insert into autonomy_executions(
             execution_id,turn_id,conversation_id,persona_id,intention_key,
             intention_type,target_kind,target_key,user_activity_anchor_message_id,
             status,assistant_message_id,safe_error_category,provider_started_at,
             message_persisted_at,completed_at,created_at,updated_at
           ) values($1,$2,$3,$4,$5,$6,$7,$8,$9,'RESERVED',null,null,null,null,null,$10,$10)
           on conflict do nothing""",
        execution_id, turn_id, conversation_id, persona_id,
        intention.intention_key, str(intention.intention_type),
        str(intention.target_kind), intention.target_key,
        user_activity_anchor_message_id, timestamp,
    )
    inserted = getattr(cursor, "rowcount", None)
    if inserted not in (0, 1):
        raise RuntimeError("autonomy_execution_reservation_rowcount_unavailable")
    if inserted == 0:
        return None
    return AutonomyExecutionReservation(execution_id, turn_id)


async def mark_provider_started(pool: Any, execution_id: UUID | str, *, now: datetime) -> None:
    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """update autonomy_executions set status='PROVIDER_STARTED',
                      provider_started_at=$1,updated_at=$1
               where execution_id=$2 and status='RESERVED' returning execution_id""",
            _stamp(now), execution_id,
        )
    if row is None:
        raise RuntimeError("autonomy_execution_provider_transition_conflict")


async def mark_failed_safe(
    pool: Any, execution_id: UUID | str, error: BaseException, *, now: datetime,
    safe_category: str | None = None,
) -> None:
    category = safe_category or _safe_error_category(error)
    if category not in {
        "provider_failure", "provider_invalid_response", "turn_creation_failure",
        "context_prepare_failure", "eligibility_changed", "execution_failure"
    }:
        category = "execution_failure"
    async with pool.acquire() as connection:
        await connection.execute(
            """update autonomy_executions set status='FAILED_SAFE',safe_error_category=$1,
                      updated_at=$2
               where execution_id=$3 and status in ('RESERVED','PROVIDER_STARTED')""",
            category, _stamp(now), execution_id,
        )


async def mark_indeterminate(
    pool: Any, execution_id: UUID | str, *, now: datetime, category: str = "recovery_ambiguous"
) -> None:
    if category not in {"recovery_ambiguous", "assistant_persist_failed"}:
        category = "recovery_ambiguous"
    async with pool.acquire() as connection:
        await connection.execute(
            """update autonomy_executions set status='INDETERMINATE',safe_error_category=$1,
                      updated_at=$2
               where execution_id=$3 and status in ('RESERVED','PROVIDER_STARTED','MESSAGE_PERSISTED')""",
            category, _stamp(now), execution_id,
        )


async def mark_message_persisted(
    connection: Any,
    execution_id: UUID | str,
    message_id: UUID | str,
    *,
    now: datetime,
) -> None:
    """Join assistant persistence to the caller's message/turn transaction."""
    timestamp = _stamp(now)
    row = await connection.fetchrow(
        """update autonomy_executions set status='MESSAGE_PERSISTED',assistant_message_id=$1,
                  message_persisted_at=$2,updated_at=$2
           where execution_id=$3 and status='PROVIDER_STARTED' returning execution_id""",
        message_id, timestamp, execution_id,
    )
    if row is None:
        raise RuntimeError("autonomy_execution_message_transition_conflict")


async def mark_complete(pool: Any, execution_id: UUID | str, *, now: datetime) -> None:
    async with pool.acquire() as connection:
        async with connection.transaction():
            row = await connection.fetchrow(
                """update autonomy_executions set status='COMPLETE',completed_at=$1,updated_at=$1
                   where execution_id=$2 and status='MESSAGE_PERSISTED' returning execution_id""",
                _stamp(now), execution_id,
            )
    if row is None:
        raise RuntimeError("autonomy_execution_completion_conflict")


async def get_execution_gate(
    pool: Any,
    *,
    persona_id: str,
    intention_key: str,
    user_activity_anchor_message_id: str,
    now: datetime,
) -> tuple[bool, datetime | None]:
    """Return durable dedupe and deterministic retry-backoff state."""
    async with pool.acquire() as connection:
        active = await connection.fetchrow(
            """select execution_id from autonomy_executions
               where persona_id=$1 and intention_key=$2
                 and user_activity_anchor_message_id=$3
                 and status in ('RESERVED','PROVIDER_STARTED','MESSAGE_PERSISTED','COMPLETE','INDETERMINATE')
               limit 1""",
            persona_id, intention_key, user_activity_anchor_message_id,
        )
        failures = await connection.fetch(
            """select updated_at,safe_error_category from autonomy_executions
               where persona_id=$1 and user_activity_anchor_message_id=$2
                 and status='FAILED_SAFE'
                 and coalesce(safe_error_category,'') not in ('eligibility_changed')
               order by updated_at desc,execution_id desc limit $3""",
            persona_id, user_activity_anchor_message_id, len(FAILURE_BACKOFF_SECONDS),
        )
    retry_after = None
    if failures:
        count = len(failures)
        delay = FAILURE_BACKOFF_SECONDS[min(count - 1, len(FAILURE_BACKOFF_SECONDS) - 1)]
        failed_at = datetime.fromisoformat(str(failures[0]["updated_at"]).replace("Z", "+00:00"))
        if failed_at.tzinfo is None or failed_at.utcoffset() is None:
            failed_at = failed_at.replace(tzinfo=timezone.utc)
        retry_after = failed_at.astimezone(timezone.utc) + timedelta(seconds=delay)
        if retry_after <= now.astimezone(timezone.utc):
            retry_after = None
    return active is not None, retry_after


async def recover_incomplete_autonomy_executions(
    pool: Any,
    *,
    persona_id: str,
    now: datetime,
    limit: int = RECOVERY_BATCH_LIMIT,
) -> dict[str, int]:
    """Reconcile a bounded batch without ever invoking a provider or creating messages."""
    timestamp = _stamp(now)
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """select execution_id,turn_id,conversation_id,persona_id,status,assistant_message_id
               from autonomy_executions
               where persona_id=$1 and status in ('RESERVED','PROVIDER_STARTED','MESSAGE_PERSISTED')
               order by created_at,execution_id limit $2""",
            persona_id, min(max(1, int(limit)), RECOVERY_BATCH_LIMIT),
        )
    counts = {status: 0 for status in (*RECOVERABLE_STATUSES, "COMPLETE", "FAILED_SAFE", "INDETERMINATE")}
    for execution in rows:
        execution_id = str(execution["execution_id"])
        current_status = str(execution["status"])
        outcome = "INDETERMINATE"
        assistant_id: str | None = None
        async with pool.acquire() as connection:
            turn = await connection.fetchrow(
                """select turn_id,conversation_id,user_message_id,assistant_message_id,status,
                          core_completed_at,initiator_actor,trigger_type,input_source
                   from chat_turns where turn_id=$1""",
                execution["turn_id"],
            )
            if current_status == "RESERVED":
                # A stale intent is never resumed. A valid pre-provider turn
                # is terminalized below; malformed linkage remains ambiguous.
                if turn is None:
                    outcome = "FAILED_SAFE"
                elif (
                    str(turn["conversation_id"]) == str(execution["conversation_id"])
                    and turn["user_message_id"] is None
                    and turn["assistant_message_id"] is None
                    and turn["core_completed_at"] is None
                    and str(turn["status"]) == "pending"
                    and (turn["initiator_actor"], turn["trigger_type"], turn["input_source"])
                        == ("persona", "autonomy_decision", "internal")
                ):
                    stages = await connection.fetch(
                        "select stage_name,status from chat_turn_stages where turn_id=$1",
                        execution["turn_id"],
                    )
                    stage_statuses = {str(row["stage_name"]): str(row["status"]) for row in stages}
                    valid_stages = (
                        set(stage_statuses) == {"context_prepare", "provider_generate", "assistant_persist"}
                        and stage_statuses["provider_generate"] in {"pending", "running", "failed"}
                        and stage_statuses["assistant_persist"] in {"pending", "failed"}
                        and stage_statuses["context_prepare"] in {"pending", "running", "completed", "failed"}
                    )
                    outcome = "FAILED_SAFE" if valid_stages else "INDETERMINATE"
                else:
                    outcome = "INDETERMINATE"
            elif (
                turn is not None
                and str(turn["conversation_id"]) == str(execution["conversation_id"])
                and str(execution["persona_id"]) == persona_id
                and str(turn["status"]) == "complete"
                and turn["user_message_id"] is None
                and (turn["initiator_actor"], turn["trigger_type"], turn["input_source"])
                    == ("persona", "autonomy_decision", "internal")
                and turn["assistant_message_id"] is not None
                and (
                    execution["assistant_message_id"] is None
                    or str(turn["assistant_message_id"]) == str(execution["assistant_message_id"])
                )
            ):
                message = await connection.fetchrow(
                    """select id,conversation_id,role,source_device from messages where id=$1""",
                    turn["assistant_message_id"],
                )
                if (
                    message is not None
                    and str(message["conversation_id"]) == str(execution["conversation_id"])
                    and str(message["role"]) == "diana"
                    and str(message["source_device"]) == "mindcore_proactive"
                ):
                    outcome = "COMPLETE"
                    assistant_id = str(message["id"])
            elif turn is None and current_status == "RESERVED":
                outcome = "FAILED_SAFE"
        async with pool.acquire() as connection:
            async with connection.transaction():
                if outcome == "COMPLETE":
                    await connection.execute(
                        """update autonomy_executions set status='COMPLETE',assistant_message_id=$1,
                                  message_persisted_at=coalesce(message_persisted_at,$2),
                                  completed_at=$2,updated_at=$2,safe_error_category=null
                           where execution_id=$3 and status in ('PROVIDER_STARTED','MESSAGE_PERSISTED')""",
                        assistant_id, timestamp, execution_id,
                    )
                elif outcome == "FAILED_SAFE":
                    await connection.execute(
                        """update autonomy_executions set status='FAILED_SAFE',
                                  safe_error_category='recovered_before_provider',updated_at=$1
                           where execution_id=$2 and status='RESERVED'""",
                        timestamp, execution_id,
                    )
                    if current_status == "RESERVED" and turn is not None:
                        await connection.execute(
                            """update chat_turn_stages set status='failed',
                                      completed_at=coalesce(completed_at,$1),
                                      last_error_category='recovered_before_provider'
                               where turn_id=$2 and status in ('pending','running')""",
                            timestamp, execution["turn_id"],
                        )
                        await connection.execute(
                            """update chat_turns set status='core_failed',updated_at=$1,
                                      last_failed_stage='recovery',
                                      safe_error_category='recovered_before_provider'
                               where turn_id=$2 and status='pending' and core_completed_at is null""",
                            timestamp, execution["turn_id"],
                        )
                else:
                    await connection.execute(
                        """update autonomy_executions set status='INDETERMINATE',
                                  safe_error_category='recovery_ambiguous',updated_at=$1
                           where execution_id=$2 and status in ('RESERVED','PROVIDER_STARTED','MESSAGE_PERSISTED')""",
                        timestamp, execution_id,
                    )
        counts[outcome] += 1
        logger.info("AUTONOMY_RECOVERY execution=%s status=%s category=%s",
                    execution_id, outcome, "reconciled" if outcome == "COMPLETE" else "safe")
    return counts
