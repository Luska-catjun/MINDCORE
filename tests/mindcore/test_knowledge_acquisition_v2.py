from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import uuid4
import unittest

import libsql

from app.database.turso import TursoConnection
from app.services.mindcore.knowledge import acquire_user_knowledge, check_epistemic_state, detect_subjects


class LocalPool:
    def __init__(self) -> None:
        self.connection = TursoConnection(libsql.connect(":memory:"))

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


class KnowledgeAcquisitionV2Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.pool = LocalPool()
        async with self.pool.acquire() as connection:
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

    async def _acquire(self, text: str):
        return await acquire_user_knowledge(
            self.pool, user_text=text, user_message_id=uuid4(), source_episode_id=None,
            episode_is_grounded=True, conversation_id=uuid4(),
        )

    async def test_calibration_corpus_captures_explicit_facts_and_rejects_nonknowledge(self) -> None:
        must_capture = [
            "스파게티는 파스타의 한 종류야.",
            "흑요석은 화산 활동으로 만들어진 유리질 암석이야.",
            "물은 수소와 산소로 이루어져 있어.",
            "지구는 태양 주위를 돈다.",
            "베트르랑의 역설은 확률을 정의하는 방식에 따라 답이 달라지는 문제야.",
            "업다운 게임에서 업은 정답이 더 크다는 뜻이야.",
            "고래는 포유류야.",
            "소금은 염화나트륨으로 이루어져 있어.",
        ]
        ambiguous = [
            "아마 고래는 포유류일 거야.", "그 책은 재미있을 수도 있어.", "스파게티가 맛있어.",
            "고래에 대해 들어봤어?", "그건 아마 어려운 문제야.",
        ]
        nonknowledge = [
            "오늘 친구들이랑 놀았는데 재밌었어.", "나는 민트초코 좋아해.", "다음에 게임 하고 싶어.",
            "오늘 피곤해.", "초콜릿 먹고 싶다.", "내일 학교 가야 해.", "그 게임 재밌더라.",
        ]
        captured = [await self._acquire(text) for text in must_capture]
        ambiguous_results = [await self._acquire(text) for text in ambiguous]
        nonknowledge_results = [await self._acquire(text) for text in nonknowledge]
        self.assertEqual(sum(bool(result) for result in captured), len(must_capture))
        self.assertEqual(sum(bool(result) for result in ambiguous_results), 0)
        self.assertEqual(sum(bool(result) for result in nonknowledge_results), 0)
        async with self.pool.acquire() as connection:
            self.assertEqual(await connection.fetchval("select count(*) from diana_knowledge"), len(must_capture))

    async def test_explicit_teaching_is_durable_without_episode_and_visible_to_epistemic_context(self) -> None:
        stored = await self._acquire("참고로 스파게티는 파스타 종류 중 하나야.")
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["source_type"], "user_message")
        self.assertIsNone(stored[0]["source_episode_id"])
        items, context = await check_epistemic_state(self.pool, "스파게티는 뭐야?")
        self.assertEqual(items[0]["status"], "introduced")
        self.assertIn("파스타 종류 중 하나", context or "")

    async def test_same_explicit_fact_reinforces_one_subject_row(self) -> None:
        await self._acquire("고양이는 포유류야.")
        await self._acquire("고양이는 포유류야.")
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow("select reinforcement_count,confidence from diana_knowledge where subject_key='고양이'")
        self.assertEqual(row["reinforcement_count"], 2)
        self.assertGreaterEqual(float(row["confidence"]), .95)

    async def test_hedged_claim_and_user_display_name_do_not_become_knowledge(self) -> None:
        self.assertEqual(await self._acquire("고래는 포유류일 거야."), [])
        self.assertEqual(await self._acquire("내 이름은 User야."), [])
        self.assertEqual(await self._acquire("내 생일은 5월 3일이야."), [])
        async with self.pool.acquire() as connection:
            self.assertEqual(await connection.fetchval("select count(*) from diana_knowledge"), 0)

    async def test_story_content_stays_story_knowledge_not_general_concept(self) -> None:
        stored = await self._acquire("빨간 모자라는 아이가 숲에 갔어.")
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["knowledge_type"], "story")
        async with self.pool.acquire() as connection:
            self.assertEqual(await connection.fetchval("select count(*) from diana_knowledge_facts"), 1)
            self.assertEqual(await connection.fetchval("select count(*) from diana_knowledge where knowledge_type='concept'"), 0)

    async def test_obvious_story_contradiction_retains_existing_fact_and_marks_it(self) -> None:
        conversation_id = uuid4()
        await acquire_user_knowledge(
            self.pool, user_text="빨간 모자라는 아이가 숲에 갔어.", user_message_id=uuid4(),
            source_episode_id=None, episode_is_grounded=True, conversation_id=conversation_id,
        )
        await acquire_user_knowledge(
            self.pool, user_text="빨간 모자는 숲에 안 갔어.", user_message_id=uuid4(),
            source_episode_id=None, episode_is_grounded=True, conversation_id=conversation_id,
        )
        async with self.pool.acquire() as connection:
            facts = await connection.fetch("select fact_text, contradiction_count from diana_knowledge_facts")
        self.assertEqual(len(facts), 1)
        self.assertIn("숲에 갔어", facts[0]["fact_text"])
        self.assertEqual(facts[0]["contradiction_count"], 1)

    def test_explicit_teaching_score_is_higher_than_nonknowledge_ownership_forms(self) -> None:
        teaching = detect_subjects("물은 수소와 산소로 이루어져 있어.")
        self.assertEqual(teaching[0].kind, "teaching")
        self.assertGreaterEqual(teaching[0].score, .70)
        self.assertFalse(any(item.kind == "teaching" for item in detect_subjects("나는 민트초코 좋아해.")))
