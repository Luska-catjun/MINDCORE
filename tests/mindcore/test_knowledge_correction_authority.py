from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID, uuid4
import unittest
from unittest.mock import patch

import libsql

from app.config import Settings
from app.database.turso import TursoConnection
from app.routers.observe import observe_knowledge
from app.services.gemini import _call_gemini_sync
from app.services.mindcore.context_builder import build_context
from app.services.mindcore.internal_state import _default_state
from app.services.mindcore.knowledge import check_epistemic_state
from app.services.mindcore import observation_corrections
from app.services.prompt_loader import load_persona_identity_prompt


class LocalKnowledgePool:
    def __init__(self) -> None:
        self.connection = TursoConnection(libsql.connect(":memory:"))

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


class KnowledgeCorrectionAuthorityTests(unittest.IsolatedAsyncioTestCase):
    async def _create_pool(self, *, summary: str = "Project X uses PostgreSQL") -> tuple[LocalKnowledgePool, UUID, UUID]:
        pool = LocalKnowledgePool()
        knowledge_id, message_id = uuid4(), uuid4()
        timestamp = datetime(2026, 9, 9, tzinfo=timezone.utc)
        async with pool.acquire() as connection:
            await connection.execute("pragma foreign_keys=on")
            await connection.execute("create table messages(id text primary key)")
            await connection.execute("""
                create table diana_knowledge (
                    knowledge_id text primary key, subject_key text unique, canonical_name text,
                    aliases text, knowledge_type text, summary text, confidence real, status text,
                    source_type text, source_id text, source_episode_id text, first_learned_at text,
                    last_reinforced_at text, reinforcement_count integer, created_at text, updated_at text,
                    learning_session_count integer, last_learning_session_id text
                )
            """)
            await connection.execute("""
                create table diana_knowledge_facts (
                    knowledge_fact_id text primary key, knowledge_id text,
                    fact_key text, fact_text text, knowledge_scope text, source_type text,
                    source_message_id text references messages(id) on delete set null,
                    source_episode_id text, confidence real, reinforcement_count integer,
                    contradiction_count integer, first_learned_at text, last_reinforced_at text,
                    last_contradicted_at text, created_at text, updated_at text,
                    unique(knowledge_id, fact_key)
                )
            """)
            await connection.execute("insert into messages values($1)", message_id)
            await connection.execute(
                """insert into diana_knowledge values(
                       $1,'project_x','Project X','[]','story',$2,.8,'known','user_story',$3,null,
                       $4,$4,2,$4,$4,1,$3)""",
                knowledge_id, summary, message_id, timestamp,
            )
            await connection.execute(
                """insert into diana_knowledge_facts values(
                       $1,$2,'uses_postgresql',$3,'fictional_story','user_story',$4,null,
                       .8,2,0,$5,$5,null,$5,$5)""",
                uuid4(), knowledge_id, summary, message_id, timestamp,
            )
        return pool, knowledge_id, message_id

    async def _lookup(self, pool: LocalKnowledgePool) -> tuple[list[dict], str]:
        items, context = await check_epistemic_state(pool, "Project X는 뭐야?")
        self.assertEqual(len(items), 1)
        return items, context or ""

    def _provider_payload(self, dynamic_context: str) -> str:
        settings = Settings(_env_file=None, gemini_api_key="synthetic-test-key")
        with patch("app.services.gemini._request_gemini_sync", return_value="ok") as request:
            self.assertEqual(
                _call_gemini_sync(
                    settings,
                    "Project X는 뭐야?",
                    dynamic_context=dynamic_context,
                    identity_prompt=load_persona_identity_prompt(settings),
                ),
                "ok",
            )
        return str(request.call_args.args[1])

    async def test_correction_is_authoritative_in_observation_epistemic_context_and_provider_payload(self) -> None:
        pool, knowledge_id, _message_id = await self._create_pool()

        await observation_corrections.update_knowledge(pool, knowledge_id, "Project X uses Turso")

        observed = await observe_knowledge(limit=50, offset=0, sort="recent", search=None, pool=pool)
        self.assertEqual(observed["items"][0]["summary"], "Project X uses Turso")
        self.assertTrue(observed["items"][0]["correction_active"])
        self.assertFalse(observed["items"][0]["facts"][0]["active"])
        items, epistemic_context = await self._lookup(pool)
        self.assertTrue(items[0]["correction_active"])
        self.assertIn("Project X uses Turso", epistemic_context)
        self.assertNotIn("Project X uses PostgreSQL", epistemic_context)

        built = build_context(
            current_user_message="Project X는 뭐야?", recent_messages=[], memories=[],
            internal_state=_default_state(), working_memory=None,
            epistemic_context=epistemic_context,
        ).dynamic_context or ""
        payload = self._provider_payload(built)
        self.assertIn("Project X uses Turso", payload)
        self.assertNotIn("Project X uses PostgreSQL", payload)

    async def test_repeated_and_contradictory_corrections_keep_latest_value_without_fact_duplication(self) -> None:
        pool, knowledge_id, _message_id = await self._create_pool()
        await observation_corrections.update_knowledge(pool, knowledge_id, "Project X uses Turso")
        await observation_corrections.update_knowledge(pool, knowledge_id, "Project X uses Turso")
        await observation_corrections.update_knowledge(pool, knowledge_id, "Project X uses SQLite")

        _items, context = await self._lookup(pool)
        self.assertIn("Project X uses SQLite", context)
        self.assertNotIn("Project X uses Turso", context)
        self.assertNotIn("Project X uses PostgreSQL", context)
        async with pool.acquire() as connection:
            self.assertEqual(await connection.fetchval("select count(*) from diana_knowledge_facts"), 1)
            self.assertEqual(await connection.fetchval("select summary from diana_knowledge where knowledge_id=$1", knowledge_id), "Project X uses SQLite")

    async def test_message_provenance_detaches_without_losing_correction_or_fact_history(self) -> None:
        pool, knowledge_id, message_id = await self._create_pool()
        await observation_corrections.update_knowledge(pool, knowledge_id, "Project X uses Turso")
        async with pool.acquire() as connection:
            fact_before = await connection.fetchrow("select * from diana_knowledge_facts where knowledge_id=$1", knowledge_id)
            await connection.execute("delete from messages where id=$1", message_id)
            fact_after = await connection.fetchrow("select * from diana_knowledge_facts where knowledge_id=$1", knowledge_id)

        self.assertEqual(fact_after["knowledge_fact_id"], fact_before["knowledge_fact_id"])
        self.assertEqual(fact_after["fact_text"], fact_before["fact_text"])
        self.assertEqual(fact_after["confidence"], fact_before["confidence"])
        self.assertEqual(fact_after["contradiction_count"], fact_before["contradiction_count"])
        self.assertIsNone(fact_after["source_message_id"])
        _items, context = await self._lookup(pool)
        self.assertIn("Project X uses Turso", context)
        self.assertNotIn("Project X uses PostgreSQL", context)

    async def test_persona_database_isolation_keeps_other_knowledge_unchanged(self) -> None:
        pool_a, knowledge_a, _message_a = await self._create_pool()
        pool_b, _knowledge_b, _message_b = await self._create_pool()
        await observation_corrections.update_knowledge(pool_a, knowledge_a, "Project X uses Turso")

        _items_a, context_a = await self._lookup(pool_a)
        _items_b, context_b = await self._lookup(pool_b)
        self.assertIn("Project X uses Turso", context_a)
        self.assertNotIn("Project X uses PostgreSQL", context_a)
        self.assertIn("Project X uses PostgreSQL", context_b)
        self.assertNotIn("Project X uses Turso", context_b)

    async def test_uncorrected_story_facts_retain_existing_active_semantics(self) -> None:
        pool, _knowledge_id, _message_id = await self._create_pool()
        items, context = await self._lookup(pool)

        self.assertFalse(items[0]["correction_active"])
        self.assertTrue(items[0]["facts"][0]["active"])
        self.assertIn("Project X uses PostgreSQL", context)
