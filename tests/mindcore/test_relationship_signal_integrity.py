from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

import libsql

from app.database.turso import TursoConnection, TursoPool
from app.services.mindcore.relationship import (
    RELATIONSHIP_EVENT_MAX_ATTEMPTS,
    RelationshipState,
    update_relationship_from_experience,
)


class FixedDateTime(datetime):
    current = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        value = cls.current
        return value if tz is None else value.astimezone(tz)


class SingleConnectionPool:
    def __init__(self, connection: TursoConnection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


class AlwaysLockedRelationshipConnection(TursoConnection):
    def __init__(self, connection) -> None:
        super().__init__(connection)
        self.log_attempts = 0

    async def fetchrow(self, statement, *args):
        if statement.lstrip().casefold().startswith("insert into relationship_log"):
            self.log_attempts += 1
            raise ValueError("database is locked")
        return await super().fetchrow(statement, *args)


class ConflictOnceRelationshipConnection(TursoConnection):
    def __init__(self, connection) -> None:
        super().__init__(connection)
        self.conflict_next_update = False
        self.update_attempts = 0

    async def fetchrow(self, statement, *args):
        if statement.lstrip().casefold().startswith("update relationship set"):
            self.update_attempts += 1
            if self.conflict_next_update:
                self.conflict_next_update = False
                return None
        return await super().fetchrow(statement, *args)


class CommitFailingRawConnection:
    def __init__(self) -> None:
        self.raw = libsql.connect(":memory:")
        self.fail_next_commit = False

    @property
    def in_transaction(self):
        return self.raw.in_transaction

    def execute(self, *args, **kwargs):
        return self.raw.execute(*args, **kwargs)

    def commit(self):
        if self.fail_next_commit:
            self.fail_next_commit = False
            raise RuntimeError("injected relationship commit failure")
        return self.raw.commit()

    def rollback(self):
        return self.raw.rollback()

    def close(self):
        return self.raw.close()


async def initialize(pool) -> None:
    async with pool.acquire() as connection:
        await connection.execute(
            "create table experiences(experience_id text primary key,conversation_id text,created_at text not null)"
        )
        await connection.execute(
            """create table relationship(
                id integer primary key,familiarity real,trust real,affection real,
                shared_experience real,conflict_history text not null,
                updated_at text not null,conflict real not null)"""
        )
        await connection.execute(
            "insert into relationship values(1,.10,.30,.15,0,$1,$2,0)",
            [], FixedDateTime.current,
        )
        await connection.execute(
            """create table relationship_log(
                relationship_log_id text primary key,source_experience_id text unique,
                previous_state text not null,delta text not null,new_state text not null,
                reason text not null,created_at text not null)"""
        )


class RelationshipSignalIntegrityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.database = str(Path(self.directory.name) / "relationship-integrity.db")
        self.pool = TursoPool(self.database, "isolated-test-token")
        await initialize(self.pool)

    async def asyncTearDown(self) -> None:
        self.directory.cleanup()

    async def _experience(self, conversation_id: UUID | None = None) -> UUID:
        experience_id = uuid4()
        async with self.pool.acquire() as connection:
            await connection.execute(
                "insert into experiences values($1,$2,$3)",
                experience_id, conversation_id or uuid4(), FixedDateTime.current,
            )
        return experience_id

    async def _trust(self, pool=None) -> float:
        async with (pool or self.pool).acquire() as connection:
            return float(await connection.fetchval("select trust from relationship where id=1"))

    async def _logs(self, pool=None):
        async with (pool or self.pool).acquire() as connection:
            return await connection.fetch(
                "select source_experience_id,previous_state,delta,new_state from relationship_log"
            )

    async def test_three_distinct_stale_snapshots_preserve_diminishing_return_chain(self) -> None:
        stale = RelationshipState(.10, .30, .15, 0)
        experiences = [await self._experience() for _ in range(3)]
        with patch("app.services.mindcore.relationship.datetime", FixedDateTime):
            for experience_id in experiences:
                await update_relationship_from_experience(
                    self.pool, experience_id, user_text="이건 믿고 맡길게.", current_state=stale
                )

        self.assertAlmostEqual(await self._trust(), .3289903208)
        logs = await self._logs()
        self.assertEqual(len(logs), 3)
        chain = sorted(
            (round(float(row["previous_state"]["trust"]), 10), round(float(row["new_state"]["trust"]), 10))
            for row in logs
        )
        self.assertEqual(chain, [(.3, .3098), (.3098, .3194628), (.3194628, .3289903208)])

    async def test_cross_conversation_concurrent_distinct_events_survive_restart(self) -> None:
        left = await self._experience(uuid4())
        right = await self._experience(uuid4())
        stale = RelationshipState(.10, .30, .15, 0)
        with patch("app.services.mindcore.relationship.datetime", FixedDateTime):
            await asyncio.gather(
                update_relationship_from_experience(
                    self.pool, left, user_text="이건 믿고 맡길게.", current_state=stale
                ),
                update_relationship_from_experience(
                    self.pool, right, user_text="이건 믿고 맡길게.", current_state=stale
                ),
            )

        fresh_pool = TursoPool(self.database, "isolated-test-token")
        self.assertAlmostEqual(await self._trust(fresh_pool), .3194628)
        logs = await self._logs(fresh_pool)
        self.assertEqual(len(logs), 2)
        chain = sorted(
            (round(float(row["previous_state"]["trust"]), 7), round(float(row["new_state"]["trust"]), 7))
            for row in logs
        )
        self.assertEqual(chain, [(.3, .3098), (.3098, .3194628)])

    async def test_concurrent_same_experience_is_exactly_once(self) -> None:
        experience_id = await self._experience()
        stale = RelationshipState(.10, .30, .15, 0)
        with patch("app.services.mindcore.relationship.datetime", FixedDateTime):
            results = await asyncio.gather(
                update_relationship_from_experience(
                    self.pool, experience_id, user_text="이건 믿고 맡길게.", current_state=stale
                ),
                update_relationship_from_experience(
                    self.pool, experience_id, user_text="이건 믿고 맡길게.", current_state=stale
                ),
            )

        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertAlmostEqual(await self._trust(), .3098)
        self.assertEqual(len(await self._logs()), 1)

    async def test_lazy_conflict_recovery_is_applied_before_signal(self) -> None:
        experience_id = await self._experience()
        anchor = FixedDateTime.current - timedelta(days=60)
        async with self.pool.acquire() as connection:
            await connection.execute(
                "update relationship set conflict=.20,updated_at=$1 where id=1", anchor
            )
        with patch("app.services.mindcore.relationship.datetime", FixedDateTime):
            result = await update_relationship_from_experience(
                self.pool, experience_id, user_text="오늘 답변은 좀 별로다.",
                current_state=RelationshipState(.10, .30, .15, .20),
            )

        assert result is not None
        self.assertAlmostEqual(result.conflict, .1045)
        log = (await self._logs())[0]
        self.assertAlmostEqual(float(log["previous_state"]["conflict"]), .10)
        self.assertAlmostEqual(float(log["new_state"]["conflict"]), .1045)

    async def test_retry_exhaustion_is_bounded_and_leaves_no_transaction(self) -> None:
        raw = libsql.connect(":memory:")
        connection = AlwaysLockedRelationshipConnection(raw)
        pool = SingleConnectionPool(connection)

        with self.assertRaisesRegex(ValueError, "database is locked"):
            await update_relationship_from_experience(
                pool, uuid4(), user_text="이건 믿고 맡길게.",
                current_state=RelationshipState(.10, .30, .15, 0),
            )

        self.assertEqual(connection.log_attempts, RELATIONSHIP_EVENT_MAX_ATTEMPTS)
        self.assertFalse(raw.in_transaction)
        raw.close()

    async def test_commit_failure_rolls_back_log_and_singleton_state(self) -> None:
        raw = CommitFailingRawConnection()
        connection = TursoConnection(raw)
        pool = SingleConnectionPool(connection)
        await initialize(pool)
        experience_id = uuid4()
        await connection.execute(
            "insert into experiences values($1,$2,$3)", experience_id, uuid4(), FixedDateTime.current
        )
        raw.fail_next_commit = True

        with self.assertRaisesRegex(RuntimeError, "injected relationship commit failure"):
            await update_relationship_from_experience(
                pool, experience_id, user_text="이건 믿고 맡길게.",
                current_state=RelationshipState(.10, .30, .15, 0),
            )

        self.assertAlmostEqual(float(await connection.fetchval("select trust from relationship where id=1")), .30)
        self.assertEqual(await connection.fetchval("select count(*) from relationship_log"), 0)
        self.assertFalse(raw.in_transaction)
        raw.close()

    async def test_detected_stale_write_rolls_back_log_and_retries(self) -> None:
        raw = libsql.connect(":memory:")
        connection = ConflictOnceRelationshipConnection(raw)
        pool = SingleConnectionPool(connection)
        await initialize(pool)
        experience_id = uuid4()
        await connection.execute(
            "insert into experiences values($1,$2,$3)", experience_id, uuid4(), FixedDateTime.current
        )
        connection.conflict_next_update = True

        result = await update_relationship_from_experience(
            pool, experience_id, user_text="이건 믿고 맡길게.",
            current_state=RelationshipState(.10, .30, .15, 0),
        )

        assert result is not None
        self.assertEqual(connection.update_attempts, 2)
        self.assertAlmostEqual(result.trust, .3098)
        self.assertEqual(await connection.fetchval("select count(*) from relationship_log"), 1)
        raw.close()
