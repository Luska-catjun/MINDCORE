from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4
import unittest

import libsql

from app.database.turso import TursoConnection
from app.services.mindcore.diana_preferences import _status_for, build_diana_preference_context
from app.services.mindcore.preferences import (
    build_preference_context,
    evaluate_preference_evidence,
    get_stable_preferences,
    update_preference_from_experience,
)
from scripts.apply_turso_decision_lifecycle_migration import apply_lifecycle_migration


class Pool:
    def __init__(self) -> None:
        self.connection = TursoConnection(libsql.connect(":memory:"))
        self.acquires = 0

    @asynccontextmanager
    async def acquire(self):
        self.acquires += 1
        yield self.connection


class PreferenceEvolutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.pool = Pool()
        async with self.pool.acquire() as c:
            await c.execute("""create table preferences(
                preference_id text primary key, owner_type text, subject text, value text,
                preference_type text, status text, confidence real, evidence_count integer,
                first_seen_at text, last_seen_at text, created_at text, updated_at text)""")
            await c.execute("""create table preference_evidence(
                evidence_id text primary key, preference_id text, experience_id text,
                message_id text, evidence_type text, direction integer, strength real, created_at text)""")

    async def _stable(self, value: str, preference_type: str) -> str:
        identifier = uuid4(); now = datetime.now(timezone.utc)
        async with self.pool.acquire() as c:
            await c.execute(
                """insert into preferences values($1,'user','general',$2,$3,'stable',.9,3,$4,$4,$4,$4)""",
                identifier, value, preference_type, now,
            )
        return str(identifier)

    async def test_explicit_reversal_preserves_history_and_excludes_old_context(self) -> None:
        old_id = await self._stable("초콜릿", "like")
        result = await update_preference_from_experience(
            self.pool, uuid4(), uuid4(), "예전에는 초콜릿이 좋았는데 이제는 싫어.",
        )
        self.assertEqual(result["preference_type"], "dislike")
        async with self.pool.acquire() as c:
            old_status = await c.fetchval("select status from preferences where preference_id=$1", old_id)
            new_status = await c.fetchval("select status from preferences where preference_id=$1", result["preference_id"])
            rows = await c.fetch("select * from preferences order by created_at")
        self.assertEqual(old_status, "superseded")
        self.assertEqual(new_status, "candidate")
        self.assertEqual(len(rows), 2)
        self.assertEqual(await get_stable_preferences(self.pool), [])
        self.assertIsNone(build_preference_context(await get_stable_preferences(self.pool)))
        self.assertIsNone(build_preference_context([{
            "status": "superseded", "preference_type": "like", "value": "초콜릿",
        }]))

    async def test_reinforcement_updates_one_current_claim(self) -> None:
        first = await update_preference_from_experience(self.pool, uuid4(), uuid4(), "초콜릿 좋아해.")
        second = await update_preference_from_experience(self.pool, uuid4(), uuid4(), "초콜릿 역시 좋아해.")
        self.assertEqual(first["preference_id"], second["preference_id"])
        self.assertEqual(second["evidence_count"], 2)
        async with self.pool.acquire() as c:
            self.assertEqual(await c.fetchval("select count(*) from preferences"), 1)

    async def test_temporary_state_does_not_mutate_stable_preference(self) -> None:
        identifier = await self._stable("초콜릿", "like")
        before = self.pool.acquires
        self.assertIsNone(await update_preference_from_experience(self.pool, uuid4(), uuid4(), "오늘은 초콜릿이 안 땡겨."))
        self.assertEqual(self.pool.acquires, before)
        async with self.pool.acquire() as c:
            self.assertEqual(await c.fetchval("select status from preferences where preference_id=$1", identifier), "stable")

    async def test_explicit_negative_to_positive_evolution(self) -> None:
        old_id = await self._stable("초콜릿", "dislike")
        result = await update_preference_from_experience(
            self.pool, uuid4(), uuid4(), "예전에는 초콜릿이 별로였는데 요즘은 좋아.",
        )
        async with self.pool.acquire() as c:
            old_status = await c.fetchval("select status from preferences where preference_id=$1", old_id)
        self.assertEqual(old_status, "superseded")
        self.assertEqual(result["preference_type"], "like")

    async def test_new_explicit_opposite_claim_supersedes_even_without_temporal_adverb(self) -> None:
        old_id = await self._stable("초콜릿", "like")
        await update_preference_from_experience(self.pool, uuid4(), uuid4(), "초콜릿 싫어.")
        async with self.pool.acquire() as c:
            self.assertEqual(await c.fetchval("select status from preferences where preference_id=$1", old_id), "superseded")

    async def test_diana_negative_affinity_can_be_current_preference(self) -> None:
        status = _status_for(evidence_count=6, conversation_count=4, affinity=-.60, confidence=.80)
        self.assertEqual(status, "stable")
        context = build_diana_preference_context([{
            "display_name": "horror games", "status": status, "confidence": .80, "affinity": -.60,
        }])
        self.assertIn("stable negative preference", context or "")

    async def test_legacy_decision_migration_keeps_active_but_recovers_conversation(self) -> None:
        conversation_id = uuid4(); decision_id = uuid4(); now = datetime.now(timezone.utc)
        async with self.pool.acquire() as c:
            await c.execute("""create table decision_log(
                id text primary key, target text not null, old_value text, new_value text,
                reason text, source_episode_ids text not null, created_at text not null)""")
            await c.execute(
                "insert into decision_log values($1,'diana_decision',null,$2,null,$3,$4)",
                decision_id, {"conversation_id": str(conversation_id)}, [], now,
            )
            await apply_lifecycle_migration(c)
            await apply_lifecycle_migration(c)
            row = await c.fetchrow("select conversation_id,status,updated_at,resolved_at from decision_log where id=$1", decision_id)
        self.assertEqual(str(row["conversation_id"]), str(conversation_id))
        self.assertEqual(row["status"], "active")
        self.assertEqual(row["updated_at"], now)
        self.assertIsNone(row["resolved_at"])

    def test_parser_distinguishes_temporary_and_explicit_evolution(self) -> None:
        self.assertIsNone(evaluate_preference_evidence("지금은 초콜릿이 안 땡겨."))
        item = evaluate_preference_evidence("이제 초콜릿은 별로야.")
        self.assertEqual(item and item["preference_type"], "dislike")
        self.assertTrue(item and item["explicit_evolution"])
