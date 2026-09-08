from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import unittest
from uuid import UUID, uuid4

import libsql

from app.database.turso import TursoConnection
from app.services.memory_service import should_extract_memory
from app.services.mindcore.knowledge import detect_subjects
from app.services.mindcore.preferences import (
    CONTEXTUAL_STRENGTH,
    build_preference_context,
    evaluate_preference_evidence,
    get_stable_preferences,
    update_preference_from_experience,
)


class Pool:
    def __init__(self) -> None:
        self.connection = TursoConnection(libsql.connect(":memory:"))

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


class ContextualPreferenceInferenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.pool = Pool()
        async with self.pool.acquire() as c:
            await c.execute("create table conversations(conversation_id text primary key)")
            await c.execute(
                """create table messages(
                    id text primary key, conversation_id text not null references conversations(conversation_id) on delete cascade
                )"""
            )
            await c.execute(
                """create table experiences(
                    experience_id text primary key, conversation_id text not null references conversations(conversation_id),
                    user_message_id text not null references messages(id) on delete cascade,
                    assistant_message_id text not null references messages(id) on delete cascade
                )"""
            )
            await c.execute(
                """create table preferences(
                    preference_id text primary key, owner_type text, subject text, value text,
                    preference_type text, status text, confidence real, evidence_count integer,
                    first_seen_at text, last_seen_at text, created_at text, updated_at text,
                    unique(owner_type,subject,value,preference_type))"""
            )
            await c.execute(
                """create table preference_evidence(
                    evidence_id text primary key, preference_id text references preferences(preference_id),
                    experience_id text references experiences(experience_id) on delete cascade,
                    message_id text references messages(id) on delete cascade,
                    evidence_type text, direction integer, strength real, created_at text,
                    unique(experience_id,preference_id))"""
            )
            await c.execute(
                """create table diana_preferences(
                    diana_preference_id text primary key, display_name text not null)"""
            )

    async def _experience(self, conversation_id: UUID | None = None) -> tuple[UUID, UUID, UUID]:
        conversation_id = conversation_id or uuid4()
        user_message_id, assistant_message_id, experience_id = uuid4(), uuid4(), uuid4()
        async with self.pool.acquire() as c:
            await c.execute("insert into conversations values($1) on conflict do nothing", conversation_id)
            await c.execute("insert into messages values($1,$2)", user_message_id, conversation_id)
            await c.execute("insert into messages values($1,$2)", assistant_message_id, conversation_id)
            await c.execute(
                "insert into experiences values($1,$2,$3,$4)",
                experience_id, conversation_id, user_message_id, assistant_message_id,
            )
        return experience_id, user_message_id, conversation_id

    async def _apply(self, text: str, conversation_id: UUID | None = None):
        experience_id, message_id, conversation_id = await self._experience(conversation_id)
        result = await update_preference_from_experience(self.pool, experience_id, message_id, text)
        return result, experience_id, message_id, conversation_id

    def test_parser_preserves_explicit_and_rejects_non_evidence(self) -> None:
        positive = evaluate_preference_evidence("나는 홍차를 좋아해")
        negative = evaluate_preference_evidence("홍차 싫어")
        self.assertEqual((positive or {}).get("evidence_type"), "explicit_positive")
        self.assertEqual((negative or {}).get("evidence_type"), "explicit_negative")
        for text in (
            "오늘 홍차 마셨어", "홍차 샀어", "라면밖에 없어서 먹었어", "친구는 홍차 좋아해",
            "홍차 좋아해?", "친구가 '난 홍차 좋아해'라고 했어", "내가 홍차를 좋아하나?",
            "홍차 좋아하는 것 같기도 한데 모르겠어", "숙제라 공포게임 분석하고 있음",
            "내 이름은 Luska야",
        ):
            with self.subTest(text=text):
                self.assertIsNone(evaluate_preference_evidence(text))

    def test_contextual_signal_types_are_weak_and_normalized(self) -> None:
        cases = (
            ("요즘 카페 가면 홍차 계속 고르게 되네", "like", "홍차"),
            ("카페에서는 거의 홍차 시켜", "like", "홍차"),
            ("카페 가면 거의 홍차 고르는 편이야", "like", "홍차"),
            ("요즘 계속 홍차 마시게 되네", "like", "홍차"),
            ("요즘 이 게임 계속 재밌게 하고 있음", "like", "이 게임"),
            ("홍차는 마실 때마다 괜찮네", "like", "홍차"),
            ("공포게임을 계속 피하게 되네", "dislike", "공포게임"),
            ("해산물 메뉴는 잘 안 고르게 됨", "dislike", "해산물 메뉴"),
            ("보통 RPG부터 찾게 됨", "like", "rpg"),
            ("둘 있으면 항상 홍차 쪽 고름", "like", "홍차"),
            ("요즘은 FPS보다 RPG를 더 자주 함", "like", "rpg"),
            ("커피보다 자꾸 홍차에 손이 감", "like", "홍차"),
        )
        for text, preference_type, value in cases:
            with self.subTest(text=text):
                item = evaluate_preference_evidence(text)
                self.assertIsNotNone(item)
                self.assertEqual(item["preference_type"], preference_type)
                self.assertEqual(item["value"], value)
                self.assertEqual(item["strength"], CONTEXTUAL_STRENGTH)
                self.assertTrue(item["contextual"])

    async def test_independent_conversations_promote_but_same_conversation_does_not(self) -> None:
        same_conversation = uuid4()
        for _ in range(6):
            result, *_ = await self._apply("카페에서는 거의 홍차 시켜", same_conversation)
        self.assertEqual(result["status"], "candidate")
        self.assertEqual(result["evidence_count"], 1)

        for text in (
            "요즘 카페 가면 홍차 계속 고르게 되네",
            "홍차는 마실 때마다 괜찮네",
            "또 홍차 골랐어",
        ):
            result, *_ = await self._apply(text)
        self.assertEqual(result["status"], "stable")
        self.assertEqual(result["evidence_count"], 4)
        self.assertAlmostEqual(float(result["confidence"]), .8)
        async with self.pool.acquire() as c:
            kinds = await c.fetch("select evidence_type,strength from preference_evidence")
        self.assertTrue(all(row["evidence_type"] == "contextual_positive" for row in kinds))
        self.assertTrue(all(float(row["strength"]) == CONTEXTUAL_STRENGTH for row in kinds))

    async def test_one_off_does_not_supersede_but_explicit_evolution_does(self) -> None:
        now = datetime.now(timezone.utc)
        preference_id = uuid4()
        async with self.pool.acquire() as c:
            await c.execute(
                "insert into preferences values($1,'user','general','홍차','like','stable',.9,3,$2,$2,$2,$2)",
                preference_id, now,
            )
        result, *_ = await self._apply("오늘은 커피 마셨어")
        self.assertIsNone(result)
        async with self.pool.acquire() as c:
            self.assertEqual(await c.fetchval("select status from preferences where preference_id=$1", preference_id), "stable")
        changed, *_ = await self._apply("예전엔 홍차 좋아했는데 요즘은 커피가 더 좋아")
        self.assertEqual(changed["value"], "커피")

    async def test_contextual_evidence_neither_supersedes_nor_demotes_explicit_stable(self) -> None:
        now = datetime.now(timezone.utc)
        preference_id = uuid4()
        async with self.pool.acquire() as c:
            await c.execute(
                "insert into preferences values($1,'user','general','홍차','like','stable',.9,3,$2,$2,$2,$2)",
                preference_id, now,
            )

        reinforced, *_ = await self._apply("요즘 카페 가면 홍차 계속 고르게 되네")
        self.assertEqual(reinforced["status"], "stable")
        weak_opposite, *_ = await self._apply("카페에서는 거의 커피 시켜")
        self.assertEqual(weak_opposite["status"], "candidate")
        async with self.pool.acquire() as c:
            self.assertEqual(
                await c.fetchval("select status from preferences where preference_id=$1", preference_id),
                "stable",
            )

    async def test_candidate_is_not_context_and_ownership_layers_do_not_duplicate(self) -> None:
        result, *_ = await self._apply("카페에서는 거의 홍차 시켜")
        self.assertEqual(result["status"], "candidate")
        self.assertEqual(await get_stable_preferences(self.pool), [])
        self.assertIsNone(build_preference_context([result]))
        self.assertFalse(should_extract_memory("카페에서는 거의 홍차 시켜"))
        self.assertFalse(any(item.kind == "teaching" for item in detect_subjects("카페에서는 거의 홍차 시켜")))
        async with self.pool.acquire() as c:
            self.assertEqual(await c.fetchval("select count(*) from diana_preferences"), 0)

    async def test_sensitive_context_is_rejected_but_direct_general_taste_remains_explicit(self) -> None:
        self.assertIsNone(evaluate_preference_evidence("요즘 특정 정당만 계속 고르게 되네"))
        explicit = evaluate_preference_evidence("나는 공포게임을 좋아해")
        self.assertEqual((explicit or {}).get("evidence_type"), "explicit_positive")

    async def test_message_delete_detaches_evidence_without_deleting_durable_preference(self) -> None:
        result, _experience_id, message_id, _conversation_id = await self._apply("나는 홍차를 좋아해")
        async with self.pool.acquire() as c:
            await c.execute("delete from messages where id=$1", message_id)
            self.assertEqual(await c.fetchval("select count(*) from preference_evidence"), 0)
            self.assertIsNotNone(await c.fetchrow("select * from preferences where preference_id=$1", result["preference_id"]))
            self.assertEqual(await c.fetch("pragma foreign_key_check"), [])

    async def test_persona_database_isolation(self) -> None:
        other = ContextualPreferenceInferenceTests(methodName="runTest")
        await other.asyncSetUp()
        try:
            for text in (
                "카페에서는 거의 홍차 시켜", "요즘 카페 가면 홍차 계속 고르게 되네",
                "홍차는 마실 때마다 괜찮네", "또 홍차 골랐어",
            ):
                await self._apply(text)
            self.assertEqual(len(await get_stable_preferences(self.pool)), 1)
            self.assertEqual(await get_stable_preferences(other.pool), [])
        finally:
            other.pool.connection._connection.close()
