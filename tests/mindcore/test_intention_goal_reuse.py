from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
import unittest

import libsql

from app.database.turso import TursoConnection, TursoPool
from app.services.mindcore.goals import Goal, get_need_snapshot, get_relevant_goals, update_goals
from app.services.mindcore.intentions import select_response_intention
from app.services.mindcore.working_memory import WorkingMemoryItem, WorkingMemoryState


class Pool:
    def __init__(self) -> None:
        self.connection = TursoConnection(libsql.connect(":memory:"))

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


async def initialize(pool, conversation_ids) -> None:
    async with pool.acquire() as connection:
        await connection.execute("create table conversations(conversation_id text primary key)")
        for conversation_id in conversation_ids:
            await connection.execute("insert into conversations values($1)", conversation_id)
        for statement in Path("db/migrations/018_goals_needs_v01.sql").read_text().split(";"):
            if statement.strip():
                await connection.execute(statement)
    await get_need_snapshot(pool)


async def insert_goal(pool, conversation_id, *, key: str, summary: str, status: str, priority: float, expires_at=None, goal_type="short_term"):
    identifier = uuid4()
    now = datetime.now(timezone.utc)
    async with pool.acquire() as connection:
        await connection.execute(
            """insert into diana_goals(id,goal_key,goal_type,summary,origin_need,priority,status,progress,confidence,conversation_id,source_type,created_at,updated_at,expires_at,metadata)
               values($1,$2,$3,$4,'curiosity',$5,$6,0,.8,$7,'test',$8,$8,$9,'{}')""",
            identifier, key, goal_type, summary, priority, status, conversation_id, now, expires_at,
        )
    return identifier


class IntentionGoalReuseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.pool = Pool()
        self.conversation_id = uuid4()
        self.other_conversation_id = uuid4()
        self.message_id = uuid4()
        await initialize(self.pool, (self.conversation_id, self.other_conversation_id))

    async def test_turn_result_matches_db_relevant_goals_after_lifecycle(self) -> None:
        now = datetime.now(timezone.utc)
        await insert_goal(self.pool, self.conversation_id, key="first", summary="Learn more about First", status="active", priority=.9)
        await insert_goal(self.pool, self.conversation_id, key="second", summary="Learn more about Second", status="active", priority=.8)
        await insert_goal(self.pool, self.conversation_id, key="expired", summary="Learn more about Expired", status="active", priority=1.0, expires_at=now - timedelta(seconds=1))
        await insert_goal(self.pool, self.conversation_id, key="satisfied", summary="Clarify Satisfied", status="active", priority=.7, goal_type="open_loop_clarification")
        await insert_goal(self.pool, self.conversation_id, key="abandoned", summary="Learn more about Abandoned", status="abandoned", priority=1.0)
        await insert_goal(self.pool, self.conversation_id, key="renewed", summary="Learn more about Renewed", status="expired", priority=1.0)
        await insert_goal(self.pool, self.other_conversation_id, key="other", summary="Learn more about Other", status="active", priority=1.0)

        result = await update_goals(self.pool, self.conversation_id, "ㅋㅋ")
        reread = await get_relevant_goals(self.pool, self.conversation_id, None)

        self.assertEqual([goal.id for goal in result.relevant_goals], [goal.id for goal in reread])
        self.assertEqual([goal.goal_key for goal in result.relevant_goals], ["first", "second"])
        self.assertTrue(all(goal.conversation_id == self.conversation_id for goal in result.relevant_goals))

    async def test_selector_preserves_priority_and_grounding_with_request_scoped_goals(self) -> None:
        goal = Goal("goal-1", "curiosity:world-model", "Learn more about World Model", "curiosity", .8, "active", 0, .8, self.conversation_id, "message", "source", datetime.now(timezone.utc), None)
        matching = WorkingMemoryState(self.conversation_id, [WorkingMemoryItem("focus", "World Model", .9)])
        mismatch = WorkingMemoryState(self.conversation_id, [WorkingMemoryItem("focus", "School", .9)])

        explicit = await select_response_intention(self.conversation_id, self.message_id, "이 문장 요약해줘", working_memory=matching, relevant_goals=(goal,))
        question = await select_response_intention(self.conversation_id, self.message_id, "이게 뭐야?", working_memory=matching, relevant_goals=(goal,))
        followup = await select_response_intention(self.conversation_id, self.message_id, "ㅋㅋ", working_memory=matching, relevant_goals=(goal,))
        no_followup = await select_response_intention(self.conversation_id, self.message_id, "ㅋㅋ", working_memory=mismatch, relevant_goals=(goal,))

        self.assertEqual(explicit.action, "fulfill_request")
        self.assertEqual(question.action, "answer")
        self.assertEqual(followup.action, "ask_followup")
        self.assertEqual(followup.source_refs["goal_id"], "goal-1")
        self.assertEqual(no_followup.action, "acknowledge")

    async def test_selector_rejects_other_conversation_goal(self) -> None:
        foreign = Goal("foreign", "curiosity:other", "Learn more about Other", "curiosity", .9, "active", 0, .8, self.other_conversation_id, "message", "source", datetime.now(timezone.utc), None)
        working_memory = WorkingMemoryState(self.conversation_id, [WorkingMemoryItem("focus", "Other", .9)])

        intention = await select_response_intention(self.conversation_id, self.message_id, "ㅋㅋ", working_memory=working_memory, relevant_goals=(foreign,))

        self.assertEqual(intention.action, "acknowledge")

    async def test_turn_result_includes_goal_formed_before_final_selection(self) -> None:
        async with self.pool.acquire() as connection:
            await connection.execute("update diana_needs set value=.70 where need_key='curiosity'")
        working_memory = WorkingMemoryState(self.conversation_id, [WorkingMemoryItem("focus", "New Topic", .9)])

        result = await update_goals(
            self.pool, self.conversation_id, "새로운 주제에 대해 같이 이야기하자", uuid4(),
            working_memory=working_memory, epistemic_unknown=True,
        )
        reread = await get_relevant_goals(self.pool, self.conversation_id, working_memory)

        self.assertEqual([goal.id for goal in result.relevant_goals], [goal.id for goal in reread])
        self.assertTrue(any(goal.summary == "Learn more about New Topic" for goal in result.relevant_goals))

    async def test_restart_reloads_durable_goal_before_new_turn(self) -> None:
        with TemporaryDirectory() as directory:
            database = str(Path(directory) / "goals.db")
            conversation_id = uuid4()
            first = TursoPool(database, "isolated-test-token")
            await initialize(first, (conversation_id,))
            await insert_goal(first, conversation_id, key="durable", summary="Learn more about Durable", status="active", priority=.8)

            restarted = TursoPool(database, "isolated-test-token")
            result = await update_goals(restarted, conversation_id, "ㅋㅋ")
            working_memory = WorkingMemoryState(conversation_id, [WorkingMemoryItem("focus", "Durable", .9)])
            intention = await select_response_intention(conversation_id, self.message_id, "ㅋㅋ", working_memory=working_memory, relevant_goals=result.relevant_goals)

            self.assertEqual([goal.goal_key for goal in result.relevant_goals], ["durable"])
            self.assertEqual(intention.action, "ask_followup")
