from __future__ import annotations

import asyncio
from pathlib import Path
from time import perf_counter
import unittest
from unittest.mock import AsyncMock, patch

import libsql

from app.database.migrations import (
    SchemaMigrationDriftError,
    SchemaMigrationError,
    ensure_turso_schema_current,
    _baseline_checksum,
    validate_turso_schema_full,
)
from app.database.schema_contract import SchemaState, classify_turso_schema, inspect_current_turso_schema
from app.database.turso import TursoConnection


ROOT = Path(__file__).resolve().parents[2]
BASELINE_SQL = (ROOT / "db" / "turso" / "baseline_v1.sql").read_text(encoding="utf-8")


class CountingConnection:
    def __init__(self, connection: TursoConnection, *, latency: float = 0.0) -> None:
        self.connection = connection
        self.latency = latency
        self.read_round_trips = 0
        self.execute_round_trips = 0
        self.statements: list[str] = []

    async def _wait(self) -> None:
        if self.latency:
            await asyncio.sleep(self.latency)

    async def fetch(self, statement: str, *args):
        self.read_round_trips += 1
        self.statements.append(statement.lower())
        await self._wait()
        return await self.connection.fetch(statement, *args)

    async def fetchval(self, statement: str, *args):
        self.read_round_trips += 1
        self.statements.append(statement.lower())
        await self._wait()
        return await self.connection.fetchval(statement, *args)

    async def fetchrow(self, statement: str, *args):
        self.read_round_trips += 1
        self.statements.append(statement.lower())
        await self._wait()
        return await self.connection.fetchrow(statement, *args)

    async def execute(self, statement: str, *args):
        self.execute_round_trips += 1
        return await self.connection.execute(statement, *args)

    def transaction(self):
        return self.connection.transaction()


class CurrentSchemaStartupFastPathTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.raw = libsql.connect(":memory:")
        self.connection = TursoConnection(self.raw)
        await ensure_turso_schema_current(self.connection, baseline_sql=BASELINE_SQL)

    async def asyncTearDown(self) -> None:
        self.raw.close()

    async def test_current_restart_uses_one_snapshot_and_no_full_classifier(self) -> None:
        counted = CountingConnection(self.connection)
        classifier = AsyncMock(side_effect=AssertionError("full classifier must not run"))
        with patch("app.database.migrations.classify_turso_schema", classifier):
            result = await ensure_turso_schema_current(counted, baseline_sql=BASELINE_SQL)

        self.assertEqual((result.initial_version, result.final_version), (24, 24))
        self.assertEqual(counted.read_round_trips, 1)
        self.assertEqual(counted.execute_round_trips, 0)
        joined = " ".join(counted.statements)
        self.assertNotIn("pragma_integrity_check", joined)
        self.assertNotIn("pragma_foreign_key_check", joined)
        classifier.assert_not_awaited()

    async def test_full_validator_still_checks_integrity_and_foreign_keys(self) -> None:
        counted = CountingConnection(self.connection)
        # The full validation is exercised after writes/bootstrap, and its
        # call shape retains both expensive data-integrity checks.
        await validate_turso_schema_full(counted)
        joined = " ".join(counted.statements)
        self.assertIn("pragma foreign_key_check", joined)
        self.assertIn("pragma integrity_check", joined)

    async def test_full_classifier_batches_metadata_round_trips(self) -> None:
        counted = CountingConnection(self.connection)
        report = await classify_turso_schema(counted)
        self.assertEqual(report.state.value, "CURRENT")
        self.assertLessEqual(counted.read_round_trips, 7)

    async def test_simulated_remote_latency_is_paid_once_on_current_restart(self) -> None:
        counted = CountingConnection(self.connection, latency=0.01)
        started = perf_counter()
        await ensure_turso_schema_current(counted, baseline_sql=BASELINE_SQL)
        elapsed = perf_counter() - started

        self.assertEqual(counted.read_round_trips, 1)
        self.assertGreaterEqual(elapsed, 0.01)
        self.assertLess(elapsed, 0.25)

    async def test_structural_drift_falls_back_and_is_rejected(self) -> None:
        await self.connection.execute("alter table decision_log drop column resolved_at")
        with self.assertRaisesRegex(SchemaMigrationError, "schema_incompatible"):
            await ensure_turso_schema_current(self.connection, baseline_sql=BASELINE_SQL)

    async def test_ledger_checksum_drift_is_rejected_by_fast_path(self) -> None:
        await self.connection.execute(
            "update schema_migration_ledger set checksum='drifted'"
        )
        with self.assertRaisesRegex(SchemaMigrationDriftError, "ledger_drift"):
            await ensure_turso_schema_current(self.connection, baseline_sql=BASELINE_SQL)

    async def test_current_startup_skips_data_scan_but_full_validation_catches_fk_violation(self) -> None:
        await self.connection.execute("pragma foreign_keys=off")
        await self.connection.execute(
            "insert into messages(id,conversation_id,sequence,role,content,source_device,created_at) "
            "values($1,$2,$3,$4,$5,$6,$7)",
            "orphan", "missing-conversation", 1, "user", "test", "test",
            "2026-09-23T00:00:00+00:00",
        )
        await self.connection.execute("pragma foreign_keys=on")

        counted = CountingConnection(self.connection)
        result = await ensure_turso_schema_current(counted, baseline_sql=BASELINE_SQL)
        self.assertEqual((result.initial_version, result.final_version), (24, 24))
        self.assertFalse(any("pragma foreign_key_check" in query for query in counted.statements))
        with self.assertRaisesRegex(SchemaMigrationError, "validation_failed"):
            await validate_turso_schema_full(self.connection)

    async def test_current_turn_context_structural_drift_falls_back_and_is_rejected(self) -> None:
        without_combination_check = BASELINE_SQL.replace(
            "  input_source TEXT NOT NULL DEFAULT 'text' CHECK(\n"
            "    input_source IN ('text','internal') AND (\n"
            "      (initiator_actor='user' AND trigger_type='user_message' AND input_source='text') OR\n"
            "      (initiator_actor='persona' AND trigger_type='autonomy_decision' AND input_source='internal') OR\n"
            "      (initiator_actor='system' AND trigger_type='system_event' AND input_source='internal')\n"
            "    )\n"
            "  )",
            "  input_source TEXT NOT NULL DEFAULT 'text'",
            1,
        )
        without_input_source = BASELINE_SQL.replace(
            "  trigger_type TEXT NOT NULL DEFAULT 'user_message' CHECK(trigger_type IN ('user_message','autonomy_decision','system_event')),\n"
            "  input_source TEXT NOT NULL DEFAULT 'text' CHECK(\n"
            "    input_source IN ('text','internal') AND (\n"
            "      (initiator_actor='user' AND trigger_type='user_message' AND input_source='text') OR\n"
            "      (initiator_actor='persona' AND trigger_type='autonomy_decision' AND input_source='internal') OR\n"
            "      (initiator_actor='system' AND trigger_type='system_event' AND input_source='internal')\n"
            "    )\n"
            "  )",
            "  trigger_type TEXT NOT NULL DEFAULT 'user_message' CHECK(trigger_type IN ('user_message','autonomy_decision','system_event'))",
            1,
        )

        for drift_name, baseline, expected_detail in (
            (
                "combination_check",
                without_combination_check,
                "chat_turns CHECK(turn_context_combination)",
            ),
            ("input_source_column", without_input_source, "chat_turns.input_source"),
        ):
            with self.subTest(drift=drift_name):
                self.assertNotEqual(baseline, BASELINE_SQL)
                raw = libsql.connect(":memory:")
                connection = TursoConnection(raw)
                try:
                    async with connection.transaction():
                        for statement in baseline.split(";"):
                            if statement.strip():
                                await connection.execute(statement)
                        await connection.execute(
                            """create table schema_migration_ledger (
                                migration_id text primary key,
                                from_version integer not null,
                                to_version integer not null,
                                checksum text not null,
                                applied_at text not null
                            )"""
                        )
                        await connection.execute(
                            """insert into schema_migration_ledger(
                                migration_id,from_version,to_version,checksum,applied_at
                            ) values($1,$2,$3,$4,$5)""",
                            "baseline_v24", 24, 24, _baseline_checksum(24), "test",
                        )

                    counted = CountingConnection(connection)
                    with self.assertRaisesRegex(SchemaMigrationError, "schema_incompatible"):
                        await ensure_turso_schema_current(counted, baseline_sql=BASELINE_SQL)
                    self.assertGreater(counted.read_round_trips, 1)
                    self.assertEqual(counted.execute_round_trips, 0)
                    report = await classify_turso_schema(connection)
                    detail = " ".join((*report.missing_columns, *report.missing_constraints))
                    self.assertIn(expected_detail, detail)
                finally:
                    raw.close()

    async def test_current_autonomy_predicate_drift_is_rejected_in_single_snapshot(self) -> None:
        await self.connection.execute("drop index uq_autonomy_execution_active_dedupe")
        await self.connection.execute(
            """create unique index uq_autonomy_execution_active_dedupe
               on autonomy_executions(persona_id,intention_key,user_activity_anchor_message_id)
               where status='COMPLETE'"""
        )
        counted = CountingConnection(self.connection)
        snapshot = await inspect_current_turso_schema(counted)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.report.state, SchemaState.PARTIAL_OR_UNKNOWN)
        self.assertTrue(any("PREDICATE" in item for item in snapshot.report.missing_constraints))
        self.assertEqual(counted.read_round_trips, 1)
        counted.read_round_trips = 0
        with self.assertRaisesRegex(SchemaMigrationError, "schema_incompatible"):
            await ensure_turso_schema_current(counted, baseline_sql=BASELINE_SQL)
        self.assertGreater(counted.read_round_trips, 1)
        self.assertEqual(counted.execute_round_trips, 0)


if __name__ == "__main__":
    unittest.main()
