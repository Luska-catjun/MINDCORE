"""Memory Ownership v1: deterministic routing before long-term extraction."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.config import Settings
from app.services.memory_service import (
    MEMORY_MIN_IMPORTANCE,
    MemoryCandidate,
    _memory_semantic_similarity,
    classify_memory_ownership,
    extract_and_store_memory,
    is_retrievable_memory,
    retrieve_relevant_memories,
    should_extract_memory,
    upsert_memory,
)
from app.database.turso import TursoConnection
import libsql


class MemoryOwnershipClassificationTests(TestCase):
    # This corpus is intentionally local and deterministic: no LLM call is
    # needed to decide who owns a claim before Memory extraction.
    CALIBRATION = (
        # Preference-only (6)
        ("나는 초콜릿 좋아해.", False, "preference"),
        ("공포게임 별로야.", False, "preference"),
        ("나는 민트초코를 싫어해.", False, "preference"),
        ("커피보다 차를 더 좋아해.", False, "preference"),
        ("나는 매운 음식은 안 좋아해.", False, "preference"),
        ("요즘은 재즈가 좋아.", False, "preference"),
        # Episode-only, below Memory promotion threshold (6)
        ("오늘 점심 먹었어.", False, "episode"),
        ("오늘 학교 갔다 왔어.", False, "episode"),
        ("어제 커피 마셨어.", False, "episode"),
        ("오늘 책 조금 읽었어.", False, "episode"),
        ("방금 산책 갔다 왔어.", False, "episode"),
        ("오늘 친구를 만났어.", False, "episode"),
        # Event + Memory-worthy (6)
        ("오늘 학교 축제에서 친구들이랑 처음으로 게임 부스를 운영했어.", True, "memory"),
        ("어제 가족이랑 여행 가서 오랜만에 바다를 봤어.", True, "memory"),
        ("오늘 친구랑 병원에 갔다가 많이 힘들었어.", True, "memory"),
        ("처음으로 친구들이랑 같이 공연 무대에 올랐어.", True, "memory"),
        ("오늘 생일이라 가족이랑 특별한 저녁을 먹었어.", True, "memory"),
        ("친구랑 중요한 게임 대회를 준비해서 같이 운영했어.", True, "memory"),
        # Knowledge (4)
        ("흑요석은 화산 활동으로 만들어져.", False, "knowledge"),
        ("물은 섭씨 100도에서 끓어.", False, "knowledge"),
        ("스파게티는 파스타 종류야.", False, "knowledge"),
        ("북풍과 태양은 바람과 태양이 나오는 이야기야.", False, "knowledge"),
        # Goal / Decision / Relationship (6)
        ("다음에 스무고개 하고 싶어.", False, "goal"),
        ("나중에 그 책을 읽어보고 싶어.", False, "goal"),
        ("이번엔 업다운 하자.", False, "decision"),
        ("그럼 오늘은 스무고개로 하자.", False, "decision"),
        ("믿고 맡길게.", False, "relationship"),
        ("전에 말한 걸 아직 기억하고 있었네.", False, "relationship"),
        # Mixed (5): the event remains independently eligible.
        ("오늘 친구랑 공포게임 했는데 역시 난 공포게임은 별로야.", True, "memory"),
        ("오늘 민트초코 먹었는데 역시 맛있더라.", False, "episode"),
        ("오늘 친구랑 여행 계획을 세우고 다음에 같이 가고 싶어졌어.", False, "goal"),
        ("오늘 같이 게임했는데 믿고 맡길 수 있겠다고 느꼈어.", True, "memory"),
        ("오늘 학교 축제에서 친구들과 게임 부스를 운영하고 나중에 또 하고 싶어졌어.", True, "memory"),
    )

    def test_calibration_corpus_routes_33_inputs_without_memory_false_positives(self) -> None:
        self.assertGreaterEqual(len(self.CALIBRATION), 30)
        for text, eligible, owner in self.CALIBRATION:
            with self.subTest(text=text):
                decision = classify_memory_ownership(text)
                self.assertEqual(decision.memory_eligible, eligible)
                self.assertIn(owner, decision.owners)

    def test_personal_fact_remains_a_memory_owner(self) -> None:
        decision = classify_memory_ownership("내 생일은 5월 3일이야.")
        self.assertTrue(decision.memory_eligible)
        self.assertEqual(decision.memory_type, "user_fact")
        self.assertIn("personal_fact", decision.owners)

    def test_uncertain_personal_fact_and_transient_turns_do_not_open_memory_extraction(self) -> None:
        for text in ("내 생일은 5월 3일인 것 같아.", "ㅋㅋㅋ", "지금 게임 켰어."):
            with self.subTest(text=text):
                self.assertFalse(should_extract_memory(text))

    def test_user_display_name_is_not_a_memory_owner(self) -> None:
        self.assertFalse(should_extract_memory("내 이름은 User야."))

    def test_mixed_event_keeps_preference_and_event_as_distinct_owners(self) -> None:
        decision = classify_memory_ownership("오늘 친구랑 공포게임 했는데 역시 난 공포게임은 별로야.")
        self.assertTrue(decision.memory_eligible)
        self.assertIn("preference", decision.owners)
        self.assertIn("episode", decision.owners)
        self.assertIn("memory", decision.owners)
        self.assertNotIn("knowledge", decision.owners)

    def test_legacy_preference_and_relationship_rows_are_not_retrievable(self) -> None:
        self.assertFalse(is_retrievable_memory({"memory_type": "preference", "content": "사용자는 초콜릿을 좋아한다."}))
        self.assertFalse(is_retrievable_memory({"memory_type": "relationship", "content": "사용자는 Diana를 신뢰한다."}))
        self.assertFalse(is_retrievable_memory({"memory_type": "shared_event", "content": "사용자는 초콜릿을 좋아한다."}))
        self.assertTrue(is_retrievable_memory({"memory_type": "shared_event", "content": "오늘 친구와 축제에서 게임 부스를 운영했다."}))


class MemoryOwnershipExtractionTests(IsolatedAsyncioTestCase):
    async def test_preference_turn_skips_existing_extraction_llm_call(self) -> None:
        with patch("app.services.memory_service.generate_memory_candidate", new=AsyncMock()) as extract:
            result = await extract_and_store_memory(
                None, Settings(), user_content="나는 초콜릿 좋아해.", diana_content="초콜릿 좋지.",
                conversation_id=uuid4(), source_message_id=uuid4(),
            )
        self.assertIsNone(result)
        extract.assert_not_awaited()

    async def test_mixed_event_rejects_preference_shaped_candidate(self) -> None:
        payload = '{"should_store":true,"memory":"사용자는 공포게임을 싫어한다.","memory_type":"preference","importance":0.8}'
        with patch("app.services.memory_service.generate_memory_candidate", new=AsyncMock(return_value=payload)), patch(
            "app.services.memory_service.upsert_memory", new=AsyncMock()
        ) as upsert:
            result = await extract_and_store_memory(
                None, Settings(), user_content="오늘 친구랑 공포게임 했는데 역시 난 공포게임은 별로야.",
                diana_content="그랬구나.", conversation_id=uuid4(), source_message_id=uuid4(),
            )
        self.assertIsNone(result)
        upsert.assert_not_awaited()

    async def test_meaningful_event_stores_only_shared_event_representation(self) -> None:
        payload = '{"should_store":true,"memory":"오늘 친구들과 학교 축제에서 게임 부스를 운영했다.","memory_type":"shared_event","importance":0.8}'
        stored = {"memory_id": uuid4(), "memory_type": "shared_event"}
        with patch("app.services.memory_service.generate_memory_candidate", new=AsyncMock(return_value=payload)), patch(
            "app.services.memory_service.upsert_memory", new=AsyncMock(return_value=stored)
        ) as upsert:
            result = await extract_and_store_memory(
                None, Settings(), user_content="오늘 학교 축제에서 친구들이랑 처음으로 게임 부스를 운영했어.",
                diana_content="정말 특별한 경험이었겠다.", conversation_id=uuid4(), source_message_id=uuid4(),
            )
        self.assertEqual(result, stored)
        self.assertEqual(upsert.await_args.args[1].memory_type, "shared_event")

    async def test_stable_personal_fact_can_store_but_low_importance_candidate_cannot(self) -> None:
        stable_payload = '{"should_store":true,"memory":"사용자의 생일은 5월 3일이다.","memory_type":"user_fact","importance":0.35}'
        stored = {"memory_id": uuid4(), "memory_type": "user_fact"}
        with patch("app.services.memory_service.generate_memory_candidate", new=AsyncMock(return_value=stable_payload)), patch(
            "app.services.memory_service.upsert_memory", new=AsyncMock(return_value=stored)
        ) as upsert:
            result = await extract_and_store_memory(
                None, Settings(), user_content="내 생일은 5월 3일이야.", diana_content="알겠어.",
                conversation_id=uuid4(), source_message_id=uuid4(),
            )
        self.assertEqual(result, stored)
        self.assertEqual(upsert.await_args.args[1].importance, MEMORY_MIN_IMPORTANCE)

        low_importance = '{"should_store":true,"memory":"사용자의 생일은 5월 3일이다.","memory_type":"user_fact","importance":0.34}'
        with patch("app.services.memory_service.generate_memory_candidate", new=AsyncMock(return_value=low_importance)), patch(
            "app.services.memory_service.upsert_memory", new=AsyncMock()
        ) as rejected:
            result = await extract_and_store_memory(
                None, Settings(), user_content="내 생일은 5월 3일이야.", diana_content="알겠어.",
                conversation_id=uuid4(), source_message_id=uuid4(),
            )
        self.assertIsNone(result)
        rejected.assert_not_awaited()


class _ReadOnlyConnection:
    def __init__(self, rows): self.rows = rows
    async def fetch(self, *_args): return self.rows


class _ReadOnlyPool:
    def __init__(self, rows): self.connection = _ReadOnlyConnection(rows)
    @asynccontextmanager
    async def acquire(self): yield self.connection


class _MemoryWritePool:
    def __init__(self) -> None:
        self.connection = TursoConnection(libsql.connect(":memory:"))

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


class MemoryOwnershipRetrievalTests(IsolatedAsyncioTestCase):
    async def test_retrieval_filters_legacy_wrong_owner_before_context_builder(self) -> None:
        now = datetime.now(timezone.utc)
        rows = [
            {"memory_id": uuid4(), "content": "사용자는 초콜릿을 좋아한다.", "memory_type": "preference", "importance": .9, "recall_frequency": 0, "memory_strength": .9, "last_recalled_at": None, "created_at": now, "updated_at": now},
            {"memory_id": uuid4(), "content": "오늘 친구와 축제에서 게임 부스를 운영했다.", "memory_type": "shared_event", "importance": .8, "recall_frequency": 0, "memory_strength": .8, "last_recalled_at": None, "created_at": now, "updated_at": now},
        ]
        memories = await retrieve_relevant_memories(_ReadOnlyPool(rows), "축제에서 뭐 했지?")
        self.assertEqual([memory["memory_type"] for memory in memories], ["shared_event"])


class MemorySemanticDeduplicationTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.pool = _MemoryWritePool()
        async with self.pool.acquire() as connection:
            await connection.execute(
                """create table memories (
                    memory_id text primary key, content text not null, normalized_content text unique not null,
                    memory_type text not null, importance real not null, recall_frequency integer not null,
                    memory_strength real not null, last_recalled_at text, source_conversation_id text,
                    source_message_id text, created_at text not null, updated_at text not null
                )"""
            )

    async def test_semantic_event_rephrase_updates_one_durable_memory(self) -> None:
        first = MemoryCandidate("오늘 학교 축제에서 친구들과 게임 부스를 운영했다.", "shared_event", .7)
        repeat = MemoryCandidate("오늘 친구들과 학교 축제 게임 부스를 운영했어.", "shared_event", .8)
        self.assertGreaterEqual(_memory_semantic_similarity(first.content, repeat.content), .75)
        first_source = uuid4()
        row = await upsert_memory(self.pool, first, source_conversation_id=uuid4(), source_message_id=first_source)
        repeated = await upsert_memory(self.pool, repeat, source_conversation_id=uuid4(), source_message_id=uuid4())
        self.assertEqual(repeated["memory_id"], row["memory_id"])
        async with self.pool.acquire() as connection:
            self.assertEqual(await connection.fetchval("select count(*) from memories"), 1)
            self.assertEqual(await connection.fetchval("select source_message_id from memories where memory_id=$1", row["memory_id"]), first_source)

    async def test_distinct_event_details_do_not_false_merge(self) -> None:
        sea = MemoryCandidate("어제 가족이랑 여행 가서 바다를 봤어.", "shared_event", .7)
        mountain = MemoryCandidate("어제 가족이랑 여행 가서 산을 봤어.", "shared_event", .7)
        self.assertLess(_memory_semantic_similarity(sea.content, mountain.content), .75)
        await upsert_memory(self.pool, sea, source_conversation_id=uuid4(), source_message_id=uuid4())
        await upsert_memory(self.pool, mountain, source_conversation_id=uuid4(), source_message_id=uuid4())
        async with self.pool.acquire() as connection:
            self.assertEqual(await connection.fetchval("select count(*) from memories"), 2)
