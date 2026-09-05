#!/usr/bin/env python3
"""Local-only read-commit comparison for the Turso adapter.

This uses :memory: libSQL only.  Its timings are not remote Turso latency.
"""
from __future__ import annotations

import asyncio
import sys
from time import perf_counter
from pathlib import Path

import libsql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database.turso import TursoConnection


class CountingConnection:
    def __init__(self) -> None:
        self.raw = libsql.connect(":memory:")
        self.commit_calls = 0

    @property
    def in_transaction(self):
        return self.raw.in_transaction

    def execute(self, *args, **kwargs):
        return self.raw.execute(*args, **kwargs)

    def commit(self):
        self.commit_calls += 1
        return self.raw.commit()

    def rollback(self):
        return self.raw.rollback()

    def close(self):
        return self.raw.close()


async def _measure(*, forced_read_commit: bool) -> tuple[float, int]:
    raw = CountingConnection()
    connection = TursoConnection(raw)
    await connection.execute("create table probe(value integer)")
    raw.commit_calls = 0
    started = perf_counter()
    for _ in range(100):
        await connection.fetchval("select count(*) from probe")
        if forced_read_commit:
            await asyncio.to_thread(raw.commit)
    elapsed_ms = (perf_counter() - started) * 1000
    commits = raw.commit_calls
    raw.close()
    return elapsed_ms, commits


async def main() -> None:
    current_ms, current_commits = await _measure(forced_read_commit=False)
    legacy_ms, legacy_commits = await _measure(forced_read_commit=True)
    print(
        "LOCAL_LIBSQL_READ_BENCHMARK selects=100 "
        f"current_commit_calls={current_commits} current_total_ms={current_ms:.2f} "
        f"legacy_forced_commit_calls={legacy_commits} legacy_total_ms={legacy_ms:.2f}"
    )
    print("LOCAL_ONLY_NOT_REMOTE_TURSO_LATENCY")


if __name__ == "__main__":
    asyncio.run(main())
