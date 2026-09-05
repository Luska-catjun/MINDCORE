#!/usr/bin/env python3
"""Local-only connection lifecycle comparison for libSQL.

This isolates connect/query/close timing.  It is intentionally not a remote
Turso benchmark and does not implement a reusable connection pool.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from time import perf_counter

import libsql


ITERATIONS = 100


def _run_query(connection) -> float:
    started = perf_counter()
    cursor = connection.execute("select count(*) from probe")
    cursor.fetchall()
    return (perf_counter() - started) * 1000


def _connect(path: str):
    started = perf_counter()
    connection = libsql.connect(path)
    return connection, (perf_counter() - started) * 1000


def _close(connection) -> float:
    started = perf_counter()
    connection.close()
    return (perf_counter() - started) * 1000


def main() -> None:
    descriptor, path = tempfile.mkstemp(prefix="diana-lifecycle-", suffix=".db")
    os.close(descriptor)
    try:
        setup, _ = _connect(path)
        setup.execute("create table probe(value integer)")
        setup.close()

        new_connect = new_query = new_close = 0.0
        for _ in range(ITERATIONS):
            connection, elapsed = _connect(path)
            new_connect += elapsed
            new_query += _run_query(connection)
            new_close += _close(connection)

        reused, reuse_connect = _connect(path)
        reuse_query = sum(_run_query(reused) for _ in range(ITERATIONS))
        reuse_close = _close(reused)

        print(
            "LOCAL_LIBSQL_CONNECTION_LIFECYCLE "
            f"iterations={ITERATIONS} "
            f"new_connect_total_ms={new_connect:.2f} "
            f"new_query_total_ms={new_query:.2f} "
            f"new_close_total_ms={new_close:.2f} "
            f"new_overall_ms={new_connect + new_query + new_close:.2f} "
            f"reused_connect_total_ms={reuse_connect:.2f} "
            f"reused_query_total_ms={reuse_query:.2f} "
            f"reused_close_total_ms={reuse_close:.2f} "
            f"reused_overall_ms={reuse_connect + reuse_query + reuse_close:.2f}"
        )
        print("LOCAL_ONLY_NOT_REMOTE_TURSO_LATENCY")
    finally:
        Path(path).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
