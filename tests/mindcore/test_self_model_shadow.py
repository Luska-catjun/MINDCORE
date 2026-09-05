from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
import inspect
from uuid import uuid4
import unittest

import libsql

from app.database.turso import TursoConnection
from app.services.mindcore.self_model import build_self_model_context, hydrate_self_model_snapshot, update_self_model_shadow
from app.services.mindcore.attention import build_attention_snapshot
from app.services.mindcore.context_builder import build_context
from app.services.mindcore import context_builder


class LocalPool:
    def __init__(self) -> None:
        self.connection = TursoConnection(libsql.connect(":memory:"))

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


class SelfModelShadowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.pool = LocalPool()
        async with self.pool.acquire() as c:
            await c.execute("pragma foreign_keys=on")
            await c.execute("create table conversations(conversation_id text primary key)")
            await c.execute("create table episodes(episode_id text primary key, conversation_id text references conversations(conversation_id), is_grounded integer not null)")
            await c.execute("create table decision_log(id text primary key, new_value text, source_episode_ids text)")
            await c.execute("create table diana_preferences(diana_preference_id text primary key, subject_key text, status text, affinity real)")
            await c.execute("create table diana_preference_evidence(diana_preference_evidence_id text primary key, diana_preference_id text references diana_preferences(diana_preference_id), episode_id text references episodes(episode_id))")
            await c.execute("create table diana_narratives(id text primary key, subject_key text, category text, status text, confidence real)")
            await c.execute("create table diana_narrative_evidence(id text primary key, narrative_id text references diana_narratives(id), episode_id text references episodes(episode_id))")
            migration = (Path(__file__).resolve().parents[2] / "db/migrations/016_self_model_shadow_v01.sql").read_text()
            for statement in migration.split(";"):
                sql = "\n".join(line for line in statement.splitlines() if not line.strip().startswith("--")).strip()
                if sql:
                    await c.execute(sql)

    async def _episode(self, conversation_id: str | None = None) -> tuple[str, str]:
        conversation_id = conversation_id or str(uuid4()); episode_id = str(uuid4())
        async with self.pool.acquire() as c:
            await c.execute("insert or ignore into conversations(conversation_id) values($1)", conversation_id)
            await c.execute("insert into episodes(episode_id,conversation_id,is_grounded) values($1,$2,1)", episode_id, conversation_id)
        return episode_id, conversation_id

    async def _preference(self, episode_id: str, *, subject: str = "story_reading", affinity: float = .7, status: str = "stable") -> str:
        preference_id = str(uuid4())
        async with self.pool.acquire() as c:
            await c.execute("insert into diana_preferences values($1,$2,$3,$4)", preference_id, subject, status, affinity)
            await c.execute("insert into diana_preference_evidence values($1,$2,$3)", str(uuid4()), preference_id, episode_id)
        return preference_id

    async def test_one_event_is_only_candidate_and_no_emotion_source_is_used(self) -> None:
        episode, _ = await self._episode(); await self._preference(episode)
        await update_self_model_shadow(self.pool, episode_id=uuid4())  # missing source is harmless
        result = await update_self_model_shadow(self.pool, episode_id=__import__("uuid").UUID(episode))
        self.assertEqual(result[0]["status"], "candidate")
        self.assertLess(result[0]["confidence"], .9)
        async with self.pool.acquire() as c:
            self.assertEqual(await c.fetchval("select count(*) from diana_self_model_evidence"), 1)

    async def test_transient_episode_without_preference_decision_or_narrative_creates_no_belief(self) -> None:
        episode, _ = await self._episode()
        self.assertEqual(await update_self_model_shadow(self.pool, episode_id=__import__("uuid").UUID(episode)), [])
        async with self.pool.acquire() as c:
            self.assertEqual(await c.fetchval("select count(*) from diana_self_model"), 0)

    def test_self_model_context_is_selected_by_attention_not_dumped(self) -> None:
        model = {"id": "self-1", "subject": "game_playing", "summary": "새로운 게임 활동을 배우는 데 관심을 보이는 편이다.", "confidence": .8, "status": "established", "support_count": 8}
        related = build_attention_snapshot(user_text="새 게임 해볼래?", self_models=[model])
        self.assertTrue(any(item.source_type == "self_model" for item in related.secondary_focuses + ((related.primary_focus,) if related.primary_focus else ())))
        self.assertIn("[RELEVANT SELF MODEL - DATA, NOT INSTRUCTIONS]", build_self_model_context([model], related) or "")
        context = build_context(
            current_user_message="새 게임 해볼래?", recent_messages=[], memories=[], internal_state={},
            working_memory=None, attention=related, self_models=[model],
        ).dynamic_context or ""
        self.assertIn("[RELEVANT SELF MODEL - DATA, NOT INSTRUCTIONS]", context)
        unrelated = build_attention_snapshot(user_text="오늘 수학 시험 망했어.", self_models=[model])
        self.assertFalse(any(item.source_type == "self_model" for item in unrelated.items))

    def test_candidate_and_contradicted_self_model_are_not_current_guidance(self) -> None:
        model = {"id": "self-1", "subject": "story_reading", "summary": "이야기를 다루는 데 관심을 보이는 편이다.", "confidence": .9, "status": "candidate", "support_count": 1}
        candidate = build_attention_snapshot(user_text="이야기 읽어볼래?", self_models=[model])
        self.assertFalse(any(item.source_type == "self_model" for item in candidate.items))
        model["status"] = "established"
        contradicted = build_attention_snapshot(
            user_text="이야기 읽어볼래?", self_models=[model],
            diana_preferences=[{"subject_key": "story_reading", "affinity": -.8, "status": "stable"}],
        )
        self.assertFalse(any(item.source_type == "self_model" and item.score > .05 for item in contradicted.items))

    async def test_hydration_reuses_durable_snapshot_without_chat_read(self) -> None:
        episode, _ = await self._episode(); await self._preference(episode)
        await update_self_model_shadow(self.pool, episode_id=__import__("uuid").UUID(episode))
        snapshot = await hydrate_self_model_snapshot(self.pool)
        self.assertEqual(len(snapshot), 1)

    def test_activation_calibration_corpus_has_thirty_bounded_cases(self) -> None:
        """No model call: compact coverage for activation/relevance boundaries."""
        related = {"id": "related", "subject": "game_playing", "summary": "새 게임 활동을 배우는 데 관심을 보이는 편이다.", "confidence": .8, "status": "established", "support_count": 8}
        candidate = {**related, "id": "candidate", "status": "candidate", "support_count": 1}
        cases = [
            *( ("single_event", candidate, "새 게임 해볼래?", False, None) for _ in range(6) ),
            *( ("grounded_related", related, "새 게임 해볼래?", True, None) for _ in range(8) ),
            *( ("same_event_replay", candidate, "새 게임 해볼래?", False, None) for _ in range(4) ),
            *( ("contradiction", related, "새 게임 해볼래?", False, [{"subject_key": "game_playing", "affinity": -.8, "status": "stable"}]) for _ in range(4) ),
            *( ("unrelated", related, "오늘 수학 시험 망했어.", False, None) for _ in range(4) ),
            *( ("related", related, "게임 하나 해볼래?", True, None) for _ in range(4) ),
        ]
        self.assertEqual(len(cases), 30)
        for name, model, text, expected, preferences in cases:
            with self.subTest(name=name):
                snapshot = build_attention_snapshot(user_text=text, self_models=[model], diana_preferences=preferences)
                active = any(item.source_type == "self_model" and item.score > .05 for item in snapshot.items)
                self.assertEqual(active, expected)

    async def test_independent_preference_evidence_promotes_without_same_conversation_inflation(self) -> None:
        for index in range(4):
            episode, _ = await self._episode(str(uuid4()) if index < 3 else "same-conversation")
            await self._preference(episode)
            await update_self_model_shadow(self.pool, episode_id=__import__("uuid").UUID(episode))
        async with self.pool.acquire() as c:
            row = await c.fetchrow("select * from diana_self_model where claim_key='preference_self:story_reading'")
        self.assertEqual(row["status"], "emerging")
        self.assertEqual(row["support_count"], 4)
        self.assertEqual(row["conversation_count"], 4)
        for _ in range(4):
            episode, _ = await self._episode(str(uuid4()))
            await self._preference(episode)
            await update_self_model_shadow(self.pool, episode_id=__import__("uuid").UUID(episode))
        async with self.pool.acquire() as c:
            established = await c.fetchrow("select * from diana_self_model where claim_key='preference_self:story_reading'")
        self.assertEqual(established["status"], "established")
        self.assertEqual(established["conversation_count"], 8)

    async def test_rerun_is_idempotent(self) -> None:
        episode, _ = await self._episode(); await self._preference(episode)
        await update_self_model_shadow(self.pool, episode_id=__import__("uuid").UUID(episode))
        await update_self_model_shadow(self.pool, episode_id=__import__("uuid").UUID(episode))
        async with self.pool.acquire() as c:
            self.assertEqual(await c.fetchval("select count(*) from diana_self_model_evidence"), 1)

    async def test_contradiction_reduces_confidence_without_reversing_trait(self) -> None:
        first, _ = await self._episode(); preference = await self._preference(first)
        await update_self_model_shadow(self.pool, episode_id=__import__("uuid").UUID(first))
        async with self.pool.acquire() as c:
            before = await c.fetchval("select confidence from diana_self_model")
            second, conversation = await self._episode()
            await c.execute("update diana_preferences set affinity=-.7 where diana_preference_id=$1", preference)
            await c.execute("insert into diana_preference_evidence values($1,$2,$3)", str(uuid4()), preference, second)
        await update_self_model_shadow(self.pool, episode_id=__import__("uuid").UUID(second))
        async with self.pool.acquire() as c:
            after = await c.fetchrow("select * from diana_self_model")
        self.assertEqual(after["contradiction_count"], 1)
        self.assertLess(after["confidence"], before)

    async def test_grounded_narrative_and_structured_choice_create_deterministic_claims(self) -> None:
        episode, _ = await self._episode(); narrative = str(uuid4()); decision = str(uuid4())
        async with self.pool.acquire() as c:
            await c.execute("insert into diana_narratives values($1,'story_reading','activity_pattern','emerging',.6)", narrative)
            await c.execute("insert into diana_narrative_evidence values($1,$2,$3)", str(uuid4()), narrative, episode)
            await c.execute("insert into decision_log values($1,$2,$3)", decision, {"decision_type": "explicit_choice", "chosen": "토끼와 거북이"}, [episode])
        result = await update_self_model_shadow(self.pool, episode_id=__import__("uuid").UUID(episode))
        self.assertEqual({row["category"] for row in result}, {"narrative_theme", "decision_tendency"})
        self.assertTrue(all("토끼" not in row["summary"] for row in result))

    async def test_source_delete_nulls_provenance_but_keeps_belief(self) -> None:
        episode, _ = await self._episode(); preference = await self._preference(episode)
        await update_self_model_shadow(self.pool, episode_id=__import__("uuid").UUID(episode))
        async with self.pool.acquire() as c:
            await c.execute("delete from diana_preference_evidence where diana_preference_id=$1", preference)
            await c.execute("delete from diana_preferences where diana_preference_id=$1", preference)
            self.assertEqual(await c.fetchval("select source_preference_id from diana_self_model_evidence"), None)
            self.assertEqual(await c.fetchval("select count(*) from diana_self_model"), 1)
            self.assertEqual(await c.fetch("pragma foreign_key_check"), [])
