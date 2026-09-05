from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4
import unittest

import libsql

from app.database.turso import TursoConnection
from app.services.mindcore.decisions import (
    DECISION_DURABLE_THRESHOLD,
    SELF_DIRECTED_CHOICE_CONFIDENCE,
    apply_grounded_decision_execution,
    cancel_active_decision_from_reply,
    decision_domain,
    decision_rejection_reason,
    detect_decision,
    is_durable_decision,
    record_decision,
)


class Pool:
    def __init__(self) -> None:
        self.connection = TursoConnection(libsql.connect(":memory:"))
        self.acquire_count = 0

    @asynccontextmanager
    async def acquire(self):
        self.acquire_count += 1
        yield self.connection


async def initialise(pool: Pool, conversation_id) -> None:
    async with pool.acquire() as connection:
        await connection.execute("create table conversations(conversation_id text primary key)")
        await connection.execute("insert into conversations values($1)", conversation_id)
        await connection.execute("""create table decision_log(
            id text primary key, target text not null, old_value text, new_value text, reason text,
            source_episode_ids text not null, created_at text not null, conversation_id text,
            decision_domain text, status text not null default 'active', updated_at text,
            resolved_at text)""")


class DecisionAcquisitionV3Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.pool = Pool()
        self.conversation_id = uuid4()
        await initialise(self.pool, self.conversation_id)

    def _candidate(self, reply: str):
        candidate = detect_decision("오늘은 뭐 할까?", reply)
        self.assertIsNotNone(candidate, reply)
        assert candidate is not None
        self.assertTrue(is_durable_decision(candidate))
        self.assertEqual(candidate.confidence, SELF_DIRECTED_CHOICE_CONFIDENCE)
        return candidate

    async def _persist(self, reply: str) -> dict:
        candidate = self._candidate(reply)
        return await record_decision(
            self.pool,
            candidate=candidate,
            episode_id=None,
            conversation_id=self.conversation_id,
            user_message_id=uuid4(),
            assistant_message_id=uuid4(),
            user_text="오늘은 뭐 할까?",
            diana_text=reply,
        )

    async def test_calibration_funnel_has_eight_explicit_decisions_and_no_false_positives(self) -> None:
        must_capture = (
            ("그럼 이번에는 업다운부터 할래.", "game"),
            ("이번에는 스무고개로 하자.", "game"),
            ("다음 이야기는 북풍과 태양으로 할래.", "story"),
            ("다음 게임은 스무고개로 정할래.", "game"),
            ("스무고개 대신 업다운부터 하자.", "game"),
            ("업다운부터 해보자.", "game"),
            ("이번에는 산책을 하자.", "activity"),
            ("그림 그리기부터 할게.", "activity"),
        )
        ambiguous = (
            "업다운 할까?", "스무고개 어때?", "나중에 스무고개 해보고 싶어.",
            "다음엔 스무고개 해보고 싶어.", "이번엔 뭐 할까?", "오늘은 이걸 먼저 해보자.",
            "업다운 재밌겠다.", "북풍과 태양이 궁금해.",
        )
        non_decisions = (
            "오늘 학교 재밌었어.", "친구들 만나는 게 좋아.", "점심 먹었어.",
            "그렇구나.", "잘 모르겠어.", "스무고개가 뭐야?", "피곤해.",
        )

        captured = [detect_decision("오늘은 뭐 할까?", reply) for reply, _domain in must_capture]
        self.assertEqual(len([item for item in captured if item is not None]), 8)
        self.assertTrue(all(item is not None and item.confidence >= DECISION_DURABLE_THRESHOLD for item in captured))
        for item, (_reply, expected_domain) in zip(captured, must_capture, strict=True):
            assert item is not None
            self.assertEqual(decision_domain(item, diana_text=_reply), expected_domain)
        created = []
        for item, (reply, _expected_domain) in zip(captured, must_capture, strict=True):
            assert item is not None
            conversation_id = uuid4()
            async with self.pool.acquire() as connection:
                await connection.execute("insert into conversations values($1)", conversation_id)
            created.append(await record_decision(
                self.pool, candidate=item, episode_id=None, conversation_id=conversation_id,
                user_message_id=uuid4(), assistant_message_id=uuid4(), user_text="오늘은 뭐 할까?", diana_text=reply,
            ))
        self.assertEqual([item["acquisition_result"] for item in created], ["created"] * 8)
        self.assertTrue(all(detect_decision("오늘은 뭐 할까?", reply) is None for reply in ambiguous))
        self.assertTrue(all(detect_decision("", reply) is None for reply in non_decisions))
        self.assertEqual(decision_rejection_reason("업다운 하자.", "그래."), "user_command_without_self_commitment")

    async def test_self_directed_game_is_durable_and_same_choice_is_a_noop(self) -> None:
        first = await self._persist("그럼 이번에는 업다운부터 할래.")
        duplicate = await self._persist("응, 업다운으로 하자.")
        self.assertEqual(first["acquisition_result"], "created")
        self.assertEqual(duplicate["acquisition_result"], "duplicate")
        self.assertEqual(str(first["id"]), str(duplicate["id"]))
        async with self.pool.acquire() as connection:
            rows = await connection.fetch("select status,decision_domain,new_value from decision_log")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "active")
        self.assertEqual(rows[0]["decision_domain"], "game")
        self.assertEqual(rows[0]["new_value"]["chosen"], "업다운")

    async def test_self_directed_story_and_game_supersession_keep_lifecycle_semantics(self) -> None:
        story = await self._persist("다음 이야기는 북풍과 태양으로 할래.")
        game_a = await self._persist("스무고개 하자.")
        game_b = await self._persist("아냐, 업다운부터 할래.")
        async with self.pool.acquire() as connection:
            rows = await connection.fetch("select id,status,decision_domain from decision_log order by created_at")
        statuses = {str(row["id"]): row["status"] for row in rows}
        self.assertEqual(statuses[str(story["id"])], "active")
        self.assertEqual(statuses[str(game_a["id"])], "superseded")
        self.assertEqual(statuses[str(game_b["id"])], "active")

    async def test_named_cancellation_and_grounded_execution_remain_connected(self) -> None:
        decision = await self._persist("업다운부터 하자.")
        self.assertEqual(await cancel_active_decision_from_reply(self.pool, self.conversation_id, "아냐, 업다운은 안 할래."), 1)
        async with self.pool.acquire() as connection:
            self.assertEqual(await connection.fetchval("select status from decision_log where id=$1", decision["id"]), "cancelled")

        active = await self._persist("스무고개 하자.")
        episode_id = uuid4()
        self.assertEqual(await apply_grounded_decision_execution(
            self.pool, conversation_id=self.conversation_id, user_text="스무고개 게임 시작했어.", episode_id=episode_id,
        ), 1)
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow("select status,source_episode_ids from decision_log where id=$1", active["id"])
        self.assertEqual(row["status"], "executed")
        self.assertIn(str(episode_id), row["source_episode_ids"])

    async def test_non_decision_detection_never_opens_a_database_path(self) -> None:
        before = self.pool.acquire_count
        candidate = detect_decision("업다운 하자.", "업다운 재밌겠다.")
        self.assertIsNone(candidate)
        self.assertEqual(self.pool.acquire_count, before)
