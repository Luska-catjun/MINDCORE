from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4
import unittest

import libsql

from app.database.turso import TursoConnection, TursoPool
from app.services.episode_service import delete_episode, finalize_episode_linkage
from app.services.mindcore.decisions import DecisionCandidate
from app.services.mindcore.episode_recall import retrieve_episodes_for_range
from app.services.mindcore.temporal import TemporalRange


class LocalPool:
    def __init__(self) -> None:
        self.raw = libsql.connect(":memory:")
        self.connection = TursoConnection(self.raw)

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


class Phase05RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.pool = LocalPool()
        async with self.pool.acquire() as c:
            await c.execute("""create table episodes (
                episode_id text primary key, conversation_id text, user_message_id text, assistant_message_id text,
                experience_id text, sequence integer not null, summary text not null, source_device text not null,
                importance real, emotional_impact real, personal_relevance real, relationship_impact real,
                novelty real, confidence real, recall_frequency integer not null, memory_strength real, decay real,
                created_at text not null, started_at text, ended_at text, episode_type text not null,
                topic_key text, provenance text not null, is_grounded integer not null, updated_at text not null)""")
            for table, column in (("emotion_attributions", "source_experience_id"), ("relationship_log", "source_experience_id"), ("diana_preference_evidence", "source_experience_id")):
                await c.execute(f"create table {table} ({column} text, episode_id text)")
            await c.execute("create table memories(memory_id text primary key, source_episode_id text)")
            await c.execute("create table diana_knowledge(source_episode_id text)")
            await c.execute("create table diana_knowledge_facts(source_episode_id text)")
            await c.execute("create table state_log(episode_id text)")
            await c.execute("create table messages(id text primary key, conversation_id text, role text, content text, created_at text)")

    async def _episode(self, *, conversation: str, user: str | None, assistant: str | None, experience: str | None, topic: str = "story_reading", ended: datetime | None = None) -> str:
        episode_id = str(uuid4()); now = ended or datetime.now(timezone.utc)
        async with self.pool.acquire() as c:
            await c.execute("""insert into episodes values($1,$2,$3,$4,$5,1,$6,'test',.5,.1,.1,0,.2,.7,0,.4,0,$7,$7,$7,'story_reading',$8,'grounded_event',1,$7)""", episode_id, conversation, user, assistant, experience, "prior", now, topic)
        return episode_id

    async def test_deleted_source_episode_is_never_merge_target(self) -> None:
        conversation = uuid4(); prior = await self._episode(conversation=str(conversation), user=None, assistant=None, experience=None)
        result = await finalize_episode_linkage(self.pool, conversation_id=conversation, user_message_id=uuid4(), assistant_message_id=uuid4(), experience_id=uuid4(), sequence=2, source_device="test", user_text="동화 읽어줄게", diana_text="응, 듣고 싶어!", decision=DecisionCandidate("soft_choice", "빨간 모자", .8))
        self.assertIsNotNone(result); assert result
        self.assertNotEqual(str(result["episode_id"]), prior)

    async def test_valid_story_episode_merges_within_window(self) -> None:
        conversation, user, assistant, experience = uuid4(), uuid4(), uuid4(), uuid4()
        prior = await self._episode(conversation=str(conversation), user=str(user), assistant=str(assistant), experience=str(experience), topic="story:아기돼지_삼형제")
        result = await finalize_episode_linkage(self.pool, conversation_id=conversation, user_message_id=uuid4(), assistant_message_id=uuid4(), experience_id=uuid4(), sequence=2, source_device="test", user_text="아기돼지 삼형제 동화 읽어줄게", diana_text="응, 듣고 싶어!", decision=DecisionCandidate("soft_choice", "아기돼지 삼형제", .8))
        self.assertEqual(str(result["episode_id"]), prior)

    async def test_story_episode_without_experience_does_not_merge(self) -> None:
        conversation = uuid4()
        prior = await self._episode(
            conversation=str(conversation),
            user=str(uuid4()),
            assistant=str(uuid4()),
            experience=str(uuid4()),
            topic="story:아기돼지_삼형제",
        )
        result = await finalize_episode_linkage(
            self.pool,
            conversation_id=conversation,
            user_message_id=uuid4(),
            assistant_message_id=uuid4(),
            experience_id=None,
            sequence=2,
            source_device="test",
            user_text="아기돼지 삼형제 동화 읽어줄게",
            diana_text="응, 듣고 싶어!",
            decision=DecisionCandidate("soft_choice", "아기돼지 삼형제", .8),
        )
        self.assertIsNotNone(result)
        assert result
        self.assertNotEqual(str(result["episode_id"]), prior)

    async def test_episode_delete_retains_durable_records_and_detaches_provenance(self) -> None:
        episode_id = await self._episode(
            conversation="c", user=str(uuid4()), assistant=str(uuid4()), experience=str(uuid4())
        )
        async with self.pool.acquire() as c:
            await c.execute("insert into memories values($1,$2)", str(uuid4()), episode_id)
            await c.execute("insert into diana_knowledge values($1)", episode_id)
            await c.execute("insert into diana_knowledge_facts values($1)", episode_id)
            await c.execute("insert into state_log values($1)", episode_id)
            for table in ("emotion_attributions", "relationship_log", "diana_preference_evidence"):
                await c.execute(f"insert into {table} values($1,$2)", str(uuid4()), episode_id)

        await delete_episode(self.pool, episode_id=UUID(episode_id))

        async with self.pool.acquire() as c:
            self.assertEqual(await c.fetch("select * from episodes where episode_id=$1", episode_id), [])
            for table, column in (
                ("memories", "source_episode_id"),
                ("diana_knowledge", "source_episode_id"),
                ("diana_knowledge_facts", "source_episode_id"),
                ("state_log", "episode_id"),
                ("emotion_attributions", "episode_id"),
                ("relationship_log", "episode_id"),
                ("diana_preference_evidence", "episode_id"),
            ):
                self.assertEqual(
                    await c.fetchval(f"select {column} from {table}"),
                    None,
                    f"{table}.{column}",
                )

    async def test_different_story_titles_are_topic_isolated(self) -> None:
        conversation = uuid4(); prior = await self._episode(conversation=str(conversation), user=str(uuid4()), assistant=str(uuid4()), experience=str(uuid4()), topic="story:아기돼지_삼형제")
        result = await finalize_episode_linkage(self.pool, conversation_id=conversation, user_message_id=uuid4(), assistant_message_id=uuid4(), experience_id=uuid4(), sequence=2, source_device="test", user_text="동화 북풍과 태양 읽어줄게", diana_text="응!", decision=DecisionCandidate("soft_choice", "북풍과 태양", .8))
        self.assertNotEqual(str(result["episode_id"]), prior)

    async def test_legacy_generic_story_topic_does_not_absorb_named_story(self) -> None:
        conversation = uuid4()
        prior = await self._episode(conversation=str(conversation), user=str(uuid4()), assistant=str(uuid4()), experience=str(uuid4()), topic="story_reading")
        result = await finalize_episode_linkage(self.pool, conversation_id=conversation, user_message_id=uuid4(), assistant_message_id=uuid4(), experience_id=uuid4(), sequence=2, source_device="test", user_text="아기돼지 삼형제 동화 읽어줄게", diana_text="응!", decision=DecisionCandidate("soft_choice", "아기돼지 삼형제", .8))
        self.assertNotEqual(str(result["episode_id"]), prior)

    async def test_raw_message_fallback_returns_only_option_like_user_messages(self) -> None:
        now = datetime.now(timezone.utc)
        async with self.pool.acquire() as c:
            await c.execute("insert into messages values($1,$2,'user',$3,$4)", str(uuid4()), "c", "빨간 모자, 아기돼지 삼형제, 토끼와 거북이 중 골라봐", now - timedelta(hours=1))
            await c.execute("insert into messages values($1,$2,'user',$3,$4)", str(uuid4()), "c", "오늘 날씨 좋다", now - timedelta(hours=1))
        result = await retrieve_episodes_for_range(self.pool, TemporalRange("어제", now - timedelta(days=1), now + timedelta(seconds=1)))
        self.assertEqual(len(result.episodes), 0)
        self.assertEqual(len(result.raw_messages), 1)
        self.assertIn("토끼와 거북이", result.raw_messages[0]["content"])

    async def test_turso_adapter_rolls_back_multiple_writes(self) -> None:
        async with self.pool.acquire() as c:
            await c.execute("create table transaction_probe(value integer)")
            with self.assertRaisesRegex(RuntimeError, "forced"):
                async with c.transaction():
                    await c.execute("insert into transaction_probe values($1)", 1)
                    await c.execute("insert into transaction_probe values($1)", 2)
                    raise RuntimeError("forced")
            self.assertEqual(await c.fetch("select * from transaction_probe"), [])

    async def test_turso_pool_does_not_commit_another_acquire_transaction(self) -> None:
        with TemporaryDirectory() as directory:
            pool = TursoPool(str(Path(directory) / "transaction-isolation.db"), "test-token")
            async with pool.acquire() as connection:
                await connection.execute("create table transaction_probe(value integer)")

            inserted = asyncio.Event()
            release_writer = asyncio.Event()

            async def write_uncommitted() -> None:
                async with pool.acquire() as writer:
                    async with writer.transaction():
                        await writer.execute("insert into transaction_probe values($1)", 1)
                        inserted.set()
                        await release_writer.wait()

            writer_task = asyncio.create_task(write_uncommitted())
            await inserted.wait()
            async with pool.acquire() as reader:
                self.assertEqual(await reader.fetchval("select count(*) from transaction_probe"), 0)
            release_writer.set()
            await writer_task
            async with pool.acquire() as reader:
                self.assertEqual(await reader.fetchval("select count(*) from transaction_probe"), 1)
            await pool.close()
