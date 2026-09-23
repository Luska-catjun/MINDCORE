from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

import libsql

from app.config import Settings
from app.database.turso import TursoConnection
from app.models.proactive_execution import ProactiveExecutionResult
from app.services.mindcore.autonomy_runtime import AutonomyRuntimeState, run_autonomy_cycle
from app.services.mindcore.proactive_execution import execute_proactive_intention


ROOT = Path(__file__).resolve().parents[2]
BASELINE_SQL = (ROOT / "db" / "turso" / "baseline_v1.sql").read_text(encoding="utf-8")


class LocalPool:
    def __init__(self, path: Path) -> None:
        self.path = str(path)

    @asynccontextmanager
    async def acquire(self):
        raw = await asyncio.to_thread(libsql.connect, database=self.path)
        try:
            yield TursoConnection(raw)
        finally:
            await asyncio.to_thread(raw.close)


async def _execute_script(pool: LocalPool, sql: str) -> None:
    async with pool.acquire() as connection:
        async with connection.transaction():
            for statement in sql.split(";"):
                if statement.strip():
                    await connection.execute(statement)


class AutonomyRuntimeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.pool = LocalPool(Path(self.temp.name) / "autonomy-runtime.db")
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


if __name__ == "__main__":
    unittest.main()
