from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import unittest

import libsql

from app.database.turso import TursoConnection
from app.services.mindcore.goals import (
    SELF_EXPRESSION_CANDIDATE_THRESHOLD,
    SELF_EXPRESSION_PROMOTION_THRESHOLD,
    capture_self_expression_goal,
    classify_self_expression_goal,
    get_need_snapshot,
    refresh_goal_lifecycle,
    satisfy_story_goals,
)


class Pool:
    def __init__(self) -> None:
        self.connection = TursoConnection(libsql.connect(":memory:"))

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


class GoalsNeedsV2Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.pool = Pool()
        self.conversation_id = uuid4()
        async with self.pool.acquire() as connection:
            await connection.execute("create table conversations(conversation_id text primary key)")
            await connection.execute("insert into conversations values($1)", self.conversation_id)
            await connection.execute("""create table diana_knowledge(
                knowledge_id text primary key, subject_key text unique not null,
                canonical_name text not null, knowledge_type text not null,
                summary text not null, confidence real not null, status text not null
            )""")
            for statement in Path("db/migrations/018_goals_needs_v01.sql").read_text().split(";"):
                if statement.strip():
                    await connection.execute(statement)
        await get_need_snapshot(self.pool)

    def test_calibration_classifier(self) -> None:
        creates = (
            "다음에는 새로운 이야기 하나 더 들어보고 싶어.",
            "언젠가 별에 대해서 더 공부해보고 싶어.",
            "나중에 너랑 스무고개 해보고 싶어.",
            "북풍과 태양 이야기를 또 들어보고 싶어.",
            "다음에 로봇 만들기를 해보고 싶어.",
        )
        candidates = (
            "별을 더 알아보고 싶어.",
            "새 이야기 한번 들어보고 싶어.",
            "스무고개 해보고 싶어.",
        )
        rejects = (
            "그거 재밌다.", "예쁘다!", "그럴 수도 있겠다.",
            "북풍과 태양은 유명한 이야기구나.", "물 마시고 싶네",
            "귀엽네.", "궁금하네.",
        )
        for text in creates:
            signal = classify_self_expression_goal(text)
            self.assertIsNotNone(signal, text)
            self.assertGreaterEqual(signal.score, SELF_EXPRESSION_PROMOTION_THRESHOLD)
        for text in candidates:
            signal = classify_self_expression_goal(text)
            self.assertIsNotNone(signal, text)
            self.assertGreaterEqual(signal.score, SELF_EXPRESSION_CANDIDATE_THRESHOLD)
            self.assertLess(signal.score, SELF_EXPRESSION_PROMOTION_THRESHOLD)
        for text in rejects:
            self.assertIsNone(classify_self_expression_goal(text), text)

    async def test_explicit_future_desire_is_self_expression_goal(self) -> None:
        message_id = uuid4()
        goal = await capture_self_expression_goal(
            self.pool, self.conversation_id,
            "다음에는 새로운 이야기 하나 더 들어보고 싶어.", message_id,
        )
        self.assertIsNotNone(goal)
        self.assertEqual(goal.source_type, "self_expression")
        self.assertEqual(goal.source_id, str(message_id))
        self.assertIn(goal.status, {"candidate", "active"})
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow("select source_type,metadata from diana_goals where id=$1", goal.id)
        self.assertEqual(row["source_type"], "self_expression")
        self.assertEqual(float(row["metadata"]["formation_threshold"]), SELF_EXPRESSION_CANDIDATE_THRESHOLD)

    async def test_repeat_reinforces_one_unresolved_goal_then_promotes(self) -> None:
        first = await capture_self_expression_goal(self.pool, self.conversation_id, "별을 더 알아보고 싶어.", uuid4())
        self.assertEqual(first.status, "candidate")
        reinforced = await capture_self_expression_goal(self.pool, self.conversation_id, "별을 진짜 더 알아보고 싶어.", uuid4())
        self.assertEqual(reinforced.id, first.id)
        self.assertGreaterEqual(reinforced.confidence, SELF_EXPRESSION_PROMOTION_THRESHOLD)
        await refresh_goal_lifecycle(self.pool, self.conversation_id)
        async with self.pool.acquire() as connection:
            count = await connection.fetchval("select count(*) from diana_goals where goal_key='curiosity:별'")
            status = await connection.fetchval("select status from diana_goals where id=$1", first.id)
        self.assertEqual(count, 1)
        self.assertEqual(status, "active")

    async def test_known_story_blocks_first_time_desire_but_repeat_is_renewal(self) -> None:
        known_story = [{"subject_key": "북풍과_태양", "knowledge_type": "story", "status": "known"}]
        blocked = await capture_self_expression_goal(
            self.pool, self.conversation_id, "다음에는 북풍과 태양 이야기도 들어보고 싶어.", uuid4(),
            epistemic_items=known_story,
        )
        self.assertIsNone(blocked)
        renewed = await capture_self_expression_goal(
            self.pool, self.conversation_id, "북풍과 태양 이야기를 또 들어보고 싶어.", uuid4(),
            epistemic_items=known_story,
        )
        self.assertIsNotNone(renewed)

    async def test_durable_known_story_blocks_earlier_turn_reply_target(self) -> None:
        async with self.pool.acquire() as connection:
            await connection.execute(
                "insert into diana_knowledge values($1,'북풍과_태양','북풍과 태양','story','grounded',.7,'known')",
                uuid4(),
            )
        blocked = await capture_self_expression_goal(
            self.pool, self.conversation_id, "다음에는 북풍과 태양 이야기도 들어보고 싶어.", uuid4(),
        )
        self.assertIsNone(blocked)

    async def test_terminal_goal_does_not_resurrect_without_explicit_repeat(self) -> None:
        now = datetime.now(timezone.utc)
        async with self.pool.acquire() as connection:
            await connection.execute(
                """insert into diana_goals(id,goal_key,goal_type,summary,origin_need,priority,status,progress,confidence,conversation_id,source_type,source_id,created_at,updated_at,expires_at,metadata)
                   values($1,'curiosity:별','short_term','Explore 별','curiosity',.9,'satisfied',1,.9,$2,'self_expression',null,$3,$3,null,'{}')""",
                uuid4(), self.conversation_id, now,
            )
        ignored = await capture_self_expression_goal(self.pool, self.conversation_id, "다음에 별을 더 알아보고 싶어.", uuid4())
        self.assertIsNone(ignored)
        async with self.pool.acquire() as connection:
            status = await connection.fetchval("select status from diana_goals where goal_key='curiosity:별'")
        self.assertEqual(status, "satisfied")

    async def test_grounded_story_learning_satisfies_matching_goal(self) -> None:
        goal = await capture_self_expression_goal(self.pool, self.conversation_id, "다음에는 북풍과 태양 이야기를 들어보고 싶어.", uuid4())
        self.assertIsNotNone(goal)
        changed = await satisfy_story_goals(self.pool, self.conversation_id, ["북풍과_태양"])
        self.assertEqual(changed, 1)
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow("select status,progress from diana_goals where id=$1", goal.id)
        self.assertEqual(row["status"], "satisfied")
        self.assertEqual(float(row["progress"]), 1.0)

    async def test_user_request_is_not_a_self_goal(self) -> None:
        self.assertIsNone(classify_self_expression_goal("내일 날씨 알려줘."))
