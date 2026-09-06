from __future__ import annotations

from contextlib import asynccontextmanager
import unittest
from uuid import UUID, uuid4

import libsql

from app.database.turso import TursoConnection
from app.services.mindcore import observation_corrections as corrections
from app.services.mindcore.attention import AttentionItem, AttentionSnapshot
from app.services.mindcore.context_builder import build_context
from app.services.mindcore.narrative import get_narrative_snapshot, hydrate_narrative_snapshot
from app.services.mindcore.self_model import get_self_model_snapshot, hydrate_self_model_snapshot
from app.services.mindcore.snapshot_scope import CognitiveSnapshotScope


class LocalPool:
    def __init__(self) -> None:
        self.connection = TursoConnection(libsql.connect(":memory:"))

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


class FailingPool:
    @asynccontextmanager
    async def acquire(self):
        raise RuntimeError("isolated hydration failure")
        yield


class CognitiveSnapshotConsistencyTests(unittest.IsolatedAsyncioTestCase):
    async def _pool(self) -> LocalPool:
        pool = LocalPool()
        async with pool.acquire() as connection:
            await connection.execute(
                """create table diana_narratives(
                       id text primary key, narrative_key text, subject_key text, category text,
                       summary text, status text, confidence real, evidence_count integer,
                       distinct_episode_count integer, distinct_conversation_count integer,
                       first_observed_at text, last_observed_at text, created_at text, updated_at text)"""
            )
            await connection.execute(
                """create table diana_self_model(
                       id text primary key, claim_key text, category text, subject text,
                       summary text, confidence real, status text, support_count integer,
                       contradiction_count integer, conversation_count integer,
                       first_observed_at text, last_reinforced_at text, created_at text, updated_at text)"""
            )
        return pool

    async def _narrative(self, pool: LocalPool, summary: str, subject: str = "school_friends") -> UUID:
        item_id = uuid4()
        async with pool.acquire() as connection:
            await connection.execute(
                """insert into diana_narratives values(
                       $1,$2,$3,'activity_pattern',$4,'emerging',.7,4,2,2,
                       '2026-01-01','2026-01-02','2026-01-01','2026-01-02')""",
                item_id, f"activity:{subject}", subject, summary,
            )
        return item_id

    async def _self_model(self, pool: LocalPool, summary: str, subject: str = "game_playing") -> UUID:
        item_id = uuid4()
        async with pool.acquire() as connection:
            await connection.execute(
                """insert into diana_self_model values(
                       $1,$2,'narrative_theme',$3,$4,.8,'established',8,0,5,
                       '2026-01-01','2026-01-02','2026-01-01','2026-01-02')""",
                item_id, f"narrative_theme:{subject}", subject, summary,
            )
        return item_id

    async def test_narrative_patch_and_delete_reach_the_next_prompt_context(self) -> None:
        pool = await self._pool()
        scope = CognitiveSnapshotScope()
        item_id = await self._narrative(pool, "OLD_NARRATIVE_CONTEXT")
        await hydrate_narrative_snapshot(pool, scope)

        await corrections.update_narrative(pool, item_id, "NEW_NARRATIVE_CONTEXT", scope)

        async with pool.acquire() as connection:
            self.assertEqual(
                await connection.fetchval("select summary from diana_narratives where id=$1", item_id),
                "NEW_NARRATIVE_CONTEXT",
            )
        snapshot = get_narrative_snapshot(scope)
        self.assertEqual(snapshot[0]["summary"], "NEW_NARRATIVE_CONTEXT")
        attention = AttentionSnapshot(
            primary_focus=AttentionItem("narrative", str(item_id), "school friends", .8, (), "current"),
        )
        prompt_context = build_context(
            current_user_message="오늘 학교 친구들이랑 놀았어.",
            recent_messages=[], memories=[], internal_state={}, working_memory=None,
            attention=attention, narratives=snapshot,
        ).dynamic_context or ""
        self.assertIn("NEW_NARRATIVE_CONTEXT", prompt_context)
        self.assertNotIn("OLD_NARRATIVE_CONTEXT", prompt_context)

        await corrections.delete_narrative(pool, item_id, scope)

        async with pool.acquire() as connection:
            self.assertEqual(await connection.fetchval("select count(*) from diana_narratives"), 0)
        self.assertEqual(get_narrative_snapshot(scope), ())
        deleted_context = build_context(
            current_user_message="오늘 학교 친구들이랑 놀았어.",
            recent_messages=[], memories=[], internal_state={}, working_memory=None,
            attention=attention, narratives=get_narrative_snapshot(scope),
        ).dynamic_context or ""
        self.assertNotIn("NEW_NARRATIVE_CONTEXT", deleted_context)

    async def test_self_model_patch_and_delete_reach_the_next_prompt_context(self) -> None:
        pool = await self._pool()
        scope = CognitiveSnapshotScope()
        item_id = await self._self_model(pool, "OLD_SELF_MODEL_CONTEXT")
        await hydrate_self_model_snapshot(pool, scope)

        await corrections.update_self_model(pool, item_id, "NEW_SELF_MODEL_CONTEXT", scope)

        async with pool.acquire() as connection:
            self.assertEqual(
                await connection.fetchval("select summary from diana_self_model where id=$1", item_id),
                "NEW_SELF_MODEL_CONTEXT",
            )
        snapshot = get_self_model_snapshot(scope)
        self.assertEqual(snapshot[0]["summary"], "NEW_SELF_MODEL_CONTEXT")
        attention = AttentionSnapshot(
            primary_focus=AttentionItem("self_model", str(item_id), "game playing", .8, (), "current"),
        )
        prompt_context = build_context(
            current_user_message="새 게임 해볼래?", recent_messages=[], memories=[],
            internal_state={}, working_memory=None, attention=attention, self_models=snapshot,
        ).dynamic_context or ""
        self.assertIn("NEW_SELF_MODEL_CONTEXT", prompt_context)
        self.assertNotIn("OLD_SELF_MODEL_CONTEXT", prompt_context)

        await corrections.delete_self_model(pool, item_id, scope)

        async with pool.acquire() as connection:
            self.assertEqual(await connection.fetchval("select count(*) from diana_self_model"), 0)
        self.assertEqual(get_self_model_snapshot(scope), ())
        deleted_context = build_context(
            current_user_message="새 게임 해볼래?", recent_messages=[], memories=[],
            internal_state={}, working_memory=None, attention=attention,
            self_models=get_self_model_snapshot(scope),
        ).dynamic_context or ""
        self.assertNotIn("NEW_SELF_MODEL_CONTEXT", deleted_context)

    async def test_failed_correction_keeps_the_last_valid_snapshot(self) -> None:
        pool = await self._pool()
        scope = CognitiveSnapshotScope()
        item_id = await self._narrative(pool, "LAST_VALID")
        await hydrate_narrative_snapshot(pool, scope)

        with self.assertRaises(KeyError):
            await corrections.update_narrative(pool, uuid4(), "MUST_NOT_APPEAR", scope)

        self.assertEqual(get_narrative_snapshot(scope)[0]["summary"], "LAST_VALID")
        async with pool.acquire() as connection:
            self.assertEqual(
                await connection.fetchval("select summary from diana_narratives where id=$1", item_id),
                "LAST_VALID",
            )

    async def test_late_background_result_cannot_restore_a_corrected_row(self) -> None:
        pool = await self._pool()
        scope = CognitiveSnapshotScope()
        item_id = await self._narrative(pool, "STALE_BACKGROUND_ROW")
        await hydrate_narrative_snapshot(pool, scope)
        stale_rows = get_narrative_snapshot(scope)
        background_epoch = scope.narrative.begin_refresh()

        await corrections.delete_narrative(pool, item_id, scope)
        published = scope.narrative.publish_update(
            background_epoch,
            lambda current: (*current, *stale_rows),
        )

        self.assertFalse(published)
        self.assertEqual(get_narrative_snapshot(scope), ())

        unrelated_id = uuid4()
        unrelated = {
            **dict(stale_rows[0]),
            "id": unrelated_id,
            "narrative_key": "activity:music",
            "subject_key": "music",
            "summary": "UNRELATED_BACKGROUND_ROW",
        }
        current_epoch = scope.narrative.begin_refresh()
        self.assertTrue(scope.narrative.publish_update(current_epoch, lambda current: (*current, unrelated)))
        ids = {str(row["id"]) for row in get_narrative_snapshot(scope)}
        self.assertEqual(ids, {str(unrelated_id)})

    async def test_app_database_scopes_remain_isolated_in_both_hydration_orders(self) -> None:
        pool_a = await self._pool()
        pool_b = await self._pool()
        scope_a = CognitiveSnapshotScope()
        scope_b = CognitiveSnapshotScope()
        narrative_a = await self._narrative(pool_a, "NARRATIVE_A")
        narrative_b = await self._narrative(pool_b, "NARRATIVE_B")
        self_a = await self._self_model(pool_a, "SELF_A")
        self_b = await self._self_model(pool_b, "SELF_B")

        await hydrate_narrative_snapshot(pool_a, scope_a)
        await hydrate_self_model_snapshot(pool_a, scope_a)
        await hydrate_narrative_snapshot(pool_b, scope_b)
        await hydrate_self_model_snapshot(pool_b, scope_b)

        self.assertEqual({str(row["id"]) for row in get_narrative_snapshot(scope_a)}, {str(narrative_a)})
        self.assertEqual({str(row["id"]) for row in get_narrative_snapshot(scope_b)}, {str(narrative_b)})
        self.assertEqual({str(row["id"]) for row in get_self_model_snapshot(scope_a)}, {str(self_a)})
        self.assertEqual({str(row["id"]) for row in get_self_model_snapshot(scope_b)}, {str(self_b)})

        await hydrate_self_model_snapshot(pool_b, scope_b)
        await hydrate_narrative_snapshot(pool_b, scope_b)
        await hydrate_self_model_snapshot(pool_a, scope_a)
        await hydrate_narrative_snapshot(pool_a, scope_a)
        self.assertEqual(get_narrative_snapshot(scope_a)[0]["summary"], "NARRATIVE_A")
        self.assertEqual(get_narrative_snapshot(scope_b)[0]["summary"], "NARRATIVE_B")
        self.assertEqual(get_self_model_snapshot(scope_a)[0]["summary"], "SELF_A")
        self.assertEqual(get_self_model_snapshot(scope_b)[0]["summary"], "SELF_B")

    async def test_restart_hydration_matches_durable_corrections(self) -> None:
        pool = await self._pool()
        running_scope = CognitiveSnapshotScope()
        narrative_id = await self._narrative(pool, "BEFORE_RESTART")
        self_id = await self._self_model(pool, "DELETE_BEFORE_RESTART")
        await hydrate_narrative_snapshot(pool, running_scope)
        await hydrate_self_model_snapshot(pool, running_scope)
        await corrections.update_narrative(pool, narrative_id, "AFTER_RESTART", running_scope)
        await corrections.delete_self_model(pool, self_id, running_scope)

        restarted_scope = CognitiveSnapshotScope()
        await hydrate_narrative_snapshot(pool, restarted_scope)
        await hydrate_self_model_snapshot(pool, restarted_scope)

        self.assertEqual(get_narrative_snapshot(restarted_scope)[0]["summary"], "AFTER_RESTART")
        self.assertEqual(get_self_model_snapshot(restarted_scope), ())

    async def test_hydration_failure_preserves_only_same_scope_last_known_good(self) -> None:
        pool = await self._pool()
        scope_a = CognitiveSnapshotScope()
        scope_b = CognitiveSnapshotScope()
        await self._narrative(pool, "ONLY_SCOPE_A")
        await self._self_model(pool, "ONLY_SELF_A")
        await hydrate_narrative_snapshot(pool, scope_a)
        await hydrate_self_model_snapshot(pool, scope_a)

        self.assertEqual(await hydrate_narrative_snapshot(FailingPool(), scope_b), ())
        self.assertEqual(await hydrate_self_model_snapshot(FailingPool(), scope_b), ())
        self.assertEqual(get_narrative_snapshot(scope_b), ())
        self.assertEqual(get_self_model_snapshot(scope_b), ())
        self.assertEqual(get_narrative_snapshot(scope_a)[0]["summary"], "ONLY_SCOPE_A")
        self.assertEqual(get_self_model_snapshot(scope_a)[0]["summary"], "ONLY_SELF_A")
