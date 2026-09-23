from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

import libsql

from app.config import Settings
from app.database.turso import TursoConnection, _bind
from app.models.proactive_execution import ProactiveExecutionResult
from app.models.autonomy_decision import ActionClass, AutonomyDecision, AutonomyReasonCode
from app.models.autonomy_intention import (
    AutonomyIntention, DEFAULT_INTENTION_CONSTRAINTS, IntentionType, TargetKind,
)
from app.models.trigger_context import TriggerType
from app.services.mindcore.autonomy_runtime import AutonomyRuntimeState, run_autonomy_cycle
from app.services.mindcore.proactive_execution import execute_proactive_intention
from app.services.mindcore.autonomy_execution_store import recover_incomplete_autonomy_executions


ROOT = Path(__file__).resolve().parents[2]
BASELINE_SQL = (ROOT / "db" / "turso" / "baseline_v1.sql").read_text(encoding="utf-8")


def stable_policy_test(test):
    @wraps(test)
    async def wrapped(self):
        with self.stable_policy():
            await test(self)
    return wrapped


class LocalPool:
    def __init__(self, path: Path) -> None:
        self.path = str(path)

    @asynccontextmanager
    async def acquire(self):
        raw = await asyncio.to_thread(libsql.connect, database=self.path)
        cursor = await asyncio.to_thread(raw.execute, "PRAGMA busy_timeout=5000")
        await asyncio.to_thread(cursor.close)
        try:
            yield TursoConnection(raw)
        finally:
            await asyncio.to_thread(raw.close)


class _NativeStatementConnection:
    """Keep one native statement/commit lifecycle on one worker thread."""

    def __init__(self, raw) -> None:
        self.raw = raw

    async def execute(self, statement, *args):
        sql, bound = _bind(statement, args)

        def execute_and_commit():
            cursor = self.raw.execute(sql, bound)
            rowcount = cursor.rowcount
            if self.raw.in_transaction:
                self.raw.commit()
            cursor.close()
            return type("Result", (), {"rowcount": rowcount})()

        return await asyncio.to_thread(execute_and_commit)


class PreopenedLocalPool(LocalPool):
    """Two native connections opened before a concurrent file-DB write test."""

    def __init__(self, path: Path, count: int = 2) -> None:
        super().__init__(path)
        self._connections = [libsql.connect(database=self.path) for _ in range(count)]
        self._available: asyncio.Queue = asyncio.Queue()
        for connection in self._connections:
            connection.execute("PRAGMA busy_timeout=5000").close()
            self._available.put_nowait(_NativeStatementConnection(connection))

    @asynccontextmanager
    async def acquire(self):
        connection = await self._available.get()
        try:
            yield connection
        finally:
            self._available.put_nowait(connection)

    def close(self) -> None:
        for connection in self._connections:
            connection.close()


async def _execute_script(pool: LocalPool, sql: str) -> None:
    async with pool.acquire() as connection:
        async with connection.transaction():
            for statement in sql.split(";"):
                if statement.strip():
                    await connection.execute(statement)


class AutonomyRuntimeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = TemporaryDirectory()
        database_path = Path(self.temp.name) / "autonomy-runtime.db"
        self.pool = LocalPool(database_path)
        raw = await asyncio.to_thread(libsql.connect, database=str(database_path))
        try:
            cursor = await asyncio.to_thread(raw.execute, "PRAGMA journal_mode=WAL")
            await asyncio.to_thread(cursor.fetchall)
            await asyncio.to_thread(cursor.close)
        finally:
            await asyncio.to_thread(raw.close)
        await _execute_script(self.pool, BASELINE_SQL)
        # M7's durable message timestamps are produced by the repository's
        # normal clock, so anchor this end-to-end file-DB fixture to UTC now.
        self.now = datetime.now(timezone.utc)
        self.conversation_id = uuid4()
        self.user_message_id = str(uuid4())
        async with self.pool.acquire() as connection:
            await connection.execute(
                """insert into conversations(conversation_id,source_device,started_at,ended_at)
                   values($1,'test',$2,null)""",
                self.conversation_id, (self.now - timedelta(hours=3)).isoformat(),
            )
            await connection.execute(
                """insert into messages(id,conversation_id,sequence,role,content,source_device,created_at)
                   values($1,$2,1,'user','synthetic test input','test',$3)""",
                self.user_message_id, self.conversation_id,
                (self.now - timedelta(hours=3)).isoformat(),
            )
            await connection.execute(
                """insert into diana_needs(need_key,value,baseline,updated_at,last_triggered_at)
                   values('curiosity',.99,.4,$1,$1)""",
                (self.now - timedelta(hours=3)).isoformat(),
            )
        self.provider_calls = 0
        self.settings = Settings(
            _env_file=None, persona_id="persona-a", persona_display_name="Test Persona",
            proactive_enabled=True, proactive_quiet_hours_enabled=False,
            proactive_cooldown_seconds=300, diana_timezone="Asia/Seoul",
        )
        self.runtime = AutonomyRuntimeState(started_at=self.now - timedelta(minutes=3))

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    async def _provider(self, *_args, **_kwargs):
        self.provider_calls += 1
        return "Synthetic proactive test response"

    async def _fake_executor(self, **kwargs):
        return await execute_proactive_intention(**kwargs, provider=self._provider)

    @contextmanager
    def stable_policy(self):
        """Keep the M5/M6 result fixed while testing M9 persistence authority."""
        decision = AutonomyDecision(
            action_class=ActionClass.ACT,
            reason_codes=(AutonomyReasonCode.STRONG_NEED,),
            suppression_reasons=(), confidence=0.9, urgency=0.9,
            primary_trigger_type=TriggerType.NEED_ACTIVATION,
            primary_trigger_key="curiosity", evaluated_at=self.now,
        )
        intention = AutonomyIntention(
            intention_type=IntentionType.CHECK_IN, target_kind=TargetKind.NEED,
            target_key="curiosity", reason_codes=(AutonomyReasonCode.STRONG_NEED,),
            source_trigger_type=TriggerType.NEED_ACTIVATION,
            source_trigger_key="curiosity", related_need_keys=("curiosity",),
            related_goal_keys=(), constraints=DEFAULT_INTENTION_CONSTRAINTS,
            urgency=0.9, confidence=0.9, evaluated_at=self.now,
            intention_key="CHECK_IN:curiosity", persona_id="persona-a",
        )
        with patch(
            "app.services.mindcore.autonomy_runtime.decide_autonomy",
            side_effect=lambda _motivation, temporal, _triggers: replace(
                decision, evaluated_at=temporal.now,
            ),
        ), patch(
            "app.services.mindcore.autonomy_runtime.derive_autonomy_intention",
            side_effect=lambda current_decision, *_args, **_kwargs: replace(
                intention, evaluated_at=current_decision.evaluated_at,
            ),
        ):
            yield

    async def test_m2_m3_m5_m6_m7_execute_one_durable_turn_and_repeat_is_suppressed(self) -> None:
        result = await run_autonomy_cycle(
            pool=self.pool, settings=self.settings, identity_prompt="Test Persona identity",
            now=self.now, runtime=self.runtime, executor=self._fake_executor,
        )
        self.assertEqual(result.status, "executed")
        self.assertEqual(self.provider_calls, 1)
        async with self.pool.acquire() as connection:
            turns = await connection.fetch(
                """select initiator_actor,trigger_type,input_source,user_message_id,status
                   from chat_turns where trigger_type='autonomy_decision'"""
            )
            messages = await connection.fetch(
                """select role,source_device from messages where source_device='mindcore_proactive'"""
            )
        self.assertEqual(len(turns), 1)
        self.assertEqual((turns[0]["initiator_actor"], turns[0]["trigger_type"], turns[0]["input_source"]),
                         ("persona", "autonomy_decision", "internal"))
        self.assertIsNone(turns[0]["user_message_id"])
        self.assertEqual(turns[0]["status"], "complete")
        self.assertEqual(messages, [{"role": "diana", "source_device": "mindcore_proactive"}])

        inside_cooldown = await run_autonomy_cycle(
            pool=self.pool, settings=self.settings, identity_prompt="Test Persona identity",
            now=self.now + timedelta(minutes=5), runtime=self.runtime, executor=self._fake_executor,
        )
        self.assertEqual(inside_cooldown.reason, "cooldown")
        self.assertEqual(self.provider_calls, 1)

        repeated = await run_autonomy_cycle(
            pool=self.pool, settings=self.settings, identity_prompt="Test Persona identity",
            now=self.now + timedelta(hours=2), runtime=self.runtime, executor=self._fake_executor,
        )
        self.assertNotEqual(repeated.status, "executed")
        self.assertEqual(self.provider_calls, 1)

    async def test_provider_guard_rechecks_durable_user_activity_before_execution(self) -> None:
        async def guarded_executor(**kwargs):
            # Simulate activity after M8 selected the intention but before M7
            # reaches its provider guard.
            async with self.pool.acquire() as connection:
                await connection.execute(
                    """insert into messages(id,conversation_id,sequence,role,content,source_device,created_at)
                       values($1,$2,2,'user','second synthetic input','test',$3)""",
                    str(uuid4()), self.conversation_id, self.now.isoformat(),
                )
            return await execute_proactive_intention(**kwargs, provider=self._provider)
        result = await run_autonomy_cycle(
            pool=self.pool, settings=self.settings, identity_prompt="Test Persona identity",
            now=self.now + timedelta(seconds=1), runtime=self.runtime, executor=guarded_executor,
        )
        self.assertEqual(result.reason, "user_or_policy_changed")
        self.assertEqual(self.provider_calls, 0)

    async def test_concurrent_ticks_coalesce_to_one_executor_call(self) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        executor_calls = 0

        async def slow_executor(**kwargs):
            nonlocal executor_calls
            executor_calls += 1
            entered.set()
            await release.wait()
            return ProactiveExecutionResult(
                turn_id=uuid4(), conversation_id=kwargs["conversation_id"],
                message_id=uuid4(), intention_key=kwargs["intention"].intention_key,
                status="complete", created_at=self.now,
            )

        first = asyncio.create_task(run_autonomy_cycle(
            pool=self.pool, settings=self.settings, identity_prompt="Test Persona identity",
            now=self.now, runtime=self.runtime, executor=slow_executor,
        ))
        await entered.wait()
        coalesced = await run_autonomy_cycle(
            pool=self.pool, settings=self.settings, identity_prompt="Test Persona identity",
            now=self.now, runtime=self.runtime, executor=slow_executor,
        )
        self.assertEqual(coalesced.status, "coalesced")
        release.set()
        first_result = await first
        self.assertEqual(first_result.reason, "same_intention")
        self.assertEqual(executor_calls, 1)

    async def test_failure_backoff_and_meaningful_user_activity_reset(self) -> None:
        executor_calls = 0

        async def fails_once(**_kwargs):
            nonlocal executor_calls
            executor_calls += 1
            if executor_calls == 1:
                raise RuntimeError("synthetic provider failure")
            return await self._fake_executor(**_kwargs)

        failed = await run_autonomy_cycle(
            pool=self.pool, settings=self.settings, identity_prompt="Test Persona identity",
            now=self.now, runtime=self.runtime, executor=fails_once,
        )
        self.assertEqual(failed.status, "failed")
        self.assertEqual(self.runtime.retry_after, self.now + timedelta(seconds=60))
        backed_off = await run_autonomy_cycle(
            pool=self.pool, settings=self.settings, identity_prompt="Test Persona identity",
            now=self.now + timedelta(seconds=30), runtime=self.runtime, executor=fails_once,
        )
        self.assertEqual(backed_off.reason, "failure_backoff")
        self.assertEqual(executor_calls, 1)

        async with self.pool.acquire() as connection:
            await connection.execute(
                """insert into messages(id,conversation_id,sequence,role,content,source_device,created_at)
                   values($1,$2,2,'user','new synthetic activity','test',$3)""",
                str(uuid4()), self.conversation_id, (self.now - timedelta(hours=2)).isoformat(),
            )
        retried = await run_autonomy_cycle(
            pool=self.pool, settings=self.settings, identity_prompt="Test Persona identity",
            now=self.now + timedelta(seconds=31), runtime=self.runtime, executor=fails_once,
        )
        self.assertEqual(retried.status, "executed")
        self.assertEqual(executor_calls, 2)

    async def test_durable_ignored_streak_hard_suppresses_until_new_user_activity(self) -> None:
        async with self.pool.acquire() as connection:
            for index in range(3):
                message_id = str(uuid4())
                turn_id = str(uuid4())
                created_at = (self.now - timedelta(hours=2) + timedelta(minutes=index + 1)).isoformat()
                await connection.execute(
                    """insert into messages(id,conversation_id,sequence,role,content,source_device,created_at)
                       values($1,$2,$3,'diana','synthetic proactive','mindcore_proactive',$4)""",
                    message_id, self.conversation_id, index + 2, created_at,
                )
                await connection.execute(
                    """insert into chat_turns(
                         turn_id,conversation_id,user_message_id,assistant_message_id,status,created_at,updated_at,
                         core_completed_at,completed_at,initiator_actor,trigger_type,input_source)
                       values($1,$2,null,$3,'complete',$4,$4,$4,$4,'persona','autonomy_decision','internal')""",
                    turn_id, self.conversation_id, message_id, created_at,
                )
        from app.services.mindcore.autonomy_runtime import load_durable_autonomy_state
        state = await load_durable_autonomy_state(self.pool)
        self.assertEqual(state.ignored_streak, 3)
        suppressed = await run_autonomy_cycle(
            pool=self.pool, settings=self.settings, identity_prompt="Test Persona identity",
            now=self.now, runtime=self.runtime, executor=self._fake_executor,
        )
        self.assertEqual(suppressed.reason, "ignored_streak")
        self.assertEqual(self.provider_calls, 0)

        async with self.pool.acquire() as connection:
            await connection.execute(
                """insert into messages(id,conversation_id,sequence,role,content,source_device,created_at)
                   values($1,$2,5,'user','new synthetic activity','test',$3)""",
                str(uuid4()), self.conversation_id, (self.now - timedelta(hours=1)).isoformat(),
            )
        state_after_user = await load_durable_autonomy_state(self.pool)
        self.assertEqual(state_after_user.ignored_streak, 0)

    async def pool_scalar(self, query: str, *args) -> int:
        async with self.pool.acquire() as connection:
            return int(await connection.fetchval(query, *args))

    @stable_policy_test
    async def test_success_record_and_same_anchor_restart_suppression(self) -> None:
        result = await run_autonomy_cycle(
            pool=self.pool, settings=self.settings, identity_prompt="Test Persona identity",
            now=self.now, runtime=self.runtime, executor=self._fake_executor,
        )
        self.assertEqual(result.status, "executed")
        async with self.pool.acquire() as connection:
            record = await connection.fetchrow(
                "select * from autonomy_executions where turn_id=$1", result.execution.turn_id
            )
        self.assertEqual(record["status"], "COMPLETE")
        self.assertEqual(record["intention_key"], result.execution.intention_key)
        self.assertEqual(str(record["user_activity_anchor_message_id"]), self.user_message_id)
        self.assertEqual(record["persona_id"], "persona-a")

        restarted = AutonomyRuntimeState(started_at=self.now + timedelta(hours=2) - timedelta(minutes=3))
        repeated = await run_autonomy_cycle(
            pool=self.pool, settings=self.settings, identity_prompt="Test Persona identity",
            now=self.now + timedelta(hours=2), runtime=restarted, executor=self._fake_executor,
        )
        self.assertEqual(repeated.reason, "same_intention_durable")
        self.assertEqual(self.provider_calls, 1)
        self.assertEqual(await self.pool_scalar(
            "select count(*) from messages where source_device='mindcore_proactive'"
        ), 1)

    @stable_policy_test
    async def test_new_user_anchor_allows_same_intention_again(self) -> None:
        await run_autonomy_cycle(
            pool=self.pool, settings=self.settings, identity_prompt="Test Persona identity",
            now=self.now, runtime=self.runtime, executor=self._fake_executor,
        )
        new_anchor = str(uuid4())
        activity_at = self.now + timedelta(hours=2)
        async with self.pool.acquire() as connection:
            await connection.execute(
                """insert into messages(id,conversation_id,sequence,role,content,source_device,created_at)
                   values($1,$2,3,'user','new synthetic activity','test',$3)""",
                new_anchor, self.conversation_id, activity_at.isoformat(),
            )
        fresh_runtime = AutonomyRuntimeState(started_at=activity_at - timedelta(minutes=3))
        result = await run_autonomy_cycle(
            pool=self.pool, settings=self.settings, identity_prompt="Test Persona identity",
            now=activity_at + timedelta(hours=1), runtime=fresh_runtime,
            executor=self._fake_executor,
        )
        self.assertEqual(result.status, "executed")
        self.assertEqual(self.provider_calls, 2)
        self.assertEqual(await self.pool_scalar(
            "select count(distinct user_activity_anchor_message_id) from autonomy_executions"
        ), 2)

    @stable_policy_test
    async def test_provider_failure_backoff_is_durable_and_anchor_scoped(self) -> None:
        async def failing_provider(*_args, **_kwargs):
            self.provider_calls += 1
            raise RuntimeError("synthetic provider outage")

        async def failing_executor(**kwargs):
            return await execute_proactive_intention(**kwargs, provider=failing_provider)

        first = await run_autonomy_cycle(
            pool=self.pool, settings=self.settings, identity_prompt="Test Persona identity",
            now=self.now, runtime=self.runtime, executor=failing_executor,
        )
        self.assertEqual(first.status, "failed")
        self.assertEqual(await self.pool_scalar(
            "select count(*) from autonomy_executions where status='FAILED_SAFE' and safe_error_category='provider_failure'"
        ), 1)
        restarted = AutonomyRuntimeState(started_at=self.now + timedelta(seconds=30) - timedelta(minutes=3))
        waiting = await run_autonomy_cycle(
            pool=self.pool, settings=self.settings, identity_prompt="Test Persona identity",
            now=self.now + timedelta(seconds=30), runtime=restarted, executor=failing_executor,
        )
        self.assertEqual(waiting.reason, "failure_backoff_durable")
        self.assertEqual(self.provider_calls, 1)

        new_anchor = str(uuid4())
        activity_at = self.now + timedelta(minutes=2)
        async with self.pool.acquire() as connection:
            await connection.execute(
                """insert into messages(id,conversation_id,sequence,role,content,source_device,created_at)
                   values($1,$2,3,'user','reset synthetic activity','test',$3)""",
                new_anchor, self.conversation_id, activity_at.isoformat(),
            )
        reset_runtime = AutonomyRuntimeState(started_at=activity_at - timedelta(minutes=3))
        retried = await run_autonomy_cycle(
            pool=self.pool, settings=self.settings, identity_prompt="Test Persona identity",
            now=activity_at + timedelta(seconds=1), runtime=reset_runtime,
            executor=failing_executor,
        )
        self.assertEqual(retried.status, "failed")
        self.assertEqual(self.provider_calls, 2)

    @stable_policy_test
    async def test_empty_whitespace_and_invalid_provider_results_are_retryable_failures(self) -> None:
        outputs = ("", "   \n\t", "invalid-\ud800")
        for index, output in enumerate(outputs):
            with self.subTest(output_kind="empty" if not output else "invalid"):
                if index:
                    self.now += timedelta(seconds=(60, 120)[index - 1] + 1)
                    self.runtime = AutonomyRuntimeState(started_at=self.now - timedelta(minutes=3))
                async def invalid_provider(*_args, **_kwargs):
                    self.provider_calls += 1
                    return output

                async def invalid_executor(**kwargs):
                    return await execute_proactive_intention(**kwargs, provider=invalid_provider)

                result = await run_autonomy_cycle(
                    pool=self.pool, settings=self.settings, identity_prompt="Test Persona identity",
                    now=self.now, runtime=self.runtime, executor=invalid_executor,
                )
                self.assertEqual(result.status, "failed")
                self.assertEqual(await self.pool_scalar(
                    "select count(*) from autonomy_executions where status='FAILED_SAFE' "
                    "and safe_error_category='provider_invalid_response'"
                ), index + 1)
                self.assertEqual(await self.pool_scalar(
                    "select count(*) from messages where source_device='mindcore_proactive'"
                ), 0)
                gate = await __import__(
                    "app.services.mindcore.autonomy_execution_store",
                    fromlist=["get_execution_gate"],
                ).get_execution_gate(
                    self.pool, persona_id="persona-a", intention_key="CHECK_IN:curiosity",
                    user_activity_anchor_message_id=self.user_message_id, now=self.now,
                )
                self.assertEqual(gate[0], False)
                delay = (60, 120, 300)[index]
                self.assertEqual(gate[1], self.now + timedelta(seconds=delay))
                immediate_retry = await run_autonomy_cycle(
                    pool=self.pool, settings=self.settings, identity_prompt="Test Persona identity",
                    now=self.now, runtime=AutonomyRuntimeState(
                        started_at=self.now - timedelta(minutes=3)
                    ), executor=invalid_executor,
                )
                self.assertEqual(immediate_retry.reason, "failure_backoff_durable")
                restarted = await recover_incomplete_autonomy_executions(
                    self.pool, persona_id="persona-a", now=self.now + timedelta(seconds=1),
                )
                self.assertEqual(sum(restarted.values()), 0)
                self.assertEqual(self.provider_calls, index + 1)

    async def test_failure_backoff_uses_failure_time_not_creation_time(self) -> None:
        from types import SimpleNamespace
        from app.services.mindcore.autonomy_execution_store import (
            get_execution_gate, mark_failed_safe, reserve_autonomy_execution,
        )

        created_at = self.now - timedelta(minutes=20)
        reservation = await reserve_autonomy_execution(
            self.pool, conversation_id=self.conversation_id, persona_id="persona-a",
            intention=SimpleNamespace(
                intention_key="CHECK_IN:curiosity", intention_type="CHECK_IN",
                target_kind="NEED", target_key="curiosity",
            ), user_activity_anchor_message_id=self.user_message_id, now=created_at,
        )
        failed_at = created_at + timedelta(minutes=5)
        await mark_failed_safe(
            self.pool, reservation.execution_id, RuntimeError("synthetic"),
            now=failed_at, safe_category="provider_failure",
        )
        blocked, retry_after = await get_execution_gate(
            self.pool, persona_id="persona-a", intention_key="CHECK_IN:curiosity",
            user_activity_anchor_message_id=self.user_message_id,
            now=failed_at + timedelta(seconds=10),
        )
        self.assertFalse(blocked)
        self.assertEqual(retry_after, failed_at + timedelta(seconds=60))

    @stable_policy_test
    async def test_atomic_reservation_turn_stage_creation_rolls_back_on_turn_failure(self) -> None:
        async def fail_turn(*_args, **_kwargs):
            raise RuntimeError("injected turn creation failure")

        with patch(
            "app.services.turn_durability.TurnDurability._insert_proactive_turn_with_connection",
            new=fail_turn,
        ):
            result = await run_autonomy_cycle(
                pool=self.pool, settings=self.settings, identity_prompt="Test Persona identity",
                now=self.now, runtime=self.runtime, executor=self._fake_executor,
            )
        self.assertEqual(result.status, "failed")
        self.assertEqual(self.provider_calls, 0)
        self.assertEqual(await self.pool_scalar("select count(*) from autonomy_executions"), 0)
        self.assertEqual(await self.pool_scalar(
            "select count(*) from chat_turns where trigger_type='autonomy_decision'"
        ), 0)

    @stable_policy_test
    async def test_atomic_reservation_stage_creation_rolls_back_all_rows(self) -> None:
        async def fail_stages(*_args, **_kwargs):
            raise RuntimeError("injected stage creation failure")

        with patch(
            "app.services.turn_durability.TurnDurability._insert_proactive_stages_with_connection",
            new=fail_stages,
        ):
            result = await run_autonomy_cycle(
                pool=self.pool, settings=self.settings, identity_prompt="Test Persona identity",
                now=self.now, runtime=self.runtime, executor=self._fake_executor,
            )
        self.assertEqual(result.status, "failed")
        self.assertEqual(self.provider_calls, 0)
        self.assertEqual(await self.pool_scalar("select count(*) from autonomy_executions"), 0)
        self.assertEqual(await self.pool_scalar(
            "select count(*) from chat_turns where trigger_type='autonomy_decision'"
        ), 0)
        self.assertEqual(await self.pool_scalar("select count(*) from chat_turn_stages"), 0)
        retry = await run_autonomy_cycle(
            pool=self.pool, settings=self.settings, identity_prompt="Test Persona identity",
            now=self.now + timedelta(seconds=61), runtime=self.runtime, executor=self._fake_executor,
        )
        self.assertEqual(retry.status, "executed")
        self.assertEqual(self.provider_calls, 1)

    @stable_policy_test
    async def test_provider_crash_recovers_indeterminate_and_never_recalls(self) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def waiting_provider(*_args, **_kwargs):
            self.provider_calls += 1
            entered.set()
            await release.wait()
            return "not durably saved"

        async def waiting_executor(**kwargs):
            return await execute_proactive_intention(**kwargs, provider=waiting_provider)

        task = asyncio.create_task(run_autonomy_cycle(
            pool=self.pool, settings=self.settings, identity_prompt="Test Persona identity",
            now=self.now, runtime=self.runtime, executor=waiting_executor,
        ))
        await asyncio.wait_for(entered.wait(), timeout=2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(await self.pool_scalar("select count(*) from autonomy_executions where status='PROVIDER_STARTED'"), 1)
        first_recovery = await recover_incomplete_autonomy_executions(
            self.pool, persona_id="persona-a", now=self.now + timedelta(seconds=1),
        )
        self.assertEqual(first_recovery["INDETERMINATE"], 1)
        second_recovery = await recover_incomplete_autonomy_executions(
            self.pool, persona_id="persona-a", now=self.now + timedelta(seconds=2),
        )
        self.assertEqual(sum(second_recovery.values()), 0)
        restarted = AutonomyRuntimeState(started_at=self.now + timedelta(hours=2) - timedelta(minutes=3))
        result = await run_autonomy_cycle(
            pool=self.pool, settings=self.settings, identity_prompt="Test Persona identity",
            now=self.now + timedelta(hours=2), runtime=restarted, executor=waiting_executor,
        )
        self.assertEqual(result.reason, "same_intention_durable")
        self.assertEqual(self.provider_calls, 1)
        self.assertEqual(await self.pool_scalar(
            "select count(*) from messages where source_device='mindcore_proactive'"
        ), 0)

    async def test_message_persisted_before_finalization_recovers_once_and_100_repeats_are_noop(self) -> None:
        from unittest.mock import patch

        def fail_finalization(*_args, **_kwargs):
            raise RuntimeError("injected finalization boundary")

        with patch(
            "app.services.mindcore.proactive_execution.mark_autonomy_execution_complete",
            new=fail_finalization,
        ):
            failed = await run_autonomy_cycle(
                pool=self.pool, settings=self.settings, identity_prompt="Test Persona identity",
                now=self.now, runtime=self.runtime, executor=self._fake_executor,
            )
        self.assertEqual(failed.status, "failed")
        self.assertEqual(await self.pool_scalar(
            "select count(*) from autonomy_executions where status='MESSAGE_PERSISTED'"
        ), 1)
        async with self.pool.acquire() as connection:
            persisted = await connection.fetchrow(
                "select turn_id from autonomy_executions where status='MESSAGE_PERSISTED'"
            )
            turn_status = await connection.fetchval(
                "select status from chat_turns where turn_id=$1", persisted["turn_id"]
            )
        self.assertEqual(turn_status, "complete")
        self.assertEqual(await self.pool_scalar(
            "select count(*) from messages where source_device='mindcore_proactive'"
        ), 1)
        recovered = await recover_incomplete_autonomy_executions(
            self.pool, persona_id="persona-a", now=self.now + timedelta(seconds=1),
        )
        self.assertEqual(recovered["COMPLETE"], 1)
        for offset in range(100):
            await recover_incomplete_autonomy_executions(
                self.pool, persona_id="persona-a", now=self.now + timedelta(seconds=2 + offset),
            )
        self.assertEqual(await self.pool_scalar(
            "select count(*) from autonomy_executions where status='COMPLETE'"
        ), 1)
        self.assertEqual(await self.pool_scalar(
            "select count(*) from messages where source_device='mindcore_proactive'"
        ), 1)
        self.assertEqual(self.provider_calls, 1)

    async def test_concurrent_reservation_exactly_once(self) -> None:
        from types import SimpleNamespace
        from app.services.mindcore.autonomy_execution_store import reserve_autonomy_execution

        intention = SimpleNamespace(
            intention_key="CHECK_IN:curiosity", intention_type="check_in",
            target_kind="need", target_key="curiosity",
        )
        pool = PreopenedLocalPool(Path(self.temp.name) / "autonomy-runtime.db")
        try:
            async def reserve():
                return await reserve_autonomy_execution(
                    pool, conversation_id=self.conversation_id, persona_id="persona-a",
                    intention=intention, user_activity_anchor_message_id=self.user_message_id,
                    now=self.now,
                )
            results = await asyncio.gather(reserve(), reserve())
            self.assertEqual(sum(item is not None for item in results), 1)
        finally:
            pool.close()
        self.assertEqual(await self.pool_scalar("select count(*) from autonomy_executions"), 1)

    async def test_reserved_orphan_recovers_failed_safe_without_provider(self) -> None:
        from types import SimpleNamespace
        from app.services.mindcore.autonomy_execution_store import reserve_autonomy_execution

        reservation = await reserve_autonomy_execution(
            self.pool, conversation_id=self.conversation_id, persona_id="persona-a",
            intention=SimpleNamespace(
                intention_key="CHECK_IN:curiosity", intention_type="check_in",
                target_kind="need", target_key="curiosity",
            ),
            user_activity_anchor_message_id=self.user_message_id, now=self.now,
        )
        self.assertIsNotNone(reservation)
        recovered = await recover_incomplete_autonomy_executions(
            self.pool, persona_id="persona-a", now=self.now + timedelta(seconds=1),
        )
        self.assertEqual(recovered["FAILED_SAFE"], 1)
        self.assertEqual(await self.pool_scalar(
            "select count(*) from chat_turns where turn_id=$1", reservation.turn_id
        ), 0)
        self.assertEqual(self.provider_calls, 0)

    async def test_reserved_turn_created_before_provider_recovers_without_replay(self) -> None:
        from types import SimpleNamespace
        from app.services.mindcore.autonomy_execution_store import reserve_autonomy_execution

        reservation = await reserve_autonomy_execution(
            self.pool, conversation_id=self.conversation_id, persona_id="persona-a",
            intention=SimpleNamespace(
                intention_key="CHECK_IN:curiosity", intention_type="CHECK_IN",
                target_kind="NEED", target_key="curiosity",
            ),
            user_activity_anchor_message_id=self.user_message_id, now=self.now,
        )
        from app.models.turn_context import ActorContext, ActorKind, TurnContext, TurnInputSource, TurnTrigger
        await __import__("app.services.turn_durability", fromlist=["TurnDurability"]).TurnDurability(
            self.pool
        ).begin_proactive_turn(
            self.conversation_id,
            TurnContext(
                initiator=ActorContext(ActorKind.PERSONA),
                trigger=TurnTrigger.AUTONOMY_DECISION,
                input_source=TurnInputSource.INTERNAL,
            ),
            turn_id=reservation.turn_id,
        )
        recovered = await recover_incomplete_autonomy_executions(
            self.pool, persona_id="persona-a", now=self.now + timedelta(seconds=1),
        )
        self.assertEqual(recovered["FAILED_SAFE"], 1)
        self.assertEqual(self.provider_calls, 0)
        async with self.pool.acquire() as connection:
            turn = await connection.fetchrow(
                "select status,last_failed_stage,safe_error_category from chat_turns where turn_id=$1",
                reservation.turn_id,
            )
            stages = await connection.fetch(
                "select status from chat_turn_stages where turn_id=$1", reservation.turn_id
            )
        self.assertEqual(turn["status"], "core_failed")
        self.assertEqual(turn["last_failed_stage"], "recovery")
        self.assertEqual(turn["safe_error_category"], "recovered_before_provider")
        self.assertEqual({row["status"] for row in stages}, {"failed"})
        for offset in range(100):
            repeated = await recover_incomplete_autonomy_executions(
                self.pool, persona_id="persona-a", now=self.now + timedelta(seconds=2 + offset),
            )
            self.assertEqual(sum(repeated.values()), 0)
        self.assertEqual(await self.pool_scalar(
            "select count(*) from autonomy_executions where status='FAILED_SAFE' "
            "and safe_error_category='recovered_before_provider'"
        ), 1)
        self.assertEqual(await self.pool_scalar(
            "select count(*) from messages where source_device='mindcore_proactive'"
        ), 0)
        self.assertEqual(self.provider_calls, 0)

    async def test_reserved_execution_with_malformed_linked_turn_becomes_indeterminate(self) -> None:
        from types import SimpleNamespace
        from app.services.mindcore.autonomy_execution_store import reserve_autonomy_execution

        reservation = await reserve_autonomy_execution(
            self.pool, conversation_id=self.conversation_id, persona_id="persona-a",
            intention=SimpleNamespace(
                intention_key="CHECK_IN:curiosity", intention_type="CHECK_IN",
                target_kind="NEED", target_key="curiosity",
            ), user_activity_anchor_message_id=self.user_message_id, now=self.now,
        )
        async with self.pool.acquire() as connection:
            await connection.execute(
                """insert into chat_turns(
                     turn_id,conversation_id,user_message_id,assistant_message_id,status,created_at,updated_at,
                     initiator_actor,trigger_type,input_source)
                   values($1,$2,null,null,'pending',$3,$3,'system','system_event','internal')""",
                reservation.turn_id, self.conversation_id, self.now.isoformat(),
            )
        recovered = await recover_incomplete_autonomy_executions(
            self.pool, persona_id="persona-a", now=self.now + timedelta(seconds=1),
        )
        self.assertEqual(recovered["INDETERMINATE"], 1)
        async with self.pool.acquire() as connection:
            status = await connection.fetchval(
                "select status from autonomy_executions where execution_id=$1", reservation.execution_id
            )
        self.assertEqual(status, "INDETERMINATE")
        self.assertEqual(self.provider_calls, 0)

    async def test_recovery_is_persona_scoped(self) -> None:
        from types import SimpleNamespace
        from app.services.mindcore.autonomy_execution_store import reserve_autonomy_execution

        intention = SimpleNamespace(
            intention_key="CHECK_IN:curiosity", intention_type="CHECK_IN",
            target_kind="NEED", target_key="curiosity",
        )
        first = await reserve_autonomy_execution(
            self.pool, conversation_id=self.conversation_id, persona_id="persona-a",
            intention=intention, user_activity_anchor_message_id=self.user_message_id,
            now=self.now,
        )
        second = await reserve_autonomy_execution(
            self.pool, conversation_id=self.conversation_id, persona_id="persona-b",
            intention=intention, user_activity_anchor_message_id=self.user_message_id,
            now=self.now,
        )
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        result = await recover_incomplete_autonomy_executions(
            self.pool, persona_id="persona-a", now=self.now + timedelta(seconds=1),
        )
        self.assertEqual(result["FAILED_SAFE"], 1)
        async with self.pool.acquire() as connection:
            other = await connection.fetchval(
                "select status from autonomy_executions where execution_id=$1", second.execution_id
            )
        self.assertEqual(other, "RESERVED")

    async def test_recovery_rejects_conversation_and_provenance_mismatch(self) -> None:
        from types import SimpleNamespace
        from app.services.mindcore.autonomy_execution_store import (
            mark_provider_started, reserve_autonomy_execution,
        )

        other_conversation = str(uuid4())
        now_text = self.now.isoformat()
        async with self.pool.acquire() as connection:
            await connection.execute(
                "insert into conversations(conversation_id,source_device,started_at,ended_at) values($1,'test',$2,null)",
                other_conversation, now_text,
            )
        intention = SimpleNamespace(
            intention_key="CHECK_IN:curiosity", intention_type="CHECK_IN",
            target_kind="NEED", target_key="curiosity",
        )
        wrong_conversation = await reserve_autonomy_execution(
            self.pool, conversation_id=self.conversation_id, persona_id="persona-a",
            intention=intention, user_activity_anchor_message_id=self.user_message_id,
            now=self.now,
        )
        wrong_provenance = await reserve_autonomy_execution(
            self.pool, conversation_id=self.conversation_id, persona_id="persona-a",
            intention=SimpleNamespace(
                intention_key="CHECK_IN:understanding", intention_type="CHECK_IN",
                target_kind="NEED", target_key="understanding",
            ),
            user_activity_anchor_message_id=self.user_message_id, now=self.now,
        )
        await mark_provider_started(self.pool, wrong_conversation.execution_id, now=self.now)
        await mark_provider_started(self.pool, wrong_provenance.execution_id, now=self.now)

        async with self.pool.acquire() as connection:
            other_message = str(uuid4())
            provenance_message = str(uuid4())
            await connection.execute(
                """insert into messages(id,conversation_id,sequence,role,content,source_device,created_at)
                   values($1,$2,1,'diana','test','mindcore_proactive',$3)""",
                other_message, other_conversation, now_text,
            )
            await connection.execute(
                """insert into messages(id,conversation_id,sequence,role,content,source_device,created_at)
                   values($1,$2,2,'diana','test','mindcore_proactive',$3)""",
                provenance_message, self.conversation_id, now_text,
            )
            for reservation, conversation, message, actor, trigger, source in (
                (wrong_conversation, other_conversation, other_message, "persona", "autonomy_decision", "internal"),
                (wrong_provenance, str(self.conversation_id), provenance_message, "system", "system_event", "internal"),
            ):
                await connection.execute(
                    """insert into chat_turns(
                         turn_id,conversation_id,user_message_id,assistant_message_id,status,created_at,updated_at,
                         core_completed_at,completed_at,initiator_actor,trigger_type,input_source)
                       values($1,$2,null,$3,'complete',$4,$4,$4,$4,$5,$6,$7)""",
                    reservation.turn_id, conversation, message, now_text, actor, trigger, source,
                )

        result = await recover_incomplete_autonomy_executions(
            self.pool, persona_id="persona-a", now=self.now + timedelta(seconds=1),
        )
        self.assertEqual(result["INDETERMINATE"], 2)
        self.assertEqual(self.provider_calls, 0)
        async with self.pool.acquire() as connection:
            statuses = await connection.fetch(
                "select status from autonomy_executions order by intention_key"
            )
        self.assertTrue(all(row["status"] == "INDETERMINATE" for row in statuses))

    async def test_response_before_assistant_persistence_crash_is_indeterminate(self) -> None:
        entered = asyncio.Event()
        block = asyncio.Event()
        original = __import__(
            "app.services.turn_durability", fromlist=["TurnDurability"]
        ).TurnDurability.complete_proactive_core

        async def pause_before_persist(self, *args, **kwargs):
            entered.set()
            await block.wait()
            return await original(self, *args, **kwargs)

        from unittest.mock import patch
        with patch(
            "app.services.turn_durability.TurnDurability.complete_proactive_core",
            new=pause_before_persist,
        ):
            task = asyncio.create_task(run_autonomy_cycle(
                pool=self.pool, settings=self.settings, identity_prompt="Test Persona identity",
                now=self.now, runtime=self.runtime, executor=self._fake_executor,
            ))
            await asyncio.wait_for(entered.wait(), timeout=2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(self.provider_calls, 1)
        self.assertEqual(await self.pool_scalar(
            "select count(*) from messages where source_device='mindcore_proactive'"
        ), 0)
        recovered = await recover_incomplete_autonomy_executions(
            self.pool, persona_id="persona-a", now=self.now + timedelta(seconds=1),
        )
        self.assertEqual(recovered["INDETERMINATE"], 1)
        self.assertEqual(self.provider_calls, 1)

    async def test_message_linkage_fault_rolls_back_message_and_execution_transition(self) -> None:
        from unittest.mock import patch

        async def fail_transition(*_args, **_kwargs):
            raise RuntimeError("injected atomic persistence failure")

        with patch(
            "app.services.mindcore.autonomy_execution_store.mark_message_persisted",
            new=fail_transition,
        ):
            failed = await run_autonomy_cycle(
                pool=self.pool, settings=self.settings, identity_prompt="Test Persona identity",
                now=self.now, runtime=self.runtime, executor=self._fake_executor,
            )
        self.assertEqual(failed.status, "failed")
        self.assertEqual(await self.pool_scalar(
            "select count(*) from messages where source_device='mindcore_proactive'"
        ), 0)
        self.assertEqual(await self.pool_scalar(
            "select count(*) from autonomy_executions where status='PROVIDER_STARTED'"
        ), 1)
        recovered = await recover_incomplete_autonomy_executions(
            self.pool, persona_id="persona-a", now=self.now + timedelta(seconds=1),
        )
        self.assertEqual(recovered["INDETERMINATE"], 1)

    @stable_policy_test
    async def test_deleted_completed_message_remains_suppressed(self) -> None:
        await run_autonomy_cycle(
            pool=self.pool, settings=self.settings, identity_prompt="Test Persona identity",
            now=self.now, runtime=self.runtime, executor=self._fake_executor,
        )
        async with self.pool.acquire() as connection:
            await connection.execute("delete from messages where source_device='mindcore_proactive'")
        restarted = AutonomyRuntimeState(started_at=self.now + timedelta(hours=2) - timedelta(minutes=3))
        repeated = await run_autonomy_cycle(
            pool=self.pool, settings=self.settings, identity_prompt="Test Persona identity",
            now=self.now + timedelta(hours=2), runtime=restarted, executor=self._fake_executor,
        )
        self.assertEqual(repeated.reason, "same_intention_durable")
        self.assertEqual(self.provider_calls, 1)


if __name__ == "__main__":
    unittest.main()
