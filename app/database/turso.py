"""Async-shaped libSQL primitives used during the Turso runtime cutover.

The application currently speaks asyncpg.  This module is intentionally kept
behind DATABASE_BACKEND so Supabase remains a rollback path while PostgreSQL
queries are migrated endpoint by endpoint.
"""
from __future__ import annotations

import asyncio
import re
import json
import logging
from datetime import date, datetime
from contextlib import asynccontextmanager
from time import perf_counter
from typing import Any, AsyncIterator
from uuid import UUID

import libsql

from app.database.normalization import normalize_column_value


logger = logging.getLogger("diana.database.turso")


_PARAMETER = re.compile(r"\$(\d+)")
_TABLE_REFERENCE = re.compile(r"\b(?:from|join)\s+[\"`]?([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE)


async def _run_blocking(function: Any, *args: Any) -> Any:
    """Let a driver call finish before propagating task cancellation.

    ``asyncio.to_thread`` does not stop its worker when the awaiting task is
    cancelled. Waiting for the worker here prevents rollback or connection
    close from racing an operation that is still using the raw connection.
    """
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancellation:
        try:
            await task
        except BaseException as operation_error:
            cancellation.add_note(
                f"The cancelled database operation also failed with {type(operation_error).__name__}."
            )
        raise


def _bind(statement: str, args: tuple[Any, ...]) -> tuple[str, tuple[Any, ...]]:
    """Convert PostgreSQL $n placeholders while preserving quoted SQL text."""
    output: list[str] = []; bound: list[Any] = []; index = 0; quoted = False
    while index < len(statement):
        char = statement[index]
        if char == "'":
            output.append(char)
            if quoted and index + 1 < len(statement) and statement[index + 1] == "'": output.append("'"); index += 2; continue
            quoted = not quoted; index += 1; continue
        match = _PARAMETER.match(statement, index) if not quoted else None
        if match:
            position = int(match.group(1)) - 1
            if position < 0 or position >= len(args): raise ValueError(f"SQL placeholder {match.group(0)} has no argument")
            output.append("?"); bound.append(_value(args[position])); index = match.end(); continue
        output.append(char); index += 1
    return "".join(output).replace("::jsonb", "").replace("::uuid[]", ""), tuple(bound)

def _value(value: Any) -> Any:
    if isinstance(value, UUID): return str(value)
    if isinstance(value, (datetime, date)): return value.isoformat()
    if isinstance(value, (dict, list, tuple)): return json.dumps(value, default=str)
    return value


def _source_tables(statement: str) -> frozenset[str]:
    """Return unambiguous table context available from a SELECT statement."""
    return frozenset(match.group(1).casefold() for match in _TABLE_REFERENCE.finditer(statement))


def _read_value(name: str, value: Any, *, source_tables: frozenset[str]) -> Any:
    """Apply the Turso result contract for UUID, JSON, and UTC timestamp columns."""
    value = normalize_column_value(name, value, source_tables=source_tables)
    if not isinstance(value, str):
        return value
    if name == "id" or name.endswith("_id"):
        try:
            return UUID(value)
        except ValueError:
            return value
    return value


class TursoConnection:
    """Small asyncpg-shaped libSQL adapter.

    Standalone mutations commit outside explicit transactions.  A native
    libSQL ``in_transaction`` check, rather than SQL text inspection or the
    public method name, distinguishes a pending mutation from a read (because
    callers legitimately use ``fetchrow`` with ``... RETURNING``). ``fetch*``
    return plain dictionaries with explicit JSON columns decoded and timestamp
    columns as aware UTC datetimes. ``transaction`` commits on success and
    rolls back on an exception. SQL uses PostgreSQL positional placeholders
    via ``_bind``.
    """
    def __init__(self, connection: libsql.Connection) -> None:
        self._connection = connection
        self._transaction_depth = 0
        self._savepoint_counter = 0
        self._transaction_broken = False
        self._defer_auto_commit = False
        self._deferred_execution: tuple[float, bool, float] | None = None

    async def _run(self, statement: str, args: tuple[Any, ...]) -> tuple[Any, float, bool]:
        """Run one statement and report whether a standalone commit is due.

        libSQL keeps INSERT/UPDATE/DELETE work pending until commit, while a
        SELECT does not enter a transaction.  ``in_transaction`` is supplied
        by the installed driver, so this remains correct for CTEs and
        ``... RETURNING`` without brittle SQL-prefix classification.
        """
        sql, bound = _bind(statement, args)
        execute_started = perf_counter()
        if self._transaction_broken:
            raise RuntimeError("Turso connection has an unusable transaction state.")
        cursor = await _run_blocking(self._connection.execute, sql, bound)
        execute_ms = (perf_counter() - execute_started) * 1000
        return cursor, execute_ms, self._transaction_depth == 0 and self._connection.in_transaction

    async def _close_cursor(self, cursor: Any, original: BaseException | None = None) -> None:
        """Release the native statement owner at the adapter boundary.

        libSQL cursors own native resources independently of the connection.
        In particular, retaining a completed cursor can keep a file-backed
        database open on Windows even after ``Connection.close()``.  Every
        cursor created by this adapter is therefore closed as soon as its
        result has been consumed.
        """
        close = getattr(cursor, "close", None)
        if close is None:
            return
        try:
            await _run_blocking(close)
        except BaseException as cleanup:
            if original is None:
                raise
            self._record_cleanup_failure(original, cleanup, "cursor close")

    async def _execute_control(self, statement: str) -> None:
        """Execute and immediately release a transaction-control cursor."""
        cursor = await _run_blocking(self._connection.execute, statement)
        await self._close_cursor(cursor)

    async def _commit_and_log(
        self,
        *,
        operation: str,
        execute_ms: float,
        should_commit: bool,
        started: float,
    ) -> None:
        commit_ms = 0.0
        if should_commit:
            commit_started = perf_counter()
            try:
                await _run_blocking(self._connection.commit)
            except BaseException as error:
                await self._rollback_preserving(error, operation="standalone commit rollback")
                raise
            commit_ms = (perf_counter() - commit_started) * 1000
        logger.debug(
            "DB_LATENCY operation=%s execute_ms=%.2f commit_ms=%.2f "
            "auto_commit=%s transaction_depth=%s total_ms=%.2f",
            operation, execute_ms, commit_ms, should_commit,
            self._transaction_depth, (perf_counter() - started) * 1000,
        )

    async def execute(self, statement: str, *args: Any) -> Any:
        started = perf_counter()
        cursor, execute_ms, should_commit = await self._run(statement, args)
        if self._defer_auto_commit:
            # Keep RETURNING cursors consumable.  ``fetch`` owns the delayed
            # commit and restores regular execution immediately afterwards.
            self._deferred_execution = (execute_ms, should_commit, started)
            return cursor
        try:
            await self._commit_and_log(
                operation="execute", execute_ms=execute_ms,
                should_commit=should_commit, started=started,
            )
        except BaseException as error:
            await self._close_cursor(cursor, error)
            raise
        await self._close_cursor(cursor)
        return cursor

    async def fetch(self, statement: str, *args: Any) -> list[dict[str, Any]]:
        self._deferred_execution = None
        self._defer_auto_commit = True
        try:
            # Preserve the established polymorphic seam: recorder/fault-
            # injection connections override execute(), and fetch must still
            # travel through that override.
            cursor = await self.execute(statement, *args)
        finally:
            self._defer_auto_commit = False
        try:
            names = [item[0] for item in cursor.description or []]
            source_tables = _source_tables(statement)
            rows = cursor.fetchall()
            # DML with RETURNING keeps its statement active until its result
            # rows are consumed. Commit after materialization, while pure
            # reads keep ``should_commit`` false.
            deferred = self._deferred_execution
            self._deferred_execution = None
            if deferred is not None:
                execute_ms, should_commit, started = deferred
                await self._commit_and_log(
                    operation="fetch", execute_ms=execute_ms,
                    should_commit=should_commit, started=started,
                )
        except BaseException as error:
            self._deferred_execution = None
            await self._close_cursor(cursor, error)
            raise
        await self._close_cursor(cursor)
        return [dict(zip(names, (_read_value(name, value, source_tables=source_tables) for name, value in zip(names, row)))) for row in rows]

    async def fetchrow(self, statement: str, *args: Any) -> dict[str, Any] | None:
        rows = await self.fetch(statement, *args)
        return rows[0] if rows else None

    async def fetchval(self, statement: str, *args: Any) -> Any:
        row = await self.fetchrow(statement, *args)
        return next(iter(row.values())) if row else None

    @staticmethod
    def _record_cleanup_failure(original: BaseException, cleanup: BaseException, operation: str) -> None:
        original.add_note(f"{operation} failed with {type(cleanup).__name__}.")
        logger.error(
            "DB_TRANSACTION_CLEANUP_FAILED operation=%s original_error_type=%s cleanup_error_type=%s",
            operation, type(original).__name__, type(cleanup).__name__,
        )

    async def _rollback_preserving(self, original: BaseException, *, operation: str) -> bool:
        rollback_started = perf_counter()
        try:
            await _run_blocking(self._connection.rollback)
        except BaseException as cleanup:
            self._transaction_broken = True
            self._record_cleanup_failure(original, cleanup, operation)
            return False
        logger.debug(
            "DB_LATENCY operation=rollback rollback_ms=%.2f transaction_depth=%s",
            (perf_counter() - rollback_started) * 1000, self._transaction_depth,
        )
        return True

    async def _rollback_savepoint_preserving(self, name: str, original: BaseException) -> None:
        try:
            await self._execute_control(f"ROLLBACK TO SAVEPOINT {name}")
        except BaseException as cleanup:
            self._transaction_broken = True
            self._record_cleanup_failure(original, cleanup, "savepoint rollback")
            return
        try:
            await self._execute_control(f"RELEASE SAVEPOINT {name}")
        except BaseException as cleanup:
            self._transaction_broken = True
            self._record_cleanup_failure(original, cleanup, "savepoint release after rollback")

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        """Provide explicit outer transactions and nested SAVEPOINT scopes.

        The outer context executes BEGIN on entry and owns the raw
        commit/rollback. Nested contexts never commit the connection: they use
        unique SAVEPOINT names, release on success, and roll back only their
        own work on failure. Body cancellation is treated as a failure after
        any in-flight worker finishes, so rollback cannot race that worker.
        Cleanup failures are attached to, but never replace, the first error.
        Reads outside this context retain standalone autocommit behavior.
        """
        if self._transaction_broken:
            raise RuntimeError("Turso connection has an unusable transaction state.")
        outermost = self._transaction_depth == 0
        savepoint: str | None = None
        if outermost:
            try:
                await self._execute_control("BEGIN")
            except BaseException as error:
                if self._connection.in_transaction:
                    await self._rollback_preserving(error, operation="transaction entry rollback")
                raise
            self._transaction_broken = False
        else:
            self._savepoint_counter += 1
            savepoint = f"mindcore_sp_{self._savepoint_counter}"
            try:
                await self._execute_control(f"SAVEPOINT {savepoint}")
            except BaseException as error:
                await self._rollback_savepoint_preserving(savepoint, error)
                raise
        self._transaction_depth += 1
        try:
            yield
        except BaseException as error:
            if outermost:
                await self._rollback_preserving(error, operation="transaction rollback")
            else:
                assert savepoint is not None
                await self._rollback_savepoint_preserving(savepoint, error)
            raise
        else:
            if outermost:
                if self._transaction_broken:
                    error = RuntimeError("Turso transaction cannot commit after a cleanup failure.")
                    await self._rollback_preserving(error, operation="broken transaction rollback")
                    raise error
                commit_started = perf_counter()
                try:
                    await _run_blocking(self._connection.commit)
                except BaseException as error:
                    await self._rollback_preserving(error, operation="transaction commit rollback")
                    raise
                logger.debug(
                    "DB_LATENCY operation=transaction_commit commit_ms=%.2f transaction_depth=%s",
                    (perf_counter() - commit_started) * 1000, self._transaction_depth,
                )
            else:
                assert savepoint is not None
                try:
                    await self._execute_control(f"RELEASE SAVEPOINT {savepoint}")
                except BaseException as error:
                    await self._rollback_savepoint_preserving(savepoint, error)
                    raise
        finally:
            self._transaction_depth -= 1


class TursoPool:
    def __init__(self, url: str, auth_token: str) -> None:
        self._url = url
        self._auth_token = auth_token

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[TursoConnection]:
        # A TursoConnection tracks transaction depth locally. Sharing one raw
        # libSQL connection across acquire() calls would therefore let an
        # unrelated wrapper auto-commit another request's open transaction.
        # Keep each acquire scope isolated, matching an asyncpg pool lease.
        acquire_started = perf_counter()
        raw_connection = await asyncio.to_thread(
            libsql.connect, database=self._url, auth_token=self._auth_token
        )
        logger.debug(
            "DB_LATENCY operation=connection_connect connect_ms=%.2f",
            (perf_counter() - acquire_started) * 1000,
        )
        try:
            yield TursoConnection(raw_connection)
        finally:
            close_started = perf_counter()
            await asyncio.to_thread(raw_connection.close)
            logger.debug(
                "DB_LATENCY operation=connection_close close_ms=%.2f total_ms=%.2f",
                (perf_counter() - close_started) * 1000,
                (perf_counter() - acquire_started) * 1000,
            )

    async def close(self) -> None:
        # Connections are scoped to acquire() and closed by its context.
        return None
