"""Durable ownership for chat core completion and post-cognition stages.

The provider request is deliberately outside every transaction in this
module.  Database-only stage work is run on one leased connection and the
stage completion mark commits in that same outer transaction.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Awaitable, Callable
from uuid import UUID

from app.services import repository
from app.services.error_safety import safe_error_type


logger = logging.getLogger("diana.turn_durability")

MAX_STAGE_ATTEMPTS = 3
RUNNING_STAGE_STALE_AFTER = timedelta(minutes=5)


@dataclass(frozen=True)
class StageDefinition:
    name: str
    retry_policy: str


# Automatic recovery is limited to deterministic, database-only owners whose
# side effect is committed with the ledger mark. Other stages are still
# tracked durably, but require a future/manual replay with their original
# ephemeral inputs or an external model call.
POST_COGNITION_STAGES: tuple[StageDefinition, ...] = (
    StageDefinition("intention_persist", "manual"),
    StageDefinition("self_expression_goal", "manual"),
    StageDefinition("decision", "manual"),
    StageDefinition("decision_cancel", "automatic"),
    StageDefinition("memory_extraction", "manual"),
    StageDefinition("experience", "manual"),
    StageDefinition("emotion_attribution_link", "automatic"),
    StageDefinition("relationship", "automatic"),
    StageDefinition("preferences", "automatic"),
    StageDefinition("diana_preferences", "manual"),
    StageDefinition("consolidation", "manual"),
    StageDefinition("episode", "manual"),
    StageDefinition("decision_episode_link", "automatic"),
    StageDefinition("knowledge", "automatic"),
    StageDefinition("goal_fulfillment", "automatic"),
    StageDefinition("decision_execution", "automatic"),
    StageDefinition("goal_progress", "automatic"),
    StageDefinition("working_memory_post", "manual"),
    StageDefinition("narrative", "automatic"),
    StageDefinition("self_model", "automatic"),
)
STAGE_BY_NAME = {stage.name: stage for stage in POST_COGNITION_STAGES}


class StageNotClaimed(RuntimeError):
    """The stage is already complete, currently owned, or terminal."""


class _ConnectionBoundPool:
    """Expose one already-leased connection through the existing pool seam."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def _refresh_turn_status(connection: Any, turn_id: UUID | str) -> str:
    counts = await connection.fetchrow(
        """select
             sum(case when status='failed' then 1 else 0 end) as failed,
             sum(case when status!='completed' then 1 else 0 end) as incomplete
           from chat_turn_stages where turn_id=$1""",
        turn_id,
    )
    failed = int((counts or {}).get("failed") or 0)
    incomplete = int((counts or {}).get("incomplete") or 0)
    now = _utc_now()
    if failed:
        status = "partial"
        completed_at = None
    elif incomplete:
        status = "core_completed"
        completed_at = None
    else:
        status = "complete"
        completed_at = now
    await connection.execute(
        """update chat_turns set status=$1,updated_at=$2,completed_at=$3,
                  last_failed_stage=(select stage_name from chat_turn_stages
                    where turn_id=$4 and status='failed' order by started_at desc limit 1),
                  safe_error_category=(select last_error_category from chat_turn_stages
                    where turn_id=$4 and status='failed' order by started_at desc limit 1)
           where turn_id=$4 and core_completed_at is not null""",
        status,
        now,
        completed_at,
        turn_id,
    )
    return status


