from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
import unittest

import libsql

from app.database.turso import TursoConnection, TursoPool
from app.services.mindcore.decisions import (
    DecisionCandidate,
    apply_grounded_decision_execution,
    cancel_active_decision_from_reply,
    record_decision,
)
from app.services.mindcore.goals import (
    apply_grounded_goal_progress_from_event,
    capture_self_expression_goal,
    get_need_snapshot,
)
from app.services.mindcore.temporal_grounding import ground


class Pool:
    def __init__(self, database: str = ":memory:") -> None:
        self.connection = TursoConnection(libsql.connect(database))
        self.acquire_count = 0

    @asynccontextmanager
    async def acquire(self):
        self.acquire_count += 1
        yield self.connection

    def close(self) -> None:
        connection, self.connection = self.connection, None
        connection._connection.close()


async def initialize(pool, conversation_id) -> None:
    async with pool.acquire() as connection:
        await connection.execute("create table conversations(conversation_id text primary key)")
        await connection.execute("insert into conversations values($1)", conversation_id)
        await connection.execute("""create table diana_knowledge(
            knowledge_id text primary key, subject_key text unique, canonical_name text,
            knowledge_type text, summary text, confidence real, status text)""")
        for statement in Path("db/migrations/018_goals_needs_v01.sql").read_text().split(";"):
            if statement.strip():
                await connection.execute(statement)
        await connection.execute("""create table decision_log(
            id text primary key, target text not null, old_value text, new_value text, reason text,
            source_episode_ids text not null, created_at text not null, conversation_id text,
            decision_domain text, status text not null default 'active', updated_at text,
            resolved_at text)""")
    await get_need_snapshot(pool)


class DecisionLifecycleGoalProgressTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.pool = Pool()
        self.conversation_id = uuid4()
        await initialize(self.pool, self.conversation_id)

    async def _decision(self, chosen: str, *, options=("스무고개", "업다운")) -> dict:
        return await record_decision(
            self.pool,
            candidate=DecisionCandidate("explicit_choice", chosen, .92, options=options),
            episode_id=None, conversation_id=self.conversation_id,
            user_message_id=uuid4(), assistant_message_id=uuid4(),
            user_text="스무고개, 업다운 게임 중 골라봐",
        )

    async def _multistep_goal(self, *, status: str = "active") -> str:
        identifier = uuid4(); now = datetime.now(timezone.utc)
        metadata = {"progress_plan": {"subject_key": "업다운", "milestones": {
            "rules_learned": .35, "activity_started": .40, "activity_completed": .25,
        }}}
        async with self.pool.acquire() as connection:
            await connection.execute(
                """insert into diana_goals(id,goal_key,goal_type,summary,origin_need,priority,status,progress,confidence,conversation_id,source_type,source_id,created_at,updated_at,expires_at,metadata)
                   values($1,'curiosity:updown_learning','short_term','Learn Updown rules and play','curiosity',.8,$2,0,.8,$3,'test',null,$4,$4,null,$5)""",
                identifier, status, self.conversation_id, now, metadata,
            )
        return str(identifier)

    async def test_new_game_decision_supersedes_only_same_domain_active_decision(self) -> None:
        first = await self._decision("스무고개")
        second = await self._decision("업다운")
        async with self.pool.acquire() as connection:
            rows = await connection.fetch("select id,status,decision_domain,resolved_at from decision_log order by created_at")
        by_id = {str(row["id"]): row for row in rows}
        self.assertEqual(by_id[str(first["id"])]["status"], "superseded")
        self.assertIsNotNone(by_id[str(first["id"])]["resolved_at"])
        self.assertEqual(by_id[str(second["id"])]["status"], "active")
        self.assertEqual(by_id[str(second["id"])]["decision_domain"], "game")

    async def test_explicit_named_cancellation_preserves_row(self) -> None:
        decision = await self._decision("업다운")
        changed = await cancel_active_decision_from_reply(self.pool, self.conversation_id, "아냐, 업다운은 안 할래.")
        self.assertEqual(changed, 1)
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow("select status,resolved_at from decision_log where id=$1", decision["id"])
        self.assertEqual(row["status"], "cancelled")
        self.assertIsNotNone(row["resolved_at"])

    async def test_grounded_game_start_executes_matching_decision_without_deleting_it(self) -> None:
        decision = await self._decision("업다운")
        episode_id = uuid4()
        changed = await apply_grounded_decision_execution(
            self.pool, conversation_id=self.conversation_id, user_text="업다운 게임 시작했어.", episode_id=episode_id,
        )
        self.assertEqual(changed, 1)
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow("select status,resolved_at,source_episode_ids from decision_log where id=$1", decision["id"])
        self.assertEqual(row["status"], "executed")
        self.assertIsNotNone(row["resolved_at"])
        self.assertIn(str(episode_id), row["source_episode_ids"])

    async def test_multistep_progress_is_grounded_monotonic_and_idempotent(self) -> None:
        goal_id = await self._multistep_goal()
        self.assertEqual(await apply_grounded_goal_progress_from_event(
            self.pool, conversation_id=self.conversation_id, user_text="업다운 규칙을 배웠어.", source_id="rules-1",
        ), 1)
        self.assertEqual(await apply_grounded_goal_progress_from_event(
            self.pool, conversation_id=self.conversation_id, user_text="업다운 규칙을 배웠어.", source_id="rules-1",
        ), 0)
        async with self.pool.acquire() as connection:
            self.assertAlmostEqual(float(await connection.fetchval("select progress from diana_goals where id=$1", goal_id)), .35)
        await apply_grounded_goal_progress_from_event(
            self.pool, conversation_id=self.conversation_id, user_text="업다운 게임 시작했어.", source_id="start-1",
        )
        async with self.pool.acquire() as connection:
            self.assertAlmostEqual(float(await connection.fetchval("select progress from diana_goals where id=$1", goal_id)), .75)
        await apply_grounded_goal_progress_from_event(
            self.pool, conversation_id=self.conversation_id, user_text="업다운 한 판 했어.", source_id="done-1",
        )
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow("select status,progress from diana_goals where id=$1", goal_id)
        self.assertEqual(row["status"], "satisfied")
        self.assertEqual(float(row["progress"]), 1.0)

    async def test_candidate_may_progress_without_becoming_active(self) -> None:
        goal_id = await self._multistep_goal(status="candidate")
        changed = await apply_grounded_goal_progress_from_event(
            self.pool, conversation_id=self.conversation_id, user_text="업다운 규칙을 배웠어.", source_id="candidate-rules",
        )
        self.assertEqual(changed, 1)
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow("select status,progress from diana_goals where id=$1", goal_id)
        self.assertEqual(row["status"], "candidate")
        self.assertAlmostEqual(float(row["progress"]), .35)

    async def test_repeated_desire_reinforces_commitment_not_progress(self) -> None:
        goal = await capture_self_expression_goal(
            self.pool, self.conversation_id, "업다운 규칙을 배우고 게임을 해보고 싶어.", uuid4(),
        )
        self.assertIsNotNone(goal)
        assert goal is not None
        repeated = await capture_self_expression_goal(
            self.pool, self.conversation_id, "업다운 규칙을 진짜 배우고 게임을 해보고 싶어.", uuid4(),
        )
        self.assertIsNotNone(repeated)
        assert repeated is not None
        self.assertEqual(repeated.id, goal.id)
        self.assertGreater(repeated.confidence, goal.confidence)
        async with self.pool.acquire() as connection:
            self.assertEqual(float(await connection.fetchval("select progress from diana_goals where id=$1", goal.id)), 0.0)

    async def test_ordinary_text_creates_no_progress_database_work(self) -> None:
        before = self.pool.acquire_count
        changed = await apply_grounded_goal_progress_from_event(
            self.pool, conversation_id=self.conversation_id, user_text="오늘은 그냥 수다를 떨었어.", source_id="ordinary",
        )
        self.assertEqual(changed, 0)
        self.assertEqual(self.pool.acquire_count, before)

    async def test_terminal_decision_is_historical_for_temporal_grounding(self) -> None:
        active = ground("decision", {"status": "active"})
        executed = ground("decision", {"status": "executed"})
        self.assertTrue(active.is_current)
        self.assertFalse(active.is_terminal)
        self.assertFalse(executed.is_current)
        self.assertTrue(executed.is_terminal)

    async def test_restart_preserves_terminal_decision_and_goal_progress(self) -> None:
        with TemporaryDirectory() as directory:
            database = str(Path(directory) / "lifecycle.db")
            first = Pool(database); conversation_id = uuid4()
            await initialize(first, conversation_id)
            now = datetime.now(timezone.utc)
            async with first.acquire() as connection:
                await connection.execute("""insert into diana_goals(id,goal_key,goal_type,summary,origin_need,priority,status,progress,confidence,conversation_id,source_type,source_id,created_at,updated_at,expires_at,metadata)
                    values($1,'curiosity:updown_learning','short_term','Learn Updown','curiosity',.8,'active',.35,.8,$2,'test',null,$3,$3,null,$4)""",
                    uuid4(), conversation_id, now, {"progress_plan": {"subject_key": "업다운", "milestones": {"activity_started": .40}}},
                )
            decision = await record_decision(first, candidate=DecisionCandidate("explicit_choice", "업다운", .9, options=("스무고개", "업다운")), episode_id=None, conversation_id=conversation_id, user_text="스무고개, 업다운 게임 중 골라봐")
            await apply_grounded_decision_execution(first, conversation_id=conversation_id, user_text="업다운 게임 시작했어")
            await apply_grounded_goal_progress_from_event(first, conversation_id=conversation_id, user_text="업다운 게임 시작했어", source_id="restart-start")
            first.close()
            restarted = Pool(database)
            async with restarted.acquire() as connection:
                decision_status = await connection.fetchval("select status from decision_log where id=$1", decision["id"])
                progress = await connection.fetchval("select progress from diana_goals where goal_key='curiosity:updown_learning'")
            self.assertEqual(decision_status, "executed")
            self.assertAlmostEqual(float(progress), .75)
            restarted.close()
