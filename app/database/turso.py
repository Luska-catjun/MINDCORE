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
        cursor = await asyncio.to_thread(self._connection.execute, sql, bound)
        execute_ms = (perf_counter() - execute_started) * 1000
        return cursor, execute_ms, self._transaction_depth == 0 and self._connection.in_transaction

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
            await asyncio.to_thread(self._connection.commit)
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
        await self._commit_and_log(
            operation="execute", execute_ms=execute_ms,
            should_commit=should_commit, started=started,
        )
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
        names = [item[0] for item in cursor.description or []]
        source_tables = _source_tables(statement)
        rows = cursor.fetchall()
        # DML with RETURNING keeps its statement active until its result rows
        # are consumed.  Commit after materialization, while pure reads keep
        # ``should_commit`` false and therefore issue no commit at all.
        deferred = self._deferred_execution
        self._deferred_execution = None
        if deferred is not None:
            execute_ms, should_commit, started = deferred
            await self._commit_and_log(
                operation="fetch", execute_ms=execute_ms,
                should_commit=should_commit, started=started,
            )
        return [dict(zip(names, (_read_value(name, value, source_tables=source_tables) for name, value in zip(names, row)))) for row in rows]

    async def fetchrow(self, statement: str, *args: Any) -> dict[str, Any] | None:
        rows = await self.fetch(statement, *args)
        return rows[0] if rows else None

    async def fetchval(self, statement: str, *args: Any) -> Any:
        row = await self.fetchrow(statement, *args)
        return next(iter(row.values())) if row else None

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        self._transaction_depth += 1
        try:
            yield
        except Exception:
            rollback_started = perf_counter()
            await asyncio.to_thread(self._connection.rollback)
            logger.debug(
                "DB_LATENCY operation=rollback rollback_ms=%.2f transaction_depth=%s",
                (perf_counter() - rollback_started) * 1000, self._transaction_depth,
            )
            raise
        else:
            commit_started = perf_counter()
            await asyncio.to_thread(self._connection.commit)
            logger.debug(
                "DB_LATENCY operation=transaction_commit commit_ms=%.2f transaction_depth=%s",
                (perf_counter() - commit_started) * 1000, self._transaction_depth,
            )
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
