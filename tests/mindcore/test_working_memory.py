from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
import unittest

import libsql

from app.database.turso import TursoConnection
from app.services.mindcore.working_memory import (
    WorkingMemoryItem,
    WorkingMemoryState,
    ACTIVE_SKILL_TTL,
    build_working_memory_context,
    clear_working_memory,
    load_working_memory,
    persist_working_memory,
    record_open_loop,
    update_working_memory_persistent,
)


class RecordingTursoConnection(TursoConnection):
    def __init__(self, connection) -> None:
        super().__init__(connection)
        self.operations: list[str] = []
        self.transactions = 0

    async def execute(self, statement, *args):
        normalized = statement.lstrip().casefold()
        if normalized.startswith("select") and "diana_working_memory_items" in normalized:
            self.operations.append("wm_load")
        elif normalized.startswith("insert into diana_working_memory_items"):
            self.operations.append("wm_upsert")
        elif normalized.startswith("update diana_working_memory_items"):
            self.operations.append("wm_maintenance")
        return await super().execute(statement, *args)

    @asynccontextmanager
    async def transaction(self):
        self.transactions += 1
        async with super().transaction():
            yield


class LocalPool:
    def __init__(self) -> None:
        self.connection = RecordingTursoConnection(libsql.connect(":memory:"))

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


class WorkingMemoryPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.pool = LocalPool()
        async with self.pool.acquire() as connection:
            await connection.execute("create table conversations(conversation_id text primary key)")
            migration = Path("db/migrations/017_working_memory_v01.sql").read_text()
            for statement in migration.split(";"):
                if statement.strip():
                    await connection.execute(statement)
        self.conversation_id = uuid4()
        self.other_conversation_id = uuid4()
        async with self.pool.acquire() as connection:
            await connection.execute("insert into conversations(conversation_id) values($1)", self.conversation_id)
            await connection.execute("insert into conversations(conversation_id) values($1)", self.other_conversation_id)

    async def test_restart_round_trip_and_conversation_isolation(self) -> None:
        state = await update_working_memory_persistent(
            self.pool, self.conversation_id, "working_memory 설계를 이어서 하자", []
        )
        self.assertEqual(state.current_focus, "Working Memory")
        clear_working_memory(self.conversation_id)

        reloaded = await load_working_memory(self.pool, self.conversation_id)
        self.assertEqual(reloaded.current_focus, "Working Memory")
        self.assertEqual((await load_working_memory(self.pool, self.other_conversation_id)).items, [])

    async def test_active_skill_uses_four_hour_ttl_including_legacy_rows(self) -> None:
        await update_working_memory_persistent(self.pool, self.conversation_id, "업다운 하자", [])
        now = datetime.now(timezone.utc)
        async with self.pool.acquire() as connection:
            # Keep a legacy-style 18h expires_at deliberately: load must still
            # enforce the new active_skill age policy from last_touched_at.
            await connection.execute(
                "update diana_working_memory_items set last_touched_at=$1, expires_at=$2 where conversation_id=$3 and slot_type='active_skill'",
                now - ACTIVE_SKILL_TTL + timedelta(minutes=1), now + timedelta(hours=14), self.conversation_id,
            )
        self.assertEqual((await load_working_memory(self.pool, self.conversation_id, now=now)).active_skill_id, "game.updown")
        self.assertIsNone((await load_working_memory(self.pool, self.conversation_id, now=now + timedelta(minutes=2))).active_skill_id)
        async with self.pool.acquire() as connection:
            await connection.execute(
                "update diana_working_memory_items set last_touched_at=$1 where conversation_id=$2 and slot_type='active_skill'",
                now - ACTIVE_SKILL_TTL - timedelta(minutes=1), self.conversation_id,
            )
        await update_working_memory_persistent(self.pool, self.conversation_id, "ㅋㅋ", [])
        async with self.pool.acquire() as connection:
            self.assertEqual(await connection.fetchval("select status from diana_working_memory_items where conversation_id=$1 and slot_type='active_skill'", self.conversation_id), "expired")

    async def test_filler_does_not_replace_focus_and_switch_keeps_prior_topic(self) -> None:
        await update_working_memory_persistent(
            self.pool, self.conversation_id, "working_memory 설계를 이어서 하자", []
        )
        after_filler = await update_working_memory_persistent(self.pool, self.conversation_id, "ㅋㅋ", [])
        self.assertEqual(after_filler.current_focus, "Working Memory")

        switched = await update_working_memory_persistent(
            self.pool, self.conversation_id, "world_model 얘기로 돌아가자", []
        )
        self.assertEqual(switched.current_focus, "World Model")
        self.assertEqual(
            {item.summary for item in switched.items if item.slot_type == "active_topic"},
            {"Working Memory", "World Model"},
        )

    async def test_memory_reference_is_short_lived_reference_not_new_knowledge(self) -> None:
        state = await update_working_memory_persistent(
            self.pool,
            self.conversation_id,
            "working_memory에서 이 기억을 참고해",
            [{"memory_id": "memory-1", "content": "사용자가 직접 말한 grounded memory"}],
        )
        reference = next(item for item in state.items if item.slot_type == "active_memory_ref")
        self.assertEqual(reference.source_id, "memory-1")
        self.assertEqual(reference.source_type, "memory")
        self.assertIn("Active grounded memory references", build_working_memory_context(state, 1) or "")

    async def test_open_loop_persists_filler_does_not_resolve_and_substantive_reply_does(self) -> None:
        state = await update_working_memory_persistent(
            self.pool, self.conversation_id, "working_memory 이야기를 하자", []
        )
        await record_open_loop(self.pool, state, "오늘 무엇을 먼저 해볼까?", uuid4())
        clear_working_memory(self.conversation_id)
        self.assertEqual(
            len([item for item in (await load_working_memory(self.pool, self.conversation_id)).items if item.slot_type == "open_loop"]),
            1,
        )

        await update_working_memory_persistent(self.pool, self.conversation_id, "ㅋㅋ", [])
        self.assertEqual(
            len([item for item in (await load_working_memory(self.pool, self.conversation_id)).items if item.slot_type == "open_loop"]),
            1,
        )
        await update_working_memory_persistent(self.pool, self.conversation_id, "먼저 기억 저장 흐름부터 확인하자", [])
        self.assertEqual(
            len([item for item in (await load_working_memory(self.pool, self.conversation_id)).items if item.slot_type == "open_loop"]),
            0,
        )
        async with self.pool.acquire() as connection:
            self.assertEqual(
                await connection.fetchval(
                    "select status from diana_working_memory_items where conversation_id=$1 and slot_type='open_loop'",
                    self.conversation_id,
                ),
                "resolved",
            )

    async def test_conversation_delete_cascades_working_memory(self) -> None:
        await update_working_memory_persistent(
            self.pool, self.conversation_id, "working_memory 설계를 이어서 하자", []
        )
        async with self.pool.acquire() as connection:
            await connection.execute("delete from conversations where conversation_id=$1", self.conversation_id)
            self.assertEqual(
                await connection.fetchval(
                    "select count(*) from diana_working_memory_items where conversation_id=$1", self.conversation_id
                ),
                0,
            )

    async def test_multi_row_persistence_reduces_real_libsql_operations(self) -> None:
        self.pool.connection.operations.clear()
        self.pool.connection.transactions = 0
        await update_working_memory_persistent(
            self.pool, self.conversation_id, "working_memory에서 여러 기억을 참고하자",
            [{"memory_id": "memory-1", "content": "first"}, {"memory_id": "memory-2", "content": "second"}],
        )
        # One durable load, then one real multi-row UPSERT and one maintenance
        # update. The former implementation issued three UPSERTs here.
        self.assertEqual(self.pool.connection.operations, ["wm_load", "wm_upsert", "wm_maintenance"])
        self.assertEqual(self.pool.connection.transactions, 1)

    async def test_substantive_resolution_merges_resolve_and_expiry_maintenance(self) -> None:
        state = await update_working_memory_persistent(self.pool, self.conversation_id, "working_memory 이야기를 하자", [])
        await record_open_loop(self.pool, state, "무엇을 먼저 해볼까?", uuid4())
        self.pool.connection.operations.clear()
        self.pool.connection.transactions = 0

        final = await update_working_memory_persistent(self.pool, self.conversation_id, "먼저 설계를 검토하고 구현하자", [])

        self.assertEqual(self.pool.connection.operations, ["wm_load", "wm_upsert", "wm_maintenance"])
        self.assertEqual(self.pool.connection.transactions, 1)
        self.assertFalse(any(item.slot_type == "open_loop" for item in final.items))

    async def test_post_question_reuses_pre_snapshot_without_a_full_reload(self) -> None:
        state = await update_working_memory_persistent(self.pool, self.conversation_id, "working_memory 이야기를 하자", [])
        self.pool.connection.operations.clear()
        self.pool.connection.transactions = 0

        await record_open_loop(self.pool, state, "다음에는 무엇을 할까?", uuid4())

        self.assertEqual(self.pool.connection.operations, ["wm_upsert", "wm_maintenance"])
        self.assertEqual(self.pool.connection.transactions, 1)

    async def test_multi_row_failure_rolls_back_all_items(self) -> None:
        now = (await load_working_memory(self.pool, self.conversation_id)).updated_at
        state = WorkingMemoryState(self.conversation_id, [
            WorkingMemoryItem("valid", "Valid", .8, touched_at=now),
            WorkingMemoryItem("invalid", "Invalid", .8, slot_type="invalid", touched_at=now),
        ], now)

        with self.assertRaises(ValueError):
            await persist_working_memory(self.pool, state)

        async with self.pool.acquire() as connection:
            self.assertEqual(await connection.fetchval("select count(*) from diana_working_memory_items where conversation_id=$1", self.conversation_id), 0)
