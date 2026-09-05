"""Narrative Consolidation v1: bounded identity, not fuzzy semantic merging."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from time import perf_counter
from unittest import IsolatedAsyncioTestCase, TestCase
from uuid import uuid4

import libsql

from app.database.turso import TursoConnection
from app.services.mindcore.attention import build_attention_snapshot
from app.services.mindcore.narrative import (
    canonical_narrative_identity,
    build_narrative_context,
    select_canonical_narratives,
    update_narratives_for_episode,
)


def row(identifier: str, category: str, subject: str, *, key: str | None = None, status: str = "emerging", evidence: int = 4) -> dict[str, object]:
    now = "2026-01-01T00:00:00+00:00"
    return {"id": identifier, "narrative_key": key or f"{category}:{subject}", "category": category,
            "subject_key": subject, "summary": f"{subject} pattern", "status": status, "confidence": .7,
            "evidence_count": evidence, "created_at": now}


class NarrativeIdentityTests(TestCase):
    CALIBRATION = (
        # true duplicate / paraphrase (10)
        ("activity_pattern", "게임", "neutral", "activity_pattern", "game_playing", "neutral", True),
        ("activity_pattern", "게임하기", "neutral", "activity_pattern", "game_playing", "neutral", True),
        ("activity_pattern", "같이 게임", "neutral", "activity_pattern", "game_playing", "neutral", True),
        ("activity_pattern", "게임 활동", "neutral", "activity_pattern", "game_playing", "neutral", True),
        ("activity_pattern", "이야기", "neutral", "activity_pattern", "story_reading", "neutral", True),
        ("activity_pattern", "동화", "neutral", "activity_pattern", "story_reading", "neutral", True),
        ("activity_pattern", "story", "neutral", "activity_pattern", "story_reading", "neutral", True),
        ("emotional_pattern", "story:오늘도", "positive", "emotional_pattern", "story_reading", "positive", True),
        ("emotional_pattern", "story:오늘은", "positive", "emotional_pattern", "story_reading", "positive", True),
        ("emotional_pattern", "story:전에_내가_읽어줬던", "positive", "emotional_pattern", "story_reading", "positive", True),
        # related but distinct (8)
        ("activity_pattern", "game_playing", "neutral", "interest_pattern", "game_playing", "neutral", False),
        ("activity_pattern", "story_reading", "neutral", "choice_pattern", "story_reading", "neutral", False),
        ("learning_pattern", "game_playing", "neutral", "choice_pattern", "game_playing", "neutral", False),
        ("relationship_pattern", "relationship_primary", "neutral", "activity_pattern", "game_playing", "neutral", False),
        ("emotional_pattern", "game_playing", "positive", "activity_pattern", "game_playing", "neutral", False),
        ("choice_pattern", "choice_a", "neutral", "choice_pattern", "choice_b", "neutral", False),
        ("learning_pattern", "story_reading", "neutral", "activity_pattern", "story_reading", "neutral", False),
        ("social_pattern", "game_playing", "neutral", "activity_pattern", "game_playing", "neutral", False),
        # explicit cross-type separation (4)
        ("activity_pattern", "game_playing", "neutral", "interest_pattern", "game_playing", "positive", False),
        ("choice_pattern", "story_reading", "neutral", "learning_pattern", "story_reading", "neutral", False),
        ("relationship_pattern", "relationship_primary", "neutral", "social_pattern", "relationship_primary", "neutral", False),
        ("emotional_pattern", "game_playing", "positive", "choice_pattern", "game_playing", "neutral", False),
        # opposite direction (4)
        ("interest_pattern", "game_playing", "positive", "interest_pattern", "game_playing", "negative", False),
        ("activity_pattern", "game_playing", "positive", "activity_pattern", "game_playing", "negative", False),
        ("emotional_pattern", "story_reading", "positive", "emotional_pattern", "story_reading", "negative", False),
        ("interest_pattern", "horror_game", "positive", "interest_pattern", "horror_game", "negative", False),
        # historical/current direction stays separate (4)
        ("activity_pattern", "game_playing", "neutral", "activity_pattern", "game_playing", "negative", False),
        ("interest_pattern", "horror_game", "negative", "interest_pattern", "horror_game", "positive", False),
        ("activity_pattern", "story_reading", "neutral", "activity_pattern", "story_reading", "positive", False),
        ("emotional_pattern", "game_playing", "negative", "emotional_pattern", "game_playing", "positive", False),
    )

    def test_30_item_identity_calibration_has_no_false_merge(self) -> None:
        self.assertGreaterEqual(len(self.CALIBRATION), 30)
        for category, subject, direction, other_category, other_subject, other_direction, same in self.CALIBRATION:
            with self.subTest(category=category, subject=subject, other=other_subject):
                self.assertEqual(
                    canonical_narrative_identity(category, subject, direction)
                    == canonical_narrative_identity(other_category, other_subject, other_direction),
                    same,
                )

    def test_legacy_snapshot_attention_and_context_keep_one_canonical_theme(self) -> None:
        duplicate = row("duplicate", "activity_pattern", "게임 활동", status="candidate", evidence=1)
        canonical = row("canonical", "activity_pattern", "game_playing", status="emerging", evidence=4)
        chosen = select_canonical_narratives([duplicate, canonical])
        self.assertEqual([item["id"] for item in chosen], ["canonical"])
        attention = build_attention_snapshot(user_text="game playing을 오늘도 했어.", narratives=[duplicate, canonical])
        narrative_items = [item for item in attention.items if item.source_type == "narrative"]
        self.assertEqual([item.source_id for item in narrative_items], ["canonical"])
        context = build_narrative_context([duplicate, canonical], attention)
        self.assertEqual((context or "").count("game_playing pattern"), 1)

    def test_consolidation_lookup_is_local_and_bounded(self) -> None:
        started = perf_counter()
        for _ in range(10_000):
            canonical_narrative_identity("activity_pattern", "같이 게임", "neutral")
        self.assertLess((perf_counter() - started) * 1000 / 10_000, 1)


class _Pool:
    def __init__(self) -> None:
        self.connection = TursoConnection(libsql.connect(":memory:"))

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


class NarrativeConsolidationPersistenceTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.pool = _Pool()
        async with self.pool.acquire() as c:
            await c.execute("create table episodes(episode_id text primary key,conversation_id text,topic_key text,is_grounded integer)")
            await c.execute("create table decision_log(id text primary key,new_value text,source_episode_ids text)")
            await c.execute("create table diana_preference_evidence(diana_preference_evidence_id text primary key,episode_id text,subject_key text,signal_type text,signal_value real)")
            await c.execute("create table emotion_attributions(emotion_attribution_id text primary key,episode_id text,emotion text,delta real)")
            await c.execute("create table memories(memory_id text primary key,source_episode_id text)")
            await c.execute("create table diana_knowledge(knowledge_id text primary key,source_episode_id text,subject_key text)")
            await c.execute("create table relationship_log(relationship_log_id text primary key,episode_id text)")
            await c.execute("""create table diana_narratives(
                id text primary key,narrative_key text unique,subject_key text,category text,summary text,status text,
                confidence real,evidence_count integer,distinct_episode_count integer,distinct_conversation_count integer,
                first_observed_at text,last_observed_at text,created_at text,updated_at text)""")
            await c.execute("""create table diana_narrative_evidence(
                id text primary key,evidence_key text unique,narrative_id text,episode_id text,decision_id text,
                preference_evidence_id text,emotion_attribution_id text,memory_id text,relationship_log_id text,
                knowledge_id text,evidence_type text,signal_value real,created_at text)""")

    async def _episode_with_memory(self, topic: str, conversation: str) -> str:
        episode_id, memory_id = str(uuid4()), str(uuid4())
        async with self.pool.acquire() as c:
            await c.execute("insert into episodes values($1,$2,$3,1)", episode_id, conversation, topic)
            await c.execute("insert into memories values($1,$2)", memory_id, episode_id)
        return episode_id

    async def test_alias_evidence_reinforces_one_row_and_progresses_to_emerging(self) -> None:
        episodes = [
            await self._episode_with_memory("게임", "conversation-a"),
            await self._episode_with_memory("게임 활동", "conversation-b"),
            await self._episode_with_memory("같이 게임", "conversation-c"),
        ]
        for episode_id in episodes:
            await update_narratives_for_episode(self.pool, episode_id=episode_id)
        async with self.pool.acquire() as c:
            rows = await c.fetch("select * from diana_narratives")
            evidence = await c.fetch("select * from diana_narrative_evidence")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["subject_key"], "game_playing")
        self.assertEqual(rows[0]["evidence_count"], 3)
        self.assertEqual(rows[0]["distinct_episode_count"], 3)
        self.assertEqual(rows[0]["distinct_conversation_count"], 3)
        self.assertEqual(rows[0]["status"], "emerging")
        self.assertEqual(len(evidence), 3)

    async def test_replayed_evidence_is_noop_and_opposite_direction_is_separate(self) -> None:
        episode = await self._episode_with_memory("게임", "conversation-a")
        await update_narratives_for_episode(self.pool, episode_id=episode)
        await update_narratives_for_episode(self.pool, episode_id=episode)
        async with self.pool.acquire() as c:
            self.assertEqual(await c.fetchval("select count(*) from diana_narratives"), 1)
            self.assertEqual(await c.fetchval("select count(*) from diana_narrative_evidence"), 1)
            negative_episode = str(uuid4()); preference_id = str(uuid4())
            await c.execute("insert into episodes values($1,$2,$3,1)", negative_episode, "conversation-b", "게임")
            await c.execute("insert into diana_preference_evidence values($1,$2,$3,$4,$5)", preference_id, negative_episode, "game_playing", "negative", -.25)
        await update_narratives_for_episode(self.pool, episode_id=negative_episode)
        async with self.pool.acquire() as c:
            rows = await c.fetch("select narrative_key from diana_narratives order by narrative_key")
        self.assertEqual([item["narrative_key"] for item in rows], ["activity_pattern:game_playing", "activity_pattern:game_playing:negative"])
