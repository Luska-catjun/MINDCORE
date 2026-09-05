from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import unittest

import libsql

from app.database.turso import TursoConnection
from app.services.mindcore.internal_state import (
    EMOTION_BASELINES,
    _default_state,
    apply_candidate,
    apply_mood_drive_decay,
    apply_time_decay,
    build_state_context,
    evaluate_state,
    get_context_emotion_attributions,
    get_internal_state,
    get_recent_emotion_attributions,
    update_from_user_event,
)


class RecordingEmotionConnection(TursoConnection):
    def __init__(self, connection) -> None:
        super().__init__(connection)
        self.operations: list[str] = []
        self.transactions = 0
        self.fail_state_log = False

    async def execute(self, statement, *args):
        normalized = " ".join(statement.casefold().split())
        if "from diana_state" in normalized:
            self.operations.append("state_load")
        elif normalized.startswith("insert into diana_state"):
            self.operations.append("state_upsert")
        elif normalized.startswith("insert into state_log"):
            self.operations.append("state_log")
            if self.fail_state_log:
                raise RuntimeError("injected state-log failure")
        elif normalized.startswith("insert into emotion_attributions"):
            self.operations.append("attribution_insert")
        elif "from emotion_attributions" in normalized:
            self.operations.append("attribution_context")
        return await super().execute(statement, *args)

    @asynccontextmanager
    async def transaction(self):
        self.transactions += 1
        async with super().transaction():
            yield


class LocalEmotionPool:
    def __init__(self) -> None:
        self.connection = RecordingEmotionConnection(libsql.connect(":memory:"))

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


class EmotionSqlRoundTripTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.pool = LocalEmotionPool()
        async with self.pool.acquire() as connection:
            await connection.execute("""
                create table diana_state (
                    id integer primary key, emotion text not null,
                    emotion_intensity real not null, emotion_vector text not null,
                    mood_valence real not null, energy real not null,
                    curiosity real not null, stress real not null,
                    source_device text not null, updated_at text not null
                )
            """)
            await connection.execute("""
                create table state_log (
                    id text primary key, mood_valence real not null, emotion text not null,
                    emotion_intensity real not null, emotion_vector text not null,
                    energy real not null, curiosity real not null, stress real not null,
                    source_device text not null, created_at text not null
                )
            """)
            await connection.execute("""
                create table emotion_attributions (
                    emotion_attribution_id text primary key, emotion text not null,
                    delta real not null, resulting_value real not null,
                    cause_type text not null, cause_summary text not null,
                    source_type text not null, source_id text, confidence real not null,
                    created_at text not null
                )
            """)
        self.conversation_id = uuid4()
        self.message_id = uuid4()

    async def test_triggering_event_uses_one_fresh_state_read_and_single_statement_attributions(self) -> None:
        result = await update_from_user_event(
            self.pool, "너 정말 귀엽고 대단해", self.conversation_id, self.message_id
        )

        expected = apply_candidate(_default_state(), evaluate_state("너 정말 귀엽고 대단해"))
        self.assertEqual(result.state_before["emotion_vector"], EMOTION_BASELINES)
        self.assertEqual(result.state["emotion_vector"], expected["emotion_vector"])
        self.assertEqual(result.state["mood_valence"], expected["mood_valence"])
        self.assertEqual(
            self.pool.connection.operations,
            ["state_load", "state_upsert", "state_log", "attribution_insert", "attribution_insert", "attribution_insert"],
        )
        self.assertEqual(self.pool.connection.transactions, 1)

    async def test_duplicate_attribution_is_a_single_guarded_insert_noop(self) -> None:
        await update_from_user_event(self.pool, "너 정말 귀엽고 대단해", self.conversation_id, self.message_id)
        self.pool.connection.operations.clear()
        self.pool.connection.transactions = 0

        duplicate = await update_from_user_event(self.pool, "너 정말 귀엽고 대단해", self.conversation_id, self.message_id)

        self.assertEqual(duplicate.attribution_ids, [])
        self.assertEqual(
            self.pool.connection.operations,
            ["state_load", "state_upsert", "state_log", "attribution_insert", "attribution_insert", "attribution_insert"],
        )
        async with self.pool.acquire() as connection:
            self.assertEqual(await connection.fetchval("select count(*) from emotion_attributions"), 3)
        self.assertEqual(self.pool.connection.transactions, 1)

    async def test_lazy_decay_and_mood_drive_semantics_survive_the_request_scoped_result(self) -> None:
        initial = _default_state()
        initial["emotion_vector"]["curiosity"] = 1.0
        initial.update({"energy": 0.95, "updated_at": datetime.now(timezone.utc) - timedelta(hours=8)})
        async with self.pool.acquire() as connection:
            await connection.execute(
                "insert into diana_state values(1,$1,$2,$3,$4,$5,$6,$7,'test',$8)",
                initial["emotion"], initial["emotion_intensity"], initial["emotion_vector"],
                initial["mood_valence"], initial["energy"], initial["curiosity"], initial["stress"], initial["updated_at"],
            )
        self.pool.connection.operations.clear()
        result = await update_from_user_event(self.pool, "안녕", self.conversation_id, self.message_id)

        decayed, _ = apply_time_decay(result.state_before, current_time=result.state["updated_at"])
        expected, _ = apply_mood_drive_decay(decayed, current_time=result.state["updated_at"])
        self.assertAlmostEqual(result.state["emotion_vector"]["curiosity"], expected["emotion_vector"]["curiosity"])
        self.assertAlmostEqual(result.state["energy"], expected["energy"])
        self.assertEqual(self.pool.connection.operations, ["state_load", "state_upsert", "state_log", "attribution_insert"])

    async def test_attribution_context_preserves_provenance_without_a_second_state_read(self) -> None:
        result = await update_from_user_event(self.pool, "너 정말 귀엽고 대단해", self.conversation_id, self.message_id)
        self.pool.connection.operations.clear()

        attributions = await get_recent_emotion_attributions(
            self.pool, emotions=["delight", "joy", "bashfulness"]
        )
        context = build_state_context(result.state, attributions)

        self.assertEqual(self.pool.connection.operations, ["attribution_context"])
        self.assertEqual(len(attributions), 3)
        self.assertTrue(all(item["source_type"] == "message" for item in attributions))
        self.assertIn("RECENT EMOTION ATTRIBUTION", context or "")

    async def test_inactive_state_skips_unused_attribution_history_read(self) -> None:
        self.pool.connection.operations.clear()
        attributions = await get_context_emotion_attributions(self.pool, _default_state())

        self.assertEqual(attributions, [])
        self.assertEqual(self.pool.connection.operations, [])
        self.assertIsNone(build_state_context(_default_state(), [{"emotion": "joy", "delta": 1.0}]))

    async def test_renderable_state_keeps_the_existing_durable_attribution_window(self) -> None:
        result = await update_from_user_event(self.pool, "너 정말 귀엽고 대단해", self.conversation_id, self.message_id)
        self.pool.connection.operations.clear()

        attributions = await get_context_emotion_attributions(self.pool, result.state)

        self.assertEqual(self.pool.connection.operations, ["attribution_context"])
        self.assertEqual(len(attributions), 3)
        self.assertIn("RECENT EMOTION ATTRIBUTION", build_state_context(result.state, attributions) or "")

    async def test_failure_rolls_back_state_log_and_attributions_together(self) -> None:
        self.pool.connection.fail_state_log = True
        with self.assertRaisesRegex(RuntimeError, "injected state-log failure"):
            await update_from_user_event(self.pool, "너 정말 귀엽고 대단해", self.conversation_id, self.message_id)

        async with self.pool.acquire() as connection:
            self.assertIsNone(await connection.fetchrow("select * from diana_state where id=1"))
            self.assertEqual(await connection.fetchval("select count(*) from state_log"), 0)
            self.assertEqual(await connection.fetchval("select count(*) from emotion_attributions"), 0)

    async def test_restart_reads_the_persisted_final_state_not_request_memory(self) -> None:
        result = await update_from_user_event(self.pool, "이건 어떻게 작동해?", self.conversation_id, self.message_id)
        reloaded = await get_internal_state(self.pool)

        self.assertEqual(reloaded["emotion_vector"], result.state["emotion_vector"])
        self.assertEqual(reloaded["curiosity"], result.state["curiosity"])

    async def test_distinct_conversation_events_keep_distinct_message_provenance(self) -> None:
        other_message_id = uuid4()
        await update_from_user_event(self.pool, "너 정말 귀엽고 대단해", self.conversation_id, self.message_id)
        await update_from_user_event(self.pool, "너 정말 귀엽고 대단해", uuid4(), other_message_id)

        async with self.pool.acquire() as connection:
            source_ids = await connection.fetch("select distinct source_id from emotion_attributions order by source_id")
        self.assertEqual({str(row["source_id"]) for row in source_ids}, {str(self.message_id), str(other_message_id)})