class TurnDurability:
    """Small application service around the durable turn/stage tables."""

    def __init__(self, pool: Any, *, enabled: bool | None = None) -> None:
        self.pool = pool
        # Legacy isolated unit fixtures deliberately have no durability schema.
        pool_supports_durability = bool(
            hasattr(pool, "acquire")
            and not getattr(pool, "isolated", False)
            and getattr(pool, "turn_durability_enabled", True)
        )
        self.enabled = pool_supports_durability if enabled is None else (
            pool_supports_durability and enabled
        )

    async def begin_turn(self, payload: Any) -> dict[str, Any]:
        if not self.enabled:
            return await repository.create_message(self.pool, payload)
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                message = await repository.create_message(_ConnectionBoundPool(connection), payload)
                now = _utc_now()
                await connection.execute(
                    """insert into chat_turns(
                         turn_id,conversation_id,user_message_id,assistant_message_id,status,
                         created_at,updated_at,core_completed_at,completed_at,last_failed_stage,safe_error_category
                       ) values($1,$2,$1,null,'pending',$3,$3,null,null,null,null)""",
                    message["id"],
                    payload.conversation_id,
                    now,
                )
        logger.info("TURN_LIFECYCLE turn=%s status=pending", message["id"])
        return message

    async def mark_core_failed(self, turn_id: UUID | str, error: BaseException) -> None:
        if not self.enabled:
            return
        category = safe_error_type(error)
        now = _utc_now()
        async with self.pool.acquire() as connection:
            await connection.execute(
                """update chat_turns set status='core_failed',updated_at=$1,
                         last_failed_stage='core',safe_error_category=$2
                   where turn_id=$3 and core_completed_at is null""",
                now,
                category,
                turn_id,
            )
        logger.warning("TURN_LIFECYCLE turn=%s status=core_failed category=%s", turn_id, category)

    async def complete_core(self, turn_id: UUID | str, assistant_payload: Any) -> dict[str, Any]:
        if not self.enabled:
            return await repository.create_message(self.pool, assistant_payload)
        try:
            async with self.pool.acquire() as connection:
                async with connection.transaction():
                    message = await repository.create_message(
                        _ConnectionBoundPool(connection), assistant_payload
                    )
                    now = _utc_now()
                    updated = await connection.fetchrow(
                        """update chat_turns set assistant_message_id=$1,status='core_completed',
                                  core_completed_at=$2,updated_at=$2,last_failed_stage=null,
                                  safe_error_category=null
                           where turn_id=$3 and core_completed_at is null
                           returning turn_id""",
                        message["id"],
                        now,
                        turn_id,
                    )
                    if updated is None:
                        raise RuntimeError("turn_core_completion_conflict")
                    for stage in POST_COGNITION_STAGES:
                        await connection.execute(
                            """insert into chat_turn_stages(
                                 turn_id,stage_name,status,retry_policy,attempt_count,
                                 started_at,completed_at,last_error_category
                               ) values($1,$2,'pending',$3,0,null,null,null)
                               on conflict(turn_id,stage_name) do nothing""",
                            turn_id,
                            stage.name,
                            stage.retry_policy,
                        )
        except BaseException as error:
            await self.mark_core_failed(turn_id, error)
            raise
        logger.info("TURN_LIFECYCLE turn=%s status=core_completed", turn_id)
        return message

    async def _claim_stage_with_connection(
        self, connection: Any, turn_id: UUID | str, stage_name: str
    ) -> bool:
        if stage_name not in STAGE_BY_NAME:
            raise ValueError("unknown_turn_stage")
        now = _utc_now()
        stale_before = now - RUNNING_STAGE_STALE_AFTER
        await connection.execute(
            """update chat_turn_stages set status='failed',last_error_category='interrupted'
               where turn_id=$1 and stage_name=$2 and status='running'
                 and started_at is not null and started_at<$3""",
            turn_id,
            stage_name,
            stale_before,
        )
        claimed = await connection.fetchrow(
            """update chat_turn_stages set status='running',attempt_count=attempt_count+1,
                      started_at=$1,completed_at=null,last_error_category=null
               where turn_id=$2 and stage_name=$3
                 and status in ('pending','failed') and attempt_count<$4
               returning attempt_count""",
            now,
            turn_id,
            stage_name,
            MAX_STAGE_ATTEMPTS,
        )
        return claimed is not None

    async def _claim_stage(self, turn_id: UUID | str, stage_name: str) -> bool:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                return await self._claim_stage_with_connection(
                    connection, turn_id, stage_name
                )

    async def _mark_stage_failed(
        self, turn_id: UUID | str, stage_name: str, error: BaseException,
        *, increment_attempt: bool = False,
    ) -> None:
        category = safe_error_type(error)
        now = _utc_now()
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """update chat_turn_stages set status='failed',last_error_category=$1,
                              attempt_count=attempt_count+$2
                       where turn_id=$3 and stage_name=$4
                         and status in ('pending','running','failed')""",
                    category,
                    1 if increment_attempt else 0,
                    turn_id, stage_name,
                )
                await _refresh_turn_status(connection, turn_id)
        logger.warning(
            "TURN_STAGE turn=%s stage=%s status=failed category=%s",
            turn_id,
            stage_name,
            category,
        )

    async def run_stage(
        self,
        turn_id: UUID | str,
        stage_name: str,
        operation: Callable[[Any], Awaitable[Any]],
        *,
        transactional: bool = True,
    ) -> Any:
        """Claim and execute one stage without changing best-effort callers."""
        if not self.enabled:
            return await operation(self.pool)
        try:
            if transactional:
                async with self.pool.acquire() as connection:
                    async with connection.transaction():
                        if not await self._claim_stage_with_connection(
                            connection, turn_id, stage_name
                        ):
                            raise StageNotClaimed(stage_name)
                        result = await operation(_ConnectionBoundPool(connection))
                        await connection.execute(
                            """update chat_turn_stages set status='completed',completed_at=$1,
                                      last_error_category=null
                               where turn_id=$2 and stage_name=$3 and status='running'""",
                            _utc_now(),
                            turn_id,
                            stage_name,
                        )
                        await _refresh_turn_status(connection, turn_id)
            else:
                # External/model or process-only work cannot be held inside a
                # DB transaction. Such stages are manual-retry in the schema.
                if not await self._claim_stage(turn_id, stage_name):
                    raise StageNotClaimed(stage_name)
                result = await operation(self.pool)
                async with self.pool.acquire() as connection:
                    async with connection.transaction():
                        await connection.execute(
                            """update chat_turn_stages set status='completed',completed_at=$1,
                                      last_error_category=null
                               where turn_id=$2 and stage_name=$3 and status='running'""",
                            _utc_now(), turn_id, stage_name,
                        )
                        await _refresh_turn_status(connection, turn_id)
        except StageNotClaimed:
            raise
        except BaseException as error:
            await self._mark_stage_failed(
                turn_id, stage_name, error, increment_attempt=transactional
            )
            raise
        logger.info("TURN_STAGE turn=%s stage=%s status=completed", turn_id, stage_name)
        return result

    async def complete_noop(self, turn_id: UUID | str, stage_name: str) -> None:
        async def noop(_pool: Any) -> None:
            return None

        try:
            await self.run_stage(turn_id, stage_name, noop)
        except StageNotClaimed:
            return

    async def get_turn(self, turn_id: UUID | str) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow("select * from chat_turns where turn_id=$1", turn_id)
        return dict(row) if row is not None else None

    async def get_stages(self, turn_id: UUID | str) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        async with self.pool.acquire() as connection:
            rows = await connection.fetch(
                "select * from chat_turn_stages where turn_id=$1 order by stage_name", turn_id
            )
        return [dict(row) for row in rows]

    async def incomplete_turn_ids(self, *, limit: int) -> list[str]:
        if not self.enabled:
            return []
        async with self.pool.acquire() as connection:
            rows = await connection.fetch(
                """select turn_id from chat_turns
                   where core_completed_at is not null and status in ('core_completed','partial')
                   order by updated_at asc limit $1""",
                limit,
            )
        return [str(row["turn_id"]) for row in rows]

    async def terminalize_missing_input(self, turn_id: UUID | str) -> None:
        """Stop automatic replay when deletion removed its message authority."""
        if not self.enabled:
            return
        now = _utc_now()
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """update chat_turn_stages
                       set status='failed',attempt_count=$1,last_error_category='missing_recovery_input'
                       where turn_id=$2 and status!='completed' and retry_policy='automatic'""",
                    MAX_STAGE_ATTEMPTS,
                    turn_id,
                )
                await connection.execute(
                    """update chat_turns set status='partial',updated_at=$1,
                              last_failed_stage='recovery',safe_error_category='missing_recovery_input'
                       where turn_id=$2 and core_completed_at is not null""",
                    now,
                    turn_id,
                )


async def stage_rows_for_recovery(
    durability: TurnDurability, turn_id: UUID | str
) -> list[dict[str, Any]]:
    """Return only bounded automatic candidates in canonical stage order."""
    rows = {str(row["stage_name"]): row for row in await durability.get_stages(turn_id)}
    return [
        rows[definition.name]
        for definition in POST_COGNITION_STAGES
        if definition.retry_policy == "automatic"
        and definition.name in rows
        and str(rows[definition.name]["status"]) != "completed"
        and int(rows[definition.name]["attempt_count"] or 0) < MAX_STAGE_ATTEMPTS
    ]
