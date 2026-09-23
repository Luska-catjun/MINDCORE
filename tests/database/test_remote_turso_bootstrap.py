from __future__ import annotations

import os
from pathlib import Path
from time import perf_counter
import unittest

from app.database.migrations import ensure_turso_schema_current
from app.database.schema_contract import (
    CURRENT_TURSO_BASELINE_VERSION,
    SCHEMA_VERSION_KEY,
    SchemaState,
    classify_turso_schema,
)
from app.database.turso import TursoPool


ROOT = Path(__file__).resolve().parents[2]
BASELINE_SQL = (ROOT / "db" / "turso" / "baseline_v1.sql").read_text(encoding="utf-8")
TEST_URL = os.environ.get("MINDCORE_TEST_TURSO_URL")
TEST_TOKEN = os.environ.get("MINDCORE_TEST_TURSO_TOKEN")


@unittest.skipUnless(
    TEST_URL and TEST_TOKEN,
    "REMOTE_TURSO_TEST_SKIPPED = NO_TEST_CREDENTIALS",
)
class RemoteFreshTursoBootstrapTests(unittest.IsolatedAsyncioTestCase):
    async def test_disposable_remote_database_bootstraps_atomically_and_is_idempotent(self) -> None:
        pool = TursoPool(TEST_URL or "", TEST_TOKEN or "")
        try:
            async with pool.acquire() as connection:
                self.assertEqual(await connection.fetchval("SELECT 1"), 1)
                before = await classify_turso_schema(connection)
                self.assertEqual(
                    before.state,
                    SchemaState.EMPTY,
                    "Dedicated remote bootstrap fixture must be a completely empty disposable DB.",
                )
                started = perf_counter()
                result = await ensure_turso_schema_current(
                    connection,
                    baseline_sql=BASELINE_SQL,
                )
                duration_ms = (perf_counter() - started) * 1000
                self.assertTrue(result.bootstrapped)
                self.assertEqual((await classify_turso_schema(connection)).state, SchemaState.CURRENT)
                self.assertEqual(
                    await connection.fetchval(
                        "select value from schema_metadata where key=$1",
                        SCHEMA_VERSION_KEY,
                    ),
                    CURRENT_TURSO_BASELINE_VERSION,
                )
                rerun = await ensure_turso_schema_current(
                    connection,
                    baseline_sql=BASELINE_SQL,
                )
                self.assertFalse(rerun.bootstrapped)
                self.assertEqual(rerun.applied_migration_ids, ())
                print(f"REMOTE_TURSO_BOOTSTRAP_OK duration_ms={duration_ms:.2f}")
        finally:
            await pool.close()
