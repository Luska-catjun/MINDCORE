from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import uuid4
import unittest

import libsql

from app.database.turso import TursoConnection
from app.services.memory_service import reinforce_recalled_memories
from app.services.mindcore.diana_preferences import update_diana_preference_from_experience
from app.services.mindcore.preferences import update_preference_from_experience


class RecordingConnection(TursoConnection):
    def __init__(self, connection) -> None:
        super().__init__(connection)
        self.operations: list[str] = []
        self.fail_user_preference_update = False
        self.fail_diana_preference_update = False

    async def execute(self, statement, *args):
        normalized = " ".join(statement.casefold().split())
        if "update memories" in normalized:
            self.operations.append("memory_reinforce")
        elif "from preferences where owner_type" in normalized:
            self.operations.append("user_preference_load")
        elif "insert into preference_evidence" in normalized:
            self.operations.append("user_evidence_insert")
        elif normalized.startswith("update preferences"):
            self.operations.append("user_preference_update")
            if self.fail_user_preference_update:
                raise RuntimeError("injected preference update failure")
        elif "from diana_preferences where subject_key" in normalized:
            self.operations.append("diana_preference_load")
        elif "insert into diana_preference_evidence" in normalized:
            self.operations.append("diana_evidence_insert")
        elif "count(distinct e.conversation_id)" in normalized:
            self.operations.append("diana_conversation_count")
        elif normalized.startswith("update diana_preferences"):
            self.operations.append("diana_preference_update")
            if self.fail_diana_preference_update:
                raise RuntimeError("injected diana preference update failure")
        return await super().execute(statement, *args)


class LocalPool:
    def __init__(self) -> None:
        self.connection = RecordingConnection(libsql.connect(":memory:"))

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


class PostTurnSqlRoundTripTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.pool = LocalPool()
        async with self.pool.acquire() as c:
            await c.execute("""create table memories(
                memory_id text primary key, recall_frequency integer not null,
                last_recalled_at text, memory_strength real not null)""")
            await c.execute("""create table preferences(
                preference_id text primary key, owner_type text, subject text, value text,
                preference_type text, status text, confidence real, evidence_count integer,
                first_seen_at text, last_seen_at text, created_at text, updated_at text)""")
            await c.execute("""create table preference_evidence(
                evidence_id text primary key, preference_id text, experience_id text,
                message_id text, evidence_type text, direction integer, strength real, created_at text)""")
            await c.execute("""create table experiences(experience_id text primary key, conversation_id text)""")
            await c.execute("""create table diana_preferences(
                diana_preference_id text primary key, subject_key text unique, display_name text,
                status text, affinity real, confidence real, evidence_count integer,
                positive_evidence integer, negative_evidence integer, curiosity_evidence integer,
                first_observed_at text, last_observed_at text, stabilized_at text,
                created_at text, updated_at text)""")
            await c.execute("""create table diana_preference_evidence(
                diana_preference_evidence_id text primary key, diana_preference_id text,
                subject_key text, signal_type text, signal_value real,
                source_emotion_attribution_id text, source_experience_id text,
                source_message_id text, created_at text)""")

    async def test_recalled_memory_batch_preserves_duplicate_reinforcement_in_one_statement(self) -> None:
        first, second = uuid4(), uuid4()
        async with self.pool.acquire() as c:
            await c.execute("insert into memories values($1,0,null,.10)", first)
            await c.execute("insert into memories values($1,0,null,.20)", second)
        self.pool.connection.operations.clear()

        await reinforce_recalled_memories(self.pool, [{"memory_id": first}, {"memory_id": second}, {"memory_id": first}])

        async with self.pool.acquire() as c:
            rows = await c.fetch("select memory_id,recall_frequency,memory_strength from memories")
        values = {str(row["memory_id"]): (row["recall_frequency"], row["memory_strength"]) for row in rows}
        self.assertEqual(values[str(first)][0], 2)
        self.assertAlmostEqual(values[str(first)][1], .16)
        self.assertEqual(values[str(second)][0], 1)
        self.assertAlmostEqual(values[str(second)][1], .23)
        self.assertEqual(self.pool.connection.operations, ["memory_reinforce"])

    async def test_user_preference_evidence_insert_first_avoids_duplicate_preselect(self) -> None:
        experience_id, message_id = uuid4(), uuid4()
        result = await update_preference_from_experience(
            self.pool, experience_id, message_id, "I like jasmine tea"
        )
        self.assertEqual(result["evidence_count"], 1)
        self.assertEqual(self.pool.connection.operations, [
            "user_preference_load", "user_evidence_insert", "user_preference_update"
        ])

        self.pool.connection.operations.clear()
        duplicate = await update_preference_from_experience(
            self.pool, experience_id, message_id, "I like jasmine tea"
        )
        self.assertEqual(duplicate["evidence_count"], 1)
        self.assertEqual(self.pool.connection.operations, ["user_preference_load", "user_evidence_insert"])

    async def test_user_preference_failure_rolls_back_new_evidence(self) -> None:
        preference_id, experience_id, message_id = uuid4(), uuid4(), uuid4()
        async with self.pool.acquire() as c:
            await c.execute(
                "insert into preferences values($1,'user','general','jasmine tea','like','candidate',0,0,$2,$2,$2,$2)",
                preference_id, "2026-01-01T00:00:00+00:00",
            )
        self.pool.connection.fail_user_preference_update = True

        with self.assertRaisesRegex(RuntimeError, "injected preference update failure"):
            await update_preference_from_experience(self.pool, experience_id, message_id, "I like jasmine tea")
        async with self.pool.acquire() as c:
            self.assertEqual(await c.fetchval("select count(*) from preference_evidence"), 0)
            self.assertEqual(await c.fetchval("select evidence_count from preferences where preference_id=$1", preference_id), 0)

    async def test_diana_preference_reuses_current_turn_attributions_and_insert_first_evidence(self) -> None:
        experience_id, message_id, attribution_id = uuid4(), uuid4(), uuid4()
        async with self.pool.acquire() as c:
            await c.execute("insert into experiences values($1,$2)", experience_id, uuid4())
        self.pool.connection.operations.clear()

        result = await update_diana_preference_from_experience(
            self.pool, experience_id=experience_id, message_id=message_id,
            user_text="자스민차 좋아", message_attributions=({
                "emotion_attribution_id": attribution_id, "emotion": "joy", "delta": .14,
            },),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["evidence_count"], 1)
        self.assertEqual(self.pool.connection.operations, [
            "diana_preference_load", "diana_evidence_insert",
            "diana_conversation_count", "diana_preference_update",
        ])

        self.pool.connection.operations.clear()
        duplicate = await update_diana_preference_from_experience(
            self.pool, experience_id=experience_id, message_id=message_id,
            user_text="자스민차 좋아", message_attributions=({
                "emotion_attribution_id": attribution_id, "emotion": "joy", "delta": .14,
            },),
        )
        self.assertEqual(duplicate["evidence_count"], 1)
        self.assertEqual(self.pool.connection.operations, ["diana_preference_load", "diana_evidence_insert"])

    async def test_diana_preference_failure_rolls_back_insert_first_evidence(self) -> None:
        experience_id, message_id, attribution_id, preference_id = uuid4(), uuid4(), uuid4(), uuid4()
        async with self.pool.acquire() as c:
            await c.execute("insert into experiences values($1,$2)", experience_id, uuid4())
            await c.execute(
                """insert into diana_preferences values(
                    $1,'jasmine_tea','jasmine tea','curious',0,0,0,0,0,0,
                    $2,$2,null,$2,$2)""",
                preference_id, "2026-01-01T00:00:00+00:00",
            )
        self.pool.connection.fail_diana_preference_update = True

        with self.assertRaisesRegex(RuntimeError, "injected diana preference update failure"):
            await update_diana_preference_from_experience(
                self.pool, experience_id=experience_id, message_id=message_id,
                user_text="자스민차 좋아", message_attributions=({
                    "emotion_attribution_id": attribution_id, "emotion": "joy", "delta": .14,
                },),
            )
        async with self.pool.acquire() as c:
            self.assertEqual(await c.fetchval("select count(*) from diana_preference_evidence"), 0)
            self.assertEqual(
                await c.fetchval("select evidence_count from diana_preferences where diana_preference_id=$1", preference_id),
                0,
            )
