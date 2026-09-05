from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import uuid4
import unittest

import libsql

from app.database.turso import TursoConnection
from app.services.episode_service import finalize_episode_linkage
from app.services.mindcore.decisions import DecisionCandidate
from app.services.mindcore.knowledge import _story_focus, acquire_user_knowledge


class RecordingConnection(TursoConnection):
    def __init__(self) -> None:
        super().__init__(libsql.connect(":memory:"))
        self.operations: list[str] = []
        self._fetching = False
        self.fail_contains: str | None = None

    def _record(self, sql: str) -> None:
        query = " ".join(sql.casefold().split())
        if "from episodes where user_message_id" in query:
            operation = "episode_duplicate_select"
        elif query.startswith("insert into episodes"):
            operation = "episode_upsert"
        elif "from diana_knowledge where subject_key in" in query:
            operation = "knowledge_batch_lookup"
        elif "from diana_knowledge_facts where knowledge_id=$1" in query and "order by" not in query:
            operation = "facts_snapshot"
        elif "from diana_knowledge_facts" in query and "order by" in query:
            operation = "facts_summary"
        elif query.startswith("insert into diana_knowledge_facts"):
            operation = "fact_insert"
        elif query.startswith("update diana_knowledge_facts"):
            operation = "fact_reinforce"
        elif query.startswith("insert into diana_knowledge"):
            operation = "knowledge_insert"
        elif query.startswith("update diana_knowledge"):
            operation = "knowledge_update"
        else:
            return
        self.operations.append(operation)

    async def fetch(self, sql, *args):
        self._record(sql)
        self._fetching = True
        try:
            return await super().fetch(sql, *args)
        finally:
            self._fetching = False

    async def execute(self, sql, *args):
        if self.fail_contains and self.fail_contains in " ".join(sql.casefold().split()):
            raise RuntimeError("injected persistence failure")
        if not self._fetching:
            self._record(sql)
        return await super().execute(sql, *args)


class LocalPool:
    def __init__(self) -> None:
        self.connection = RecordingConnection()

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


class EpisodeKnowledgeSqlRoundTripTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.pool = LocalPool()
        _story_focus.clear()
        async with self.pool.acquire() as connection:
            await connection.execute(
                """create table episodes (
                    episode_id text primary key, conversation_id text, user_message_id text,
                    assistant_message_id text, experience_id text, sequence integer not null,
                    summary text not null, source_device text not null, importance real,
                    emotional_impact real, personal_relevance real, relationship_impact real,
                    novelty real, confidence real, recall_frequency integer not null,
                    memory_strength real, decay real, created_at text not null, started_at text,
                    ended_at text, episode_type text not null, topic_key text, provenance text not null,
                    is_grounded integer not null, updated_at text not null)"""
            )
            await connection.execute(
                "create unique index uq_episodes_user_message on episodes(user_message_id) where user_message_id is not null"
            )
            for table in ("emotion_attributions", "relationship_log", "diana_preference_evidence"):
                await connection.execute(f"create table {table}(source_experience_id text, episode_id text)")
            await connection.execute("create table memories(memory_id text primary key, source_episode_id text)")
            await connection.execute(
                """create table diana_knowledge (
                    knowledge_id text primary key, subject_key text unique, canonical_name text,
                    aliases text, knowledge_type text, summary text, confidence real, status text,
                    source_type text, source_id text, source_episode_id text, first_learned_at text,
                    last_reinforced_at text, reinforcement_count integer, created_at text, updated_at text,
                    learning_session_count integer, last_learning_session_id text)"""
            )
            await connection.execute(
                """create table diana_knowledge_facts (
                    knowledge_fact_id text primary key, knowledge_id text, fact_key text,
                    fact_text text, knowledge_scope text, source_type text, source_message_id text,
                    source_episode_id text, confidence real, reinforcement_count integer,
                    contradiction_count integer, first_learned_at text, last_reinforced_at text,
                    last_contradicted_at text, created_at text, updated_at text,
                    unique(knowledge_id, fact_key))"""
            )

    async def test_normal_episode_uses_partial_unique_upsert_without_preselect(self) -> None:
        user_message_id = uuid4()
        result = await finalize_episode_linkage(
            self.pool, conversation_id=uuid4(), user_message_id=user_message_id,
            assistant_message_id=uuid4(), experience_id=None, sequence=1, source_device="test",
            user_text="오늘 무엇을 하고 싶어?", diana_text="책을 읽어보고 싶어!",
            decision=DecisionCandidate("soft_choice", "책", 0.8),
        )
        self.assertIsNotNone(result)
        self.assertEqual(self.pool.connection.operations.count("episode_duplicate_select"), 0)
        self.assertEqual(self.pool.connection.operations.count("episode_upsert"), 1)
        first_id = await self.pool.connection.fetchval(
            "select episode_id from episodes where user_message_id=$1", user_message_id
        )
        self.pool.connection.operations.clear()
        await finalize_episode_linkage(
            self.pool, conversation_id=uuid4(), user_message_id=user_message_id,
            assistant_message_id=uuid4(), experience_id=None, sequence=1, source_device="test",
            user_text="오늘 무엇을 하고 싶어?", diana_text="책을 읽어보고 싶어!",
            decision=DecisionCandidate("soft_choice", "책", 0.8),
        )
        self.assertEqual(self.pool.connection.operations, ["episode_upsert"])
        self.assertEqual(
            await self.pool.connection.fetchval("select episode_id from episodes where user_message_id=$1", user_message_id),
            first_id,
        )

    async def test_story_fact_snapshot_reuses_one_turn_collection_and_preserves_duplicates(self) -> None:
        conversation_id, message_id, episode_id = uuid4(), uuid4(), uuid4()
        text = "빨간 모자라는 아이가 숲에 갔어, 늑대가 따라왔어."
        stored = await acquire_user_knowledge(
            self.pool, user_text=text, user_message_id=message_id, source_episode_id=episode_id,
            episode_is_grounded=True, conversation_id=conversation_id,
        )
        self.assertEqual(len(stored), 1)
        self.assertEqual(self.pool.connection.operations.count("knowledge_batch_lookup"), 1)
        self.assertEqual(self.pool.connection.operations.count("facts_snapshot"), 1)
        self.assertEqual(self.pool.connection.operations.count("fact_insert"), 2)
        self.assertNotIn("fact_reinforce", self.pool.connection.operations)
        knowledge_id = stored[0]["knowledge_id"]
        self.assertEqual(
            await self.pool.connection.fetchval(
                "select count(*) from diana_knowledge_facts where knowledge_id=$1", knowledge_id
            ),
            2,
        )
        self.pool.connection.operations.clear()
        reloaded = await acquire_user_knowledge(
            self.pool, user_text=text, user_message_id=uuid4(), source_episode_id=episode_id,
            episode_is_grounded=True, conversation_id=conversation_id,
        )
        self.assertEqual(len(reloaded), 1)
        self.assertEqual(self.pool.connection.operations.count("knowledge_batch_lookup"), 1)
        self.assertEqual(self.pool.connection.operations.count("facts_snapshot"), 1)
        self.assertEqual(self.pool.connection.operations.count("fact_insert"), 0)
        self.assertEqual(self.pool.connection.operations.count("fact_reinforce"), 2)
        self.assertEqual(
            await self.pool.connection.fetchval(
                "select count(*) from diana_knowledge_facts where knowledge_id=$1", knowledge_id
            ),
            2,
        )

    async def test_episode_provenance_failure_rolls_back_episode_body(self) -> None:
        self.pool.connection.fail_contains = "update relationship_log set"
        with self.assertRaisesRegex(RuntimeError, "injected persistence failure"):
            await finalize_episode_linkage(
                self.pool, conversation_id=uuid4(), user_message_id=uuid4(),
                assistant_message_id=uuid4(), experience_id=uuid4(), sequence=1, source_device="test",
                user_text="오늘 무엇을 하고 싶어?", diana_text="책을 읽어보고 싶어!",
                decision=DecisionCandidate("soft_choice", "책", 0.8),
            )
        self.assertEqual(await self.pool.connection.fetchval("select count(*) from episodes"), 0)

    async def test_knowledge_parent_failure_rolls_back_facts_and_knowledge(self) -> None:
        self.pool.connection.fail_contains = "update diana_knowledge set"
        with self.assertRaisesRegex(RuntimeError, "injected persistence failure"):
            await acquire_user_knowledge(
                self.pool, user_text="빨간 모자라는 아이가 숲에 갔어.",
                user_message_id=uuid4(), source_episode_id=uuid4(), episode_is_grounded=True,
                conversation_id=uuid4(),
            )
        self.assertEqual(await self.pool.connection.fetchval("select count(*) from diana_knowledge"), 0)
        self.assertEqual(await self.pool.connection.fetchval("select count(*) from diana_knowledge_facts"), 0)

    async def test_ordinary_nonknowledge_turn_does_not_open_knowledge_sql_path(self) -> None:
        self.pool.connection.operations.clear()
        stored = await acquire_user_knowledge(
            self.pool, user_text="오늘 친구들이랑 놀았는데 재밌었어.", user_message_id=uuid4(),
            source_episode_id=None, episode_is_grounded=True, conversation_id=uuid4(),
        )
        self.assertEqual(stored, [])
        self.assertEqual(self.pool.connection.operations, [])
