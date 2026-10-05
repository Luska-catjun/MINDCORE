from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

import libsql

from app.database.turso import TursoConnection
from app.models.enums import MessageRole
from app.models.turn_context import TurnContext
from app.schemas.chat import ChatRequest
from app.schemas.messages import MessageCreate
from app.services import repository
from app.services.turn_durability import (
    MAX_STAGE_ATTEMPTS,
    POST_COGNITION_STAGES,
    TurnDurability,
)
from app.services.turn_recovery import resume_turn


ROOT = Path(__file__).resolve().parents[2]
BASELINE_SQL = (ROOT / "db" / "turso" / "baseline_v1.sql").read_text(encoding="utf-8")


class LocalFilePool:
    def __init__(self, path: Path) -> None:
        self.path = str(path)

    @asynccontextmanager
    async def acquire(self):
        raw = await asyncio.to_thread(libsql.connect, database=self.path)
        try:
            yield TursoConnection(raw)
        finally:
            await asyncio.to_thread(raw.close)


async def execute_script(pool: LocalFilePool, sql: str) -> None:
    async with pool.acquire() as connection:
        async with connection.transaction():
            for statement in sql.split(";"):
                if statement.strip():
                    await connection.execute(statement)


class TurnDurabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.database = Path(self.directory.name) / "turns.db"
        self.pool = LocalFilePool(self.database)
        await execute_script(self.pool, BASELINE_SQL)
        self.conversation_id = uuid4()
        async with self.pool.acquire() as connection:
            await connection.execute(
                """insert into conversations(conversation_id,source_device,started_at,ended_at)
                   values($1,'test','2026-09-12T00:00:00+00:00',null)""",
                self.conversation_id,
            )
            await connection.execute(
                "create table turn_stage_effects(effect_key text primary key, value integer not null)"
            )
        self.durability = TurnDurability(self.pool)

    async def asyncTearDown(self) -> None:
        self.directory.cleanup()

    async def _core_completed_turn(self) -> tuple[dict, dict]:
        user = await self.durability.begin_turn(
            ChatRequest(
                conversation_id=self.conversation_id,
                role=MessageRole.user,
                content="hello",
            )
        )
        await self.durability.mark_foreground_context_prepared(user["id"])
        await self.durability.mark_provider_started(user["id"])
        assistant = await self.durability.complete_core(
            user["id"],
            MessageCreate(
                conversation_id=self.conversation_id,
                role=MessageRole.diana,
                content="hi",
                source_device="test",
            ),
        )
        return user, assistant

    async def _complete_except(self, turn_id, remaining: set[str]) -> None:
        for definition in POST_COGNITION_STAGES:
            if definition.name not in remaining:
                await self.durability.complete_noop(turn_id, definition.name)

    async def _row(self, statement: str, *args):
        async with self.pool.acquire() as connection:
            return await connection.fetchrow(statement, *args)

    async def _value(self, statement: str, *args):
        async with self.pool.acquire() as connection:
            return await connection.fetchval(statement, *args)

    async def _assert_user_context(self, turn_id) -> None:
        context = await self.durability.load_turn_context(turn_id)
        self.assertEqual(context, TurnContext.user_text())

    async def test_user_message_and_pending_turn_are_atomic(self) -> None:
        user = await self.durability.begin_turn(
            ChatRequest(
                conversation_id=self.conversation_id,
                role=MessageRole.user,
                content="atomic start",
            )
        )

        turn = await self.durability.get_turn(user["id"])
        self.assertEqual(turn["turn_id"], user["id"])
        self.assertEqual(turn["user_message_id"], user["id"])
        self.assertEqual(turn["status"], "pending")
        self.assertIsNone(turn["assistant_message_id"])
        stages = {row["stage_name"]: row for row in await self.durability.get_stages(user["id"])}
        self.assertEqual(set(stages), {"context_prepare", "provider_generate", "assistant_persist"})
        self.assertTrue(all(row["status"] == "pending" for row in stages.values()))
        await self._assert_user_context(user["id"])

    async def test_provider_failure_keeps_user_message_and_marks_core_failed(self) -> None:
        user = await self.durability.begin_turn(
            ChatRequest(
                conversation_id=self.conversation_id,
                role=MessageRole.user,
                content="provider failure",
            )
        )
        await self.durability.mark_foreground_context_prepared(user["id"])
        await self.durability.mark_provider_started(user["id"])
        error = RuntimeError("secret provider body must not be stored")

        await self.durability.mark_core_failed(user["id"], error)

        turn = await self.durability.get_turn(user["id"])
        self.assertEqual(turn["status"], "core_failed")
        self.assertEqual(turn["safe_error_category"], "RuntimeError")
        self.assertNotIn("secret", str(turn))
        self.assertEqual(
            await self._value("select count(*) from messages where id=$1", user["id"]), 1
        )
        stages = {row["stage_name"]: row for row in await self.durability.get_stages(user["id"])}
        self.assertEqual(stages["provider_generate"]["status"], "running")
        await self._assert_user_context(user["id"])

    async def test_assistant_failure_rolls_back_row_and_never_marks_core_complete(self) -> None:
        user = await self.durability.begin_turn(
            ChatRequest(
                conversation_id=self.conversation_id,
                role=MessageRole.user,
                content="assistant persistence failure",
            )
        )
        await self.durability.mark_foreground_context_prepared(user["id"])
        await self.durability.mark_provider_started(user["id"])
        original = repository.create_message

        async def fail_assistant(pool, payload):
            if payload.role == MessageRole.diana:
                raise RuntimeError("injected assistant failure")
            return await original(pool, payload)

        with patch.object(repository, "create_message", side_effect=fail_assistant):
            with self.assertRaisesRegex(RuntimeError, "assistant failure"):
                await self.durability.complete_core(
                    user["id"],
                    MessageCreate(
                        conversation_id=self.conversation_id,
                        role=MessageRole.diana,
                        content="must not persist",
                    ),
                )

        turn = await self.durability.get_turn(user["id"])
        self.assertEqual(turn["status"], "core_failed")
        self.assertIsNone(turn["assistant_message_id"])
        self.assertIsNone(turn["core_completed_at"])
        self.assertEqual(
            await self._value(
                "select count(*) from messages where conversation_id=$1", self.conversation_id
            ),
            1,
        )

    async def test_assistant_and_core_stage_ledger_commit_together(self) -> None:
        user, assistant = await self._core_completed_turn()

        turn = await self.durability.get_turn(user["id"])
        stages = await self.durability.get_stages(user["id"])
        self.assertEqual(turn["assistant_message_id"], assistant["id"])
        self.assertEqual(turn["status"], "core_completed")
        self.assertIsNotNone(turn["core_completed_at"])
        by_name = {row["stage_name"]: row for row in stages}
        self.assertEqual(len(stages), len(POST_COGNITION_STAGES) + 3)
        self.assertTrue(all(by_name[name]["status"] == "completed" for name in
                            ("context_prepare", "provider_generate", "assistant_persist")))
        self.assertTrue(all(by_name[definition.name]["status"] == "pending"
                            for definition in POST_COGNITION_STAGES))

    async def test_provider_started_is_visible_after_commit_to_fresh_reader(self) -> None:
        user = await self.durability.begin_turn(ChatRequest(
            conversation_id=self.conversation_id, role=MessageRole.user, content="fresh reader"
        ))
        await self.durability.mark_foreground_context_prepared(user["id"])
        await self.durability.mark_provider_started(user["id"])

        independent_pool = LocalFilePool(self.database)
        async with independent_pool.acquire() as connection:
            row = await connection.fetchrow(
                "select status,attempt_count,started_at from chat_turn_stages "
                "where turn_id=$1 and stage_name='provider_generate'", user["id"]
            )
        self.assertEqual(row["status"], "running")
        self.assertEqual(row["attempt_count"], 1)
        self.assertIsNotNone(row["started_at"])

    async def test_provider_started_requires_context_and_rejects_duplicate_start(self) -> None:
        user = await self.durability.begin_turn(ChatRequest(
            conversation_id=self.conversation_id, role=MessageRole.user, content="ordered start"
        ))
        with self.assertRaisesRegex(RuntimeError, "foreground_context_not_prepared"):
            await self.durability.mark_provider_started(user["id"])

        await self.durability.mark_foreground_context_prepared(user["id"])
        await self.durability.mark_provider_started(user["id"])
        with self.assertRaisesRegex(RuntimeError, "foreground_provider_already_started"):
            await self.durability.mark_provider_started(user["id"])
        stage = await self._row(
            "select status,attempt_count from chat_turn_stages where turn_id=$1 "
            "and stage_name='provider_generate'", user["id"]
        )
        self.assertEqual((stage["status"], stage["attempt_count"]), ("running", 1))

    async def test_core_completion_refuses_missing_provider_started_boundary(self) -> None:
        user = await self.durability.begin_turn(ChatRequest(
            conversation_id=self.conversation_id, role=MessageRole.user, content="missing boundary"
        ))
        await self.durability.mark_foreground_context_prepared(user["id"])
        with self.assertRaisesRegex(RuntimeError, "foreground_provider_boundary_missing"):
            await self.durability.complete_core(user["id"], MessageCreate(
                conversation_id=self.conversation_id, role=MessageRole.diana,
                content="must not persist", source_device="test",
            ))
        self.assertIsNone((await self.durability.get_turn(user["id"]))["assistant_message_id"])
        self.assertEqual(await self._value(
            "select count(*) from messages where role='diana' and conversation_id=$1",
            self.conversation_id,
        ), 0)

    async def test_provider_failure_keeps_durable_running_marker_for_ambiguity(self) -> None:
        user = await self.durability.begin_turn(ChatRequest(
            conversation_id=self.conversation_id, role=MessageRole.user, content="ambiguous provider"
        ))
        await self.durability.mark_foreground_context_prepared(user["id"])
        await self.durability.mark_provider_started(user["id"])
        await self.durability.mark_core_failed(user["id"], RuntimeError("provider interrupted"))
        turn = await self.durability.get_turn(user["id"])
        stage = await self._row(
            "select status from chat_turn_stages where turn_id=$1 and stage_name='provider_generate'",
            user["id"],
        )
        self.assertEqual(turn["status"], "core_failed")
        self.assertEqual(stage["status"], "running")

    async def test_stage_failure_rolls_back_side_effect_then_retry_completes_once(self) -> None:
        user, _assistant = await self._core_completed_turn()
        await self._complete_except(user["id"], {"relationship"})

        async def fail_after_write(stage_pool):
            async with stage_pool.acquire() as connection:
                await connection.execute(
                    "insert into turn_stage_effects(effect_key,value) values('relationship',1)"
                )
            raise RuntimeError("injected stage failure")

        with self.assertRaisesRegex(RuntimeError, "stage failure"):
            await self.durability.run_stage(user["id"], "relationship", fail_after_write)

        self.assertEqual(
            await self._value(
                "select count(*) from turn_stage_effects where effect_key='relationship'"
            ),
            0,
        )
        failed = await self._row(
            "select status,attempt_count from chat_turn_stages where turn_id=$1 and stage_name='relationship'",
            user["id"],
        )
        self.assertEqual((failed["status"], failed["attempt_count"]), ("failed", 1))
        self.assertEqual((await self.durability.get_turn(user["id"]))["status"], "partial")

        async def succeed(stage_pool):
            async with stage_pool.acquire() as connection:
                await connection.execute(
                    "insert into turn_stage_effects(effect_key,value) values('relationship',1)"
                )

        await self.durability.run_stage(user["id"], "relationship", succeed)
        self.assertEqual(
            await self._value(
                "select count(*) from turn_stage_effects where effect_key='relationship'"
            ),
            1,
        )
        self.assertEqual((await self.durability.get_turn(user["id"]))["status"], "complete")

    async def test_first_middle_and_last_stage_failures_are_each_durable_partial(self) -> None:
        for stage_name in ("intention_persist", "relationship", "self_model"):
            with self.subTest(stage=stage_name):
                user, _assistant = await self._core_completed_turn()
                await self._complete_except(user["id"], {stage_name})

                async def fail(_stage_pool):
                    raise RuntimeError("stage boundary failure")

                with self.assertRaises(RuntimeError):
                    await self.durability.run_stage(user["id"], stage_name, fail)
                stage = await self._row(
                    "select status,attempt_count from chat_turn_stages where turn_id=$1 and stage_name=$2",
                    user["id"], stage_name,
                )
                self.assertEqual((stage["status"], stage["attempt_count"]), ("failed", 1))
                self.assertEqual(
                    (await self.durability.get_turn(user["id"]))["status"], "partial"
                )

    async def test_all_completed_stages_make_turn_complete(self) -> None:
        user, _assistant = await self._core_completed_turn()
        await self._complete_except(user["id"], set())

        turn = await self.durability.get_turn(user["id"])
        self.assertEqual(turn["status"], "complete")
        self.assertIsNotNone(turn["completed_at"])
        await self._assert_user_context(user["id"])

    async def test_background_work_is_pending_before_dispatch_and_records_result(self) -> None:
        user, _assistant = await self._core_completed_turn()
        narrative = await self._row(
            "select status,attempt_count from chat_turn_stages where turn_id=$1 and stage_name='narrative'",
            user["id"],
        )
        self.assertEqual((narrative["status"], narrative["attempt_count"]), ("pending", 0))

        await self.durability.complete_noop(user["id"], "narrative")
        narrative = await self._row(
            "select status,attempt_count,completed_at from chat_turn_stages "
            "where turn_id=$1 and stage_name='narrative'",
            user["id"],
        )
        self.assertEqual((narrative["status"], narrative["attempt_count"]), ("completed", 1))
        self.assertIsNotNone(narrative["completed_at"])

    async def test_noop_ledger_failure_does_not_invalidate_durable_core(self) -> None:
        user, _assistant = await self._core_completed_turn()
        with patch.object(
            self.durability, "run_stage", side_effect=RuntimeError("ledger unavailable")
        ):
            await self.durability.complete_noop(user["id"], "decision_cancel")

        self.assertEqual((await self.durability.get_turn(user["id"]))["status"], "core_completed")

    async def test_manual_model_stage_is_never_automatically_replayed(self) -> None:
        user, _assistant = await self._core_completed_turn()
        await self._complete_except(user["id"], {"memory_extraction"})

        async def fail(_stage_pool):
            raise RuntimeError("memory model call failed")

        with self.assertRaises(RuntimeError):
            await self.durability.run_stage(
                user["id"], "memory_extraction", fail, transactional=False
            )
        replay_calls = 0

        async def forbidden(_stage_pool):
            nonlocal replay_calls
            replay_calls += 1

        await resume_turn(
            self.pool,
            user["id"],
            handlers={"memory_extraction": forbidden},
        )

        stage = await self._row(
            "select status,attempt_count from chat_turn_stages where turn_id=$1 "
            "and stage_name='memory_extraction'",
            user["id"],
        )
        self.assertEqual(replay_calls, 0)
        self.assertEqual((stage["status"], stage["attempt_count"]), ("failed", 1))

    async def test_core_failed_turn_is_not_recovered_or_sent_to_provider(self) -> None:
        user = await self.durability.begin_turn(
            ChatRequest(
                conversation_id=self.conversation_id,
                role=MessageRole.user,
                content="do not resend",
            )
        )
        await self.durability.mark_core_failed(user["id"], RuntimeError("provider"))
        calls = 0

        async def forbidden(_stage_pool):
            nonlocal calls
            calls += 1

        result = await resume_turn(
            self.pool, user["id"], handlers={"relationship": forbidden}
        )

        self.assertEqual(calls, 0)
        self.assertEqual(result["status"], "core_failed")

    async def test_incomplete_turn_query_is_bounded(self) -> None:
        identifiers = []
        for _ in range(3):
            user, _assistant = await self._core_completed_turn()
            identifiers.append(str(user["id"]))

        selected = await self.durability.incomplete_turn_ids(limit=2)

        self.assertEqual(len(selected), 2)
        self.assertTrue(set(selected).issubset(set(identifiers)))

    async def test_foreign_interrupted_provider_turn_settlement_is_atomic_and_idempotent(self) -> None:
        identifiers = []
        for index in range(5):
            user = await self.durability.begin_turn(
                ChatRequest(conversation_id=self.conversation_id, role=MessageRole.user,
                            content=f"interrupted provider {index}", source_device="android-origin")
            )
            await self.durability.mark_foreground_context_prepared(user["id"])
            await self.durability.mark_provider_started(user["id"])
            identifiers.append(str(user["id"]))

        async def settle(turn_id):
            return await self.durability.settle_interrupted_provider_turn(
                turn_id, origin_device_id="android-origin", recovery_device_id="desktop"
            )

        for turn_id in identifiers:
            self.assertTrue(await settle(turn_id))
            self.assertFalse(await settle(turn_id))
            turn = await self.durability.get_turn(turn_id)
            stages = {row["stage_name"]: row for row in await self.durability.get_stages(turn_id)}
            self.assertEqual(turn["status"], "core_failed")
            self.assertEqual(turn["safe_error_category"], "PROVIDER_INDETERMINATE")
            self.assertIsNone(turn["assistant_message_id"])
            self.assertEqual(stages["provider_generate"]["status"], "failed")
            self.assertEqual(stages["provider_generate"]["attempt_count"], 1)

        active = await self.durability.begin_turn(
            ChatRequest(conversation_id=self.conversation_id, role=MessageRole.user,
                        content="fresh active provider", source_device="android-origin")
        )
        await self.durability.mark_foreground_context_prepared(active["id"])
        await self.durability.mark_provider_started(active["id"])
        self.assertFalse(await self.durability.settle_interrupted_provider_turn(
            active["id"], origin_device_id="android-origin", recovery_device_id="android-origin"
        ))
        self.assertEqual((await self.durability.get_turn(active["id"]))["status"], "pending")

    async def test_restart_and_duplicate_resume_skip_completed_stage(self) -> None:
        user, _assistant = await self._core_completed_turn()
        await self._complete_except(user["id"], {"relationship"})

        async def effect(stage_pool):
            async with stage_pool.acquire() as connection:
                await connection.execute(
                    """insert into turn_stage_effects(effect_key,value) values('resume',1)
                       on conflict(effect_key) do update set value=value+1"""
                )

        # A fresh pool/service represents process restart on the same DB file.
        restarted_pool = LocalFilePool(self.database)
        await resume_turn(
            restarted_pool, user["id"], handlers={"relationship": effect}
        )
        await resume_turn(
            restarted_pool, user["id"], handlers={"relationship": effect}
        )

        self.assertEqual(
            await self._value("select value from turn_stage_effects where effect_key='resume'"),
            1,
        )
        self.assertEqual((await self.durability.get_turn(user["id"]))["status"], "complete")
        await self._assert_user_context(user["id"])

    async def test_restart_completes_safe_noop_stages_after_empty_prerequisites(self) -> None:
        user, _assistant = await self._core_completed_turn()
        automatic = {
            definition.name
            for definition in POST_COGNITION_STAGES
            if definition.retry_policy == "automatic"
        }
        await self._complete_except(user["id"], automatic)

        await resume_turn(self.pool, user["id"])

        stages = await self.durability.get_stages(user["id"])
        self.assertTrue(all(row["status"] == "completed" for row in stages))
        self.assertEqual((await self.durability.get_turn(user["id"]))["status"], "complete")

    async def test_concurrent_resume_claims_one_stage_owner(self) -> None:
        user, _assistant = await self._core_completed_turn()
        await self._complete_except(user["id"], {"relationship"})

        async def effect(stage_pool):
            async with stage_pool.acquire() as connection:
                await connection.execute(
                    "insert into turn_stage_effects(effect_key,value) values('concurrent',1)"
                )

        await asyncio.gather(
            resume_turn(self.pool, user["id"], handlers={"relationship": effect}),
            resume_turn(self.pool, user["id"], handlers={"relationship": effect}),
        )

        self.assertEqual(
            await self._value(
                "select count(*) from turn_stage_effects where effect_key='concurrent'"
            ),
            1,
        )

    async def test_attempt_limit_leaves_terminal_partial(self) -> None:
        user, _assistant = await self._core_completed_turn()
        await self._complete_except(user["id"], {"relationship"})

        async def fail(_stage_pool):
            raise RuntimeError("always fails")

        for _ in range(MAX_STAGE_ATTEMPTS):
            with self.assertRaises(RuntimeError):
                await self.durability.run_stage(user["id"], "relationship", fail)

        with self.assertRaises(Exception):
            await self.durability.run_stage(user["id"], "relationship", fail)
        stage = await self._row(
            "select status,attempt_count from chat_turn_stages where turn_id=$1 and stage_name='relationship'",
            user["id"],
        )
        self.assertEqual((stage["status"], stage["attempt_count"]), ("failed", MAX_STAGE_ATTEMPTS))
        self.assertEqual((await self.durability.get_turn(user["id"]))["status"], "partial")

    async def test_message_deletion_detaches_turn_and_blocks_wrong_recovery(self) -> None:
        user, _assistant = await self._core_completed_turn()
        await self._complete_except(user["id"], {"relationship"})
        self.assertTrue(await repository.delete_message(self.pool, user["id"]))

        await resume_turn(self.pool, user["id"], handlers={})

        turn = await self.durability.get_turn(user["id"])
        stage = await self._row(
            "select status,attempt_count,last_error_category from chat_turn_stages "
            "where turn_id=$1 and stage_name='relationship'",
            user["id"],
        )
        self.assertIsNone(turn["user_message_id"])
        self.assertEqual(turn["status"], "partial")
        self.assertEqual(stage["attempt_count"], MAX_STAGE_ATTEMPTS)
        self.assertEqual(stage["last_error_category"], "missing_recovery_input")
        await self._assert_user_context(user["id"])

    async def test_persona_database_files_keep_turns_isolated(self) -> None:
        other_database = Path(self.directory.name) / "other-persona.db"
        other_pool = LocalFilePool(other_database)
        await execute_script(other_pool, BASELINE_SQL)
        async with other_pool.acquire() as connection:
            await connection.execute(
                """insert into conversations(conversation_id,source_device,started_at,ended_at)
                   values($1,'test','2026-09-12T00:00:00+00:00',null)""",
                self.conversation_id,
            )

        user, _assistant = await self._core_completed_turn()

        async with other_pool.acquire() as connection:
            self.assertEqual(await connection.fetchval("select count(*) from chat_turns"), 0)
        self.assertIsNone(await TurnDurability(other_pool).get_turn(user["id"]))
        await self._assert_user_context(user["id"])


if __name__ == "__main__":
    unittest.main()
