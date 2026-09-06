"""Adapter durability and read-only commit regression coverage."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest

import libsql

from app.database.turso import TursoConnection, TursoPool


class CountingRawConnection:
    """Delegate to real local libSQL while recording driver-level calls."""

    def __init__(self, database: str = ":memory:") -> None:
        self.raw = libsql.connect(database)
        self.execute_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0
        self.fail_next_commit = False
        self.fail_next_rollback = False

    @property
    def in_transaction(self):
        return self.raw.in_transaction

    def execute(self, *args, **kwargs):
        self.execute_calls += 1
        return self.raw.execute(*args, **kwargs)

    def commit(self):
        self.commit_calls += 1
        if self.fail_next_commit:
            self.fail_next_commit = False
            raise RuntimeError("injected commit failure")
        return self.raw.commit()

    def rollback(self):
        self.rollback_calls += 1
        if self.fail_next_rollback:
            self.fail_next_rollback = False
            raise RuntimeError("injected rollback failure")
        return self.raw.rollback()

    def close(self):
        self.close_calls += 1
        # libSQL can retain a Windows file handle while the closed native
        # connection object is still referenced.  Drop that reference before
        # TemporaryDirectory attempts to remove a file-backed fixture.
        raw, self.raw = self.raw, None
        return raw.close()


class LocalPool:
    def __init__(self) -> None:
        self.raw = CountingRawConnection()
        self.connection = TursoConnection(self.raw)

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


class BlockingRawConnection(CountingRawConnection):
    """Hold one INSERT so cancellation can race the adapter's worker thread."""

    def __init__(self) -> None:
        super().__init__()
        self.block_next_insert = False
        self.insert_started = threading.Event()
        self.release_insert = threading.Event()
        self.insert_finished = threading.Event()

    def execute(self, statement, *args, **kwargs):
        if self.block_next_insert and statement.casefold().lstrip().startswith("insert"):
            self.block_next_insert = False
            self.insert_started.set()
            self.release_insert.wait(timeout=2)
            try:
                return super().execute(statement, *args, **kwargs)
            finally:
                self.insert_finished.set()
        return super().execute(statement, *args, **kwargs)


async def _run_plan(
    connection: TursoConnection,
    raw: CountingRawConnection,
    *,
    standalone_reads: int,
    standalone_writes: int,
    transaction_statement_counts: list[int],
) -> tuple[int, int]:
    """Execute one audited adapter-operation plan against real local libSQL."""
    await connection.execute("create table probe(value integer)")
    raw.execute_calls = raw.commit_calls = raw.rollback_calls = 0
    for _ in range(standalone_reads):
        await connection.fetchval("select count(*) from probe")
    for value in range(standalone_writes):
        await connection.execute("insert into probe values($1)", value)
    value = standalone_writes
    for statement_count in transaction_statement_counts:
        async with connection.transaction():
            for _ in range(statement_count):
                await connection.execute("insert into probe values($1)", value)
                value += 1
    return raw.execute_calls, raw.commit_calls


class TursoReadAutocommitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.pool = LocalPool()
        await self.pool.connection.execute("create table values_table(value integer primary key)")
        self.pool.raw.execute_calls = self.pool.raw.commit_calls = self.pool.raw.rollback_calls = 0

    async def asyncTearDown(self) -> None:
        self.pool.raw.close()

    async def test_standalone_read_does_not_commit_and_read_after_read_works(self) -> None:
        self.assertEqual(await self.pool.connection.fetchval("select count(*) from values_table"), 0)
        self.assertEqual(await self.pool.connection.fetchval("select count(*) from values_table"), 0)
        self.assertEqual(self.pool.raw.commit_calls, 0)
        self.assertFalse(self.pool.raw.in_transaction)

    async def test_standalone_mutations_and_returning_still_commit(self) -> None:
        await self.pool.connection.execute("insert into values_table values($1)", 1)
        inserted = await self.pool.connection.fetchval(
            "insert into values_table values($1) returning value", 2
        )
        updated = await self.pool.connection.fetchval(
            "update values_table set value=$1 where value=$2 returning value", 3, 2
        )
        deleted = await self.pool.connection.fetchval(
            "delete from values_table where value=$1 returning value", 3
        )
        self.assertEqual((inserted, updated, deleted), (2, 3, 3))
        self.assertEqual(self.pool.raw.commit_calls, 4)
        self.assertFalse(self.pool.raw.in_transaction)

    async def test_read_write_and_commit_failures_preserve_failure_boundaries(self) -> None:
        with self.assertRaises(ValueError):
            await self.pool.connection.fetchval("select missing_column from values_table")
        self.assertEqual(self.pool.raw.commit_calls, 0)

        await self.pool.connection.execute("insert into values_table values($1)", 1)
        self.pool.raw.commit_calls = 0
        with self.assertRaises(ValueError):
            await self.pool.connection.execute("insert into values_table values($1)", 1)
        self.assertEqual(self.pool.raw.commit_calls, 0)

        self.pool.raw.fail_next_commit = True
        with self.assertRaisesRegex(RuntimeError, "injected commit failure"):
            await self.pool.connection.execute("insert into values_table values($1)", 2)
        self.assertFalse(self.pool.raw.in_transaction)
        self.assertEqual(self.pool.raw.rollback_calls, 1)
        self.assertEqual(await self.pool.connection.fetchval("select count(*) from values_table"), 1)

    async def test_read_then_write_is_durable_after_fresh_connection(self) -> None:
        with TemporaryDirectory() as directory:
            database = str(Path(directory) / "read-write.db")
            first = TursoPool(database, "isolated-test-token")
            async with first.acquire() as connection:
                await connection.execute("create table probe(value integer)")
                self.assertEqual(await connection.fetchval("select count(*) from probe"), 0)
                await connection.execute("insert into probe values($1)", 7)
            second = TursoPool(database, "isolated-test-token")
            async with second.acquire() as connection:
                self.assertEqual(await connection.fetchval("select value from probe"), 7)
            await first.close()
            await second.close()

    async def test_explicit_transaction_commits_once_and_failure_rolls_back(self) -> None:
        async with self.pool.connection.transaction():
            await self.pool.connection.fetchval("select count(*) from values_table")
            await self.pool.connection.execute("insert into values_table values($1)", 1)
            await self.pool.connection.execute("insert into values_table values($1)", 2)
        self.assertEqual(self.pool.raw.commit_calls, 1)
        self.assertEqual(await self.pool.connection.fetchval("select count(*) from values_table"), 2)

        self.pool.raw.commit_calls = self.pool.raw.rollback_calls = 0
        with self.assertRaisesRegex(RuntimeError, "forced"):
            async with self.pool.connection.transaction():
                await self.pool.connection.execute("insert into values_table values($1)", 3)
                raise RuntimeError("forced")
        self.assertEqual(self.pool.raw.commit_calls, 0)
        self.assertEqual(self.pool.raw.rollback_calls, 1)
        self.assertEqual(await self.pool.connection.fetchval("select count(*) from values_table"), 2)

    async def test_one_connection_is_clean_for_sequential_reuse_after_commit_and_rollback(self) -> None:
        connection = self.pool.connection
        async with connection.transaction():
            await connection.execute("insert into values_table values($1)", 1)
        self.assertFalse(self.pool.raw.in_transaction)
        self.assertEqual(await connection.fetchval("select count(*) from values_table"), 1)

        with self.assertRaisesRegex(RuntimeError, "forced"):
            async with connection.transaction():
                await connection.execute("insert into values_table values($1)", 2)
                raise RuntimeError("forced")
        self.assertFalse(self.pool.raw.in_transaction)
        self.assertEqual(await connection.fetchval("select count(*) from values_table"), 1)

        # A DML RETURNING cursor is consumed by fetchval before its automatic
        # commit, so the same connection is clean for the next operation.
        self.assertEqual(
            await connection.fetchval("insert into values_table values($1) returning value", 3),
            3,
        )
        self.assertFalse(self.pool.raw.in_transaction)
        self.assertEqual(await connection.fetchval("select count(*) from values_table"), 2)

    async def test_cross_connection_read_close_does_not_block_writer_or_visibility(self) -> None:
        with TemporaryDirectory() as directory:
            database = str(Path(directory) / "cross-connection.db")
            pool = TursoPool(database, "isolated-test-token")
            async with pool.acquire() as connection:
                await connection.execute("create table probe(value integer)")
            async with pool.acquire() as reader:
                self.assertEqual(await reader.fetchval("select count(*) from probe"), 0)
            async with pool.acquire() as writer:
                await writer.execute("insert into probe values($1)", 1)
            async with pool.acquire() as verifier:
                self.assertEqual(await verifier.fetchval("select count(*) from probe"), 1)
            await pool.close()

    async def test_reader_writer_smoke(self) -> None:
        with TemporaryDirectory() as directory:
            database = str(Path(directory) / "reader-writer.db")
            pool = TursoPool(database, "isolated-test-token")
            async with pool.acquire() as connection:
                await connection.execute("create table probe(value integer)")

            # Alternate independently acquired reader/writer connections. The
            # reader closes without a commit before every writer acquires.
            for value in range(10):
                async with pool.acquire() as reader:
                    await reader.fetchval("select count(*) from probe")
                async with pool.acquire() as writer:
                    await writer.execute("insert into probe values($1)", value)
            async with pool.acquire() as connection:
                self.assertEqual(await connection.fetchval("select count(*) from probe"), 10)
            await pool.close()

    async def test_rebaseline_operation_plans_keep_statements_and_drop_read_commits(self) -> None:
        # The plans mirror the current coordinator audit.  They exercise the
        # adapter's real libSQL calls without reproducing cognitive services.
        ordinary_raw = CountingRawConnection()
        ordinary = TursoConnection(ordinary_raw)
        ordinary_statements, ordinary_commits = await _run_plan(
            ordinary, ordinary_raw,
            standalone_reads=9,
            standalone_writes=3,
            transaction_statement_counts=[1, 3, 1, 1, 3],
        )
        self.assertEqual(ordinary_statements, 26)  # 21 application statements + 5 BEGIN
        self.assertEqual(ordinary_commits, 8)
        self.assertEqual(ordinary_commits + 9, 17)  # prior read-autocommit total
        ordinary_raw.close()

        rich_raw = CountingRawConnection()
        rich = TursoConnection(rich_raw)
        rich_statements, rich_commits = await _run_plan(
            rich, rich_raw,
            standalone_reads=14,
            standalone_writes=8,
            transaction_statement_counts=[1, 6, 2, 2, 2, 2, 1, 3, 4, 5, 4, 7],
        )
        self.assertEqual(rich_statements, 73)  # 61 application statements + 12 BEGIN
        self.assertEqual(rich_commits, 20)
        self.assertEqual(rich_commits + 14, 34)  # prior read-autocommit total
        rich_raw.close()

    async def test_transaction_is_active_immediately_on_entry(self) -> None:
        self.assertFalse(self.pool.raw.in_transaction)
        async with self.pool.connection.transaction():
            self.assertTrue(self.pool.raw.in_transaction)
            self.assertEqual(await self.pool.connection.fetchval("select count(*) from values_table"), 0)
            self.assertTrue(self.pool.raw.in_transaction)
        self.assertFalse(self.pool.raw.in_transaction)
        self.assertEqual(self.pool.raw.commit_calls, 1)

    async def test_outer_failure_rolls_back_its_write(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "outer failure"):
            async with self.pool.connection.transaction():
                await self.pool.connection.execute("insert into values_table values($1)", 1)
                raise RuntimeError("outer failure")

        self.assertEqual(await self.pool.connection.fetchval("select count(*) from values_table"), 0)
        self.assertFalse(self.pool.raw.in_transaction)

    async def test_nested_success_is_rolled_back_by_outer_failure(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "outer failure"):
            async with self.pool.connection.transaction():
                await self.pool.connection.execute("insert into values_table values($1)", 1)
                async with self.pool.connection.transaction():
                    await self.pool.connection.execute("insert into values_table values($1)", 2)
                self.assertEqual(self.pool.raw.commit_calls, 0)
                raise RuntimeError("outer failure")

        self.assertEqual(await self.pool.connection.fetch("select value from values_table order by value"), [])
        self.assertFalse(self.pool.raw.in_transaction)

    async def test_caught_inner_failure_rolls_back_only_inner_savepoint(self) -> None:
        async with self.pool.connection.transaction():
            await self.pool.connection.execute("insert into values_table values($1)", 1)
            with self.assertRaisesRegex(RuntimeError, "inner failure"):
                async with self.pool.connection.transaction():
                    await self.pool.connection.execute("insert into values_table values($1)", 2)
                    raise RuntimeError("inner failure")
            await self.pool.connection.execute("insert into values_table values($1)", 3)

        rows = await self.pool.connection.fetch("select value from values_table order by value")
        self.assertEqual([row["value"] for row in rows], [1, 3])
        self.assertFalse(self.pool.raw.in_transaction)

    async def test_transaction_commit_failure_rolls_back_and_preserves_commit_error(self) -> None:
        self.pool.raw.fail_next_commit = True
        with self.assertRaisesRegex(RuntimeError, "injected commit failure"):
            async with self.pool.connection.transaction():
                await self.pool.connection.execute("insert into values_table values($1)", 1)

        self.assertEqual(self.pool.raw.rollback_calls, 1)
        self.assertFalse(self.pool.raw.in_transaction)
        self.assertEqual(await self.pool.connection.fetchval("select count(*) from values_table"), 0)

    async def test_rollback_failure_does_not_replace_application_error(self) -> None:
        self.pool.raw.fail_next_rollback = True
        with self.assertRaisesRegex(ValueError, "application failure") as raised:
            async with self.pool.connection.transaction():
                await self.pool.connection.execute("insert into values_table values($1)", 1)
                raise ValueError("application failure")

        self.assertTrue(any("rollback" in note for note in getattr(raised.exception, "__notes__", [])))
        with self.assertRaisesRegex(RuntimeError, "unusable transaction state"):
            await self.pool.connection.fetchval("select count(*) from values_table")
        # Test cleanup only: the injected rollback failure leaves the local
        # driver dirty, so explicitly restore it before the fixture is reused.
        self.pool.raw.rollback()

    async def test_body_cancellation_waits_for_worker_then_rolls_back(self) -> None:
        raw = BlockingRawConnection()
        connection = TursoConnection(raw)
        await connection.execute("create table probe(value integer)")
        raw.commit_calls = raw.rollback_calls = 0
        raw.block_next_insert = True

        async def write_in_transaction() -> None:
            async with connection.transaction():
                await connection.execute("insert into probe values($1)", 1)

        task = asyncio.create_task(write_in_transaction())
        self.assertTrue(await asyncio.to_thread(raw.insert_started.wait, 1))
        task.cancel()
        raw.release_insert.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(await asyncio.to_thread(raw.insert_finished.wait, 1))

        self.assertEqual(raw.rollback_calls, 1)
        self.assertFalse(raw.in_transaction)
        self.assertEqual(await connection.fetchval("select count(*) from probe"), 0)
        raw.close()

    async def test_dml_returning_is_drained_inside_explicit_transaction(self) -> None:
        async with self.pool.connection.transaction():
            inserted = await self.pool.connection.fetchval(
                "insert into values_table values($1) returning value", 7
            )
            self.assertEqual(inserted, 7)
            self.assertTrue(self.pool.raw.in_transaction)
            self.assertEqual(self.pool.raw.commit_calls, 0)

        self.assertEqual(self.pool.raw.commit_calls, 1)
        self.assertEqual(await self.pool.connection.fetchval("select value from values_table"), 7)

    async def test_transaction_write_visibility_changes_only_after_commit(self) -> None:
        with TemporaryDirectory() as directory:
            database = str(Path(directory) / "visibility.db")
            writer_raw = CountingRawConnection(database)
            observer_raw = CountingRawConnection(database)
            writer = TursoConnection(writer_raw)
            observer = TursoConnection(observer_raw)
            await writer.execute("create table probe(value integer)")

            async with writer.transaction():
                await writer.execute("insert into probe values($1)", 9)
                self.assertEqual(await observer.fetchval("select count(*) from probe"), 0)
            self.assertEqual(await observer.fetchval("select count(*) from probe"), 1)
            writer_raw.close()
            observer_raw.close()
