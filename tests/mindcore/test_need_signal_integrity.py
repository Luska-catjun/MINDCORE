from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4
import unittest
from unittest.mock import patch

import libsql

from app.database.turso import TursoConnection, TursoPool
from app.services.mindcore.goals import (
    BASELINES,
    Need,
    _apply,
    _form_goals,
    get_need_snapshot,
)


MIGRATION = Path("db/migrations/018_goals_needs_v01.sql")


class SingleConnectionPool:
    """Small isolated pool used only to inject deterministic write failures."""

    def __init__(self, connection: TursoConnection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


class FailNeedUpdateConnection(TursoConnection):
    async def execute(self, statement, *args):
        if statement.lstrip().casefold().startswith("update diana_needs"):
            raise RuntimeError("injected need update failure")
        return await super().execute(statement, *args)


class RecordingConnection(TursoConnection):
    def __init__(self, connection) -> None:
        super().__init__(connection)
        self.operations: list[str] = []

    async def execute(self, statement, *args):
        normalized = statement.lstrip().casefold()
        if normalized.startswith("insert into diana_need_events"):
            self.operations.append("event_insert")
        elif normalized.startswith("update diana_need_events"):
            self.operations.append("event_update")
        elif normalized.startswith("update diana_needs"):
            self.operations.append("need_update")
        elif normalized.startswith("select"):
            self.operations.append("select")
        else:
            self.operations.append("other")
        return await super().execute(statement, *args)


class AlwaysLockedNeedConnection(TursoConnection):
    def __init__(self, connection) -> None:
        super().__init__(connection)
        self.event_attempts = 0

    async def fetchrow(self, statement, *args):
        if statement.lstrip().casefold().startswith("insert into diana_need_events"):
            self.event_attempts += 1
            raise ValueError("database is locked")
        return await super().fetchrow(statement, *args)


class ConflictOnceNeedConnection(TursoConnection):
    def __init__(self, connection) -> None:
        super().__init__(connection)
        self.conflict_next_update = False
        self.update_attempts = 0

    async def fetchrow(self, statement, *args):
        if statement.lstrip().casefold().startswith("update diana_needs"):
            self.update_attempts += 1
            if self.conflict_next_update:
                self.conflict_next_update = False
                return None
        return await super().fetchrow(statement, *args)


async def initialize(pool, conversation_id: UUID) -> None:
    async with pool.acquire() as connection:
        await connection.execute("create table conversations(conversation_id text primary key)")
        await connection.execute("insert into conversations values($1)", conversation_id)
        for statement in MIGRATION.read_text().split(";"):
            if statement.strip():
                await connection.execute(statement)


class NeedSignalIntegrityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.database = str(Path(self.directory.name) / "needs-integrity.db")
        self.pool = TursoPool(self.database, "isolated-test-token")
        self.conversation_id = uuid4()
        await initialize(self.pool, self.conversation_id)

    async def asyncTearDown(self) -> None:
        self.directory.cleanup()

    async def _event_rows(self):
        async with self.pool.acquire() as connection:
            return await connection.fetch(
                "select need_key,delta,before_value,after_value,reason,source_type,source_id,conversation_id,fingerprint "
                "from diana_need_events order by created_at,id"
            )

    async def _need_value(self, key: str) -> float:
        async with self.pool.acquire() as connection:
            return float(await connection.fetchval("select value from diana_needs where need_key=$1", key))

    async def test_new_signal_persists_one_grounded_event_with_provenance(self) -> None:
        needs = await get_need_snapshot(self.pool)

        await _apply(
            self.pool, needs, "curiosity", 0.20, "new_signal", "message", "message-1", self.conversation_id
        )

        self.assertAlmostEqual(await self._need_value("curiosity"), 0.65)
        rows = await self._event_rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["fingerprint"], "curiosity:message:message-1:new_signal")
        self.assertEqual(row["need_key"], "curiosity")
        self.assertAlmostEqual(float(row["delta"]), 0.20)
        self.assertAlmostEqual(float(row["before_value"]), BASELINES["curiosity"])
        self.assertAlmostEqual(float(row["after_value"]), 0.65)
        self.assertEqual(row["reason"], "new_signal")
        self.assertEqual(row["source_type"], "message")
        self.assertEqual(row["source_id"], "message-1")
        self.assertEqual(UUID(str(row["conversation_id"])), self.conversation_id)

    async def test_exact_duplicate_is_a_noop_for_state_and_history(self) -> None:
        needs = await get_need_snapshot(self.pool)
        args = ("curiosity", 0.20, "duplicate_probe", "message", "same-message", self.conversation_id)

        await _apply(self.pool, needs, *args)
        await _apply(self.pool, needs, *args)

        self.assertAlmostEqual(needs["curiosity"].value, 0.65)
        self.assertAlmostEqual(await self._need_value("curiosity"), 0.65)
        self.assertEqual(len(await self._event_rows()), 1)

    async def test_different_sources_apply_independently(self) -> None:
        needs = await get_need_snapshot(self.pool)

        await _apply(self.pool, needs, "curiosity", 0.05, "signal", "message", "source-a", self.conversation_id)
        await _apply(self.pool, needs, "curiosity", 0.05, "signal", "message", "source-b", self.conversation_id)

        self.assertAlmostEqual(await self._need_value("curiosity"), 0.55)
        rows = await self._event_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["fingerprint"] for row in rows}, {
            "curiosity:message:source-a:signal",
            "curiosity:message:source-b:signal",
        })

    async def test_distinct_stale_snapshots_chain_from_authoritative_need(self) -> None:
        fixed = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
        with patch("app.services.mindcore.goals._now", return_value=fixed):
            left = await get_need_snapshot(self.pool, now=fixed)
            right = await get_need_snapshot(self.pool, now=fixed)

            await _apply(
                self.pool, left, "curiosity", 0.03, "stale_signal", "message", "source-a", self.conversation_id
            )
            await _apply(
                self.pool, right, "curiosity", 0.03, "stale_signal", "message", "source-b", self.conversation_id
            )

        self.assertAlmostEqual(await self._need_value("curiosity"), 0.51)
        rows = await self._event_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            sorted((round(float(row["before_value"]), 6), round(float(row["after_value"]), 6)) for row in rows),
            [(0.45, 0.48), (0.48, 0.51)],
        )

    async def test_three_distinct_stale_snapshots_all_apply_once(self) -> None:
        fixed = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
        with patch("app.services.mindcore.goals._now", return_value=fixed):
            snapshots = [await get_need_snapshot(self.pool, now=fixed) for _ in range(3)]
            for index, snapshot in enumerate(snapshots):
                await _apply(
                    self.pool, snapshot, "curiosity", 0.03, "triple", "message",
                    f"source-{index}", self.conversation_id,
                )

        self.assertAlmostEqual(await self._need_value("curiosity"), 0.54)
        rows = await self._event_rows()
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            sorted((round(float(row["before_value"]), 6), round(float(row["after_value"]), 6)) for row in rows),
            [(0.45, 0.48), (0.48, 0.51), (0.51, 0.54)],
        )

    async def test_cross_conversation_distinct_concurrent_signals_are_not_lost(self) -> None:
        other_conversation_id = uuid4()
        async with self.pool.acquire() as connection:
            await connection.execute("insert into conversations values($1)", other_conversation_id)
        await get_need_snapshot(self.pool)
        left, right = await asyncio.gather(get_need_snapshot(self.pool), get_need_snapshot(self.pool))

        await asyncio.gather(
            _apply(self.pool, left, "curiosity", 0.03, "cross_conversation", "message", "left", self.conversation_id),
            _apply(self.pool, right, "curiosity", 0.03, "cross_conversation", "message", "right", other_conversation_id),
        )

        fresh_pool = TursoPool(self.database, "isolated-test-token")
        self.assertAlmostEqual((await get_need_snapshot(fresh_pool))["curiosity"].value, 0.51, places=6)
        async with fresh_pool.acquire() as connection:
            self.assertEqual(await connection.fetchval("select count(*) from diana_need_events"), 2)

    async def test_multiple_same_turn_signals_preserve_each_need_and_event(self) -> None:
        needs = await get_need_snapshot(self.pool)
        signals = (
            ("curiosity", 0.03, "unknown_active_topic"),
            ("understanding", 0.025, "open_loop"),
            ("helpfulness", 0.03, "explicit_help_request"),
        )

        for key, delta, reason in signals:
            await _apply(self.pool, needs, key, delta, reason, "message", "turn-1", self.conversation_id)

        self.assertAlmostEqual(await self._need_value("curiosity"), 0.48)
        self.assertAlmostEqual(await self._need_value("understanding"), 0.325)
        self.assertAlmostEqual(await self._need_value("helpfulness"), 0.38)
        self.assertEqual(len(await self._event_rows()), 3)

    async def test_clamp_preserves_bounds_and_history(self) -> None:
        needs = await get_need_snapshot(self.pool)

        await _apply(self.pool, needs, "curiosity", 2.0, "upper", "message", "upper", self.conversation_id)
        await _apply(self.pool, needs, "curiosity", -2.0, "lower", "message", "lower", self.conversation_id)

        self.assertAlmostEqual(await self._need_value("curiosity"), 0.0)
        rows = await self._event_rows()
        self.assertEqual([(float(row["before_value"]), float(row["after_value"])) for row in rows], [(0.45, 1.0), (1.0, 0.0)])

    async def test_lazy_decay_is_applied_before_the_next_signal(self) -> None:
        needs = await get_need_snapshot(self.pool)
        await _apply(self.pool, needs, "curiosity", 0.40, "activate", "message", "activate", self.conversation_id)

        decayed = await get_need_snapshot(
            self.pool, now=needs["curiosity"].updated_at + timedelta(hours=18)
        )
        self.assertAlmostEqual(decayed["curiosity"].value, 0.65, places=3)
        await _apply(self.pool, decayed, "curiosity", 0.10, "after_decay", "message", "after-decay", self.conversation_id)

        self.assertAlmostEqual(await self._need_value("curiosity"), 0.75, places=3)
        row = (await self._event_rows())[-1]
        self.assertAlmostEqual(float(row["before_value"]), 0.65, places=3)
        self.assertAlmostEqual(float(row["after_value"]), 0.75, places=3)

    async def test_concurrent_duplicate_persists_exactly_once(self) -> None:
        # Seed canonical Need rows before opening two independent readers; the
        # concurrency assertion below is about the duplicate signal itself,
        # not concurrent first-run invariant repair.
        await get_need_snapshot(self.pool)
        left, right = await asyncio.gather(get_need_snapshot(self.pool), get_need_snapshot(self.pool))
        args = ("curiosity", 0.20, "concurrent", "message", "same-source", self.conversation_id)

        await asyncio.gather(_apply(self.pool, left, *args), _apply(self.pool, right, *args))

        self.assertAlmostEqual(await self._need_value("curiosity"), 0.65)
        self.assertEqual(len(await self._event_rows()), 1)

    async def test_need_update_failure_rolls_back_the_inserted_event(self) -> None:
        raw = libsql.connect(":memory:")
        pool = SingleConnectionPool(FailNeedUpdateConnection(raw))
        conversation_id = uuid4()
        await initialize(pool, conversation_id)
        needs = await get_need_snapshot(pool)

        with self.assertRaisesRegex(RuntimeError, "injected need update failure"):
            await _apply(pool, needs, "curiosity", 0.20, "failure", "message", "failure-source", conversation_id)

        async with pool.acquire() as connection:
            self.assertAlmostEqual(float(await connection.fetchval("select value from diana_needs where need_key='curiosity'")), 0.45)
            self.assertEqual(await connection.fetchval("select count(*) from diana_need_events"), 0)
        raw.close()

    async def test_write_conflict_retry_is_bounded(self) -> None:
        raw = libsql.connect(":memory:")
        connection = AlwaysLockedNeedConnection(raw)
        needs = {
            "curiosity": self._need_for_retry(),
        }

        with self.assertRaisesRegex(ValueError, "database is locked"):
            await _apply(
                SingleConnectionPool(connection), needs, "curiosity", 0.03,
                "retry_exhausted", "message", "retry", self.conversation_id,
            )

        self.assertEqual(connection.event_attempts, 3)
        self.assertFalse(raw.in_transaction)
        raw.close()

    async def test_detected_stale_write_rolls_back_event_and_retries(self) -> None:
        raw = libsql.connect(":memory:")
        connection = ConflictOnceNeedConnection(raw)
        pool = SingleConnectionPool(connection)
        conversation_id = uuid4()
        await initialize(pool, conversation_id)
        needs = await get_need_snapshot(pool)
        connection.conflict_next_update = True

        await _apply(
            pool, needs, "curiosity", 0.03, "cas_retry", "message", "cas", conversation_id
        )

        self.assertEqual(connection.update_attempts, 2)
        self.assertAlmostEqual(float(await connection.fetchval("select value from diana_needs where need_key='curiosity'")), .48)
        self.assertEqual(await connection.fetchval("select count(*) from diana_need_events"), 1)
        raw.close()

    @staticmethod
    def _need_for_retry():
        fixed = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
        return Need("curiosity", 0.45, 0.45, fixed)

    async def test_insert_first_statement_counts_for_new_and_duplicate_signals(self) -> None:
        raw = libsql.connect(":memory:")
        connection = RecordingConnection(raw)
        pool = SingleConnectionPool(connection)
        conversation_id = uuid4()
        await initialize(pool, conversation_id)
        needs = await get_need_snapshot(pool)
        connection.operations.clear()
        args = ("curiosity", 0.20, "statement_count", "message", "same-source", conversation_id)

        await _apply(pool, needs, *args)
        self.assertEqual(connection.operations, ["event_insert", "select", "need_update", "event_update"])

        connection.operations.clear()
        await _apply(pool, needs, *args)
        self.assertEqual(connection.operations, ["event_insert"])
        raw.close()

    async def test_restart_reads_durable_need_and_event_without_process_cache(self) -> None:
        fixed = datetime.now(timezone.utc)
        with patch("app.services.mindcore.goals._now", return_value=fixed):
            needs = await get_need_snapshot(self.pool, now=fixed)
            await _apply(self.pool, needs, "curiosity", 0.20, "restart", "message", "restart-source", self.conversation_id)

            fresh_pool = TursoPool(self.database, "isolated-test-token")
            reloaded = await get_need_snapshot(fresh_pool, now=fixed)
        self.assertAlmostEqual(reloaded["curiosity"].value, 0.65)
        async with fresh_pool.acquire() as connection:
            self.assertEqual(await connection.fetchval("select count(*) from diana_need_events"), 1)

    async def test_terminal_abandoned_goal_is_renewed_without_rewriting_history(self) -> None:
        needs = await get_need_snapshot(self.pool)
        needs["curiosity"].value = 0.70
        old_id = uuid4()
        now = needs["curiosity"].updated_at
        async with self.pool.acquire() as connection:
            await connection.execute(
                """insert into diana_goals(id,goal_key,goal_type,summary,origin_need,priority,status,progress,confidence,conversation_id,source_type,source_id,created_at,updated_at,expires_at,metadata)
                   values($1,$2,'short_term','old','curiosity',.5,'abandoned',.2,.8,$3,'message','old-source',$4,$4,$5,'{}')""",
                old_id, "curiosity:storybook", self.conversation_id, now, now + timedelta(hours=24),
            )

        created = await _form_goals(self.pool, self.conversation_id, needs, "Storybook", "renew-source")

        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].goal_key.startswith("curiosity:storybook:renewed:renew-source"))
        async with self.pool.acquire() as connection:
            self.assertEqual(await connection.fetchval("select status from diana_goals where id=$1", old_id), "abandoned")
            self.assertEqual(await connection.fetchval("select count(*) from diana_goals where goal_key=$1", created[0].goal_key), 1)
