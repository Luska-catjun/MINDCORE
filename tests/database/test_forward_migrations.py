from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import libsql

from app.database.migrations import (
    LEDGER_TABLE,
    MigrationDefinition,
    MigrationRegistry,
    MigrationRegistryError,
    SchemaMigrationDriftError,
    SchemaMigrationError,
    UnsupportedSchemaVersionError,
    ensure_turso_schema_current,
    migration_checksum,
    run_forward_migrations,
)
from app.database.schema_contract import (
    CURRENT_TURSO_BASELINE_VERSION,
    SCHEMA_VERSION_KEY,
    SchemaState,
    classify_turso_schema,
)
from app.database.turso import TursoConnection


ROOT = Path(__file__).resolve().parents[2]
BASELINE_SQL = (ROOT / "db" / "turso" / "baseline_v1.sql").read_text(encoding="utf-8")


def released_v21_sql() -> str:
    sql = BASELINE_SQL.replace(
        "INSERT INTO schema_metadata(key,value) VALUES ('turso_baseline_version','22');",
        "INSERT INTO schema_metadata(key,value) VALUES ('turso_baseline_version','21');",
        1,
    )
    start = sql.index("-- OBJECT table chat_turns (schema 22)")
    end = sql.index("-- OBJECT table preference_evidence", start)
    sql = sql[:start] + sql[end:]
    sql = sql.replace(
        "-- OBJECT index idx_chat_turns_recovery\n"
        "CREATE INDEX idx_chat_turns_recovery ON chat_turns (status, updated_at);\n",
        "",
        1,
    )
    sql = sql.replace(
        "-- OBJECT index idx_chat_turn_stages_recovery\n"
        "CREATE INDEX idx_chat_turn_stages_recovery ON chat_turn_stages (status, retry_policy, attempt_count);\n",
        "",
        1,
    )
    return sql


async def execute_script(connection: TursoConnection, sql: str) -> None:
    async with connection.transaction():
        for statement in sql.split(";"):
            if statement.strip():
                await connection.execute(statement)


async def make_versioned_schema(connection: TursoConnection, version: int = 1) -> None:
    await connection.execute("create table schema_metadata (key text primary key, value text not null)")
    await connection.execute(
        "insert into schema_metadata(key,value) values ($1,$2)",
        SCHEMA_VERSION_KEY,
        str(version),
    )


def migration(
    migration_id: str,
    from_version: int,
    to_version: int,
    statement: str,
    *,
    fail: bool = False,
    calls: list[str] | None = None,
) -> MigrationDefinition:
    async def apply(connection: TursoConnection) -> None:
        if calls is not None:
            calls.append(migration_id)
        await connection.execute(statement)
        if fail:
            raise RuntimeError("intentional migration failure")

    definition = f"{migration_id}\n{statement}"
    return MigrationDefinition(
        migration_id,
        from_version,
        to_version,
        definition,
        apply,
    )


class ForwardMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.raw = libsql.connect(":memory:")
        self.connection = TursoConnection(self.raw)

    async def asyncTearDown(self) -> None:
        self.raw.close()

    async def test_registry_rejects_duplicate_gap_and_backward_definitions(self) -> None:
        self.assertEqual(MigrationRegistry().migrations, ())
        first = migration("m1", 1, 2, "create table m1(id integer)")
        duplicate_id = migration("m1", 2, 3, "create table m2(id integer)")
        duplicate_start = migration("other-m1", 1, 2, "create table duplicate_start(id integer)")
        gap = migration("m3", 3, 4, "create table m3(id integer)")
        backward = migration("m4", 4, 3, "create table m4(id integer)")

        with self.assertRaisesRegex(MigrationRegistryError, "duplicate_id"):
            MigrationRegistry((first, duplicate_id))
        with self.assertRaisesRegex(MigrationRegistryError, "duplicate_version"):
            MigrationRegistry((first, duplicate_start))
        with self.assertRaisesRegex(MigrationRegistryError, "version_gap"):
            MigrationRegistry((first, gap))
        with self.assertRaisesRegex(MigrationRegistryError, "not_forward"):
            MigrationRegistry((backward,))

    async def test_registered_steps_apply_once_and_rerun_is_a_noop(self) -> None:
        await make_versioned_schema(self.connection)
        calls: list[str] = []
        first = migration("m1", 1, 2, "create table marker_one(id integer primary key)", calls=calls)
        second = migration("m2", 2, 3, "create table marker_two(id integer primary key)", calls=calls)
        registry = MigrationRegistry((first, second))

        first_result = await run_forward_migrations(
            self.connection, target_version=3, registry=registry
        )
        second_result = await run_forward_migrations(
            self.connection, target_version=3, registry=registry
        )

        self.assertEqual(first_result.applied_migration_ids, ("m1", "m2"))
        self.assertEqual(second_result.applied_migration_ids, ())
        self.assertEqual(calls, ["m1", "m2"])
        self.assertEqual(
            await self.connection.fetchval(
                "select value from schema_metadata where key=$1", SCHEMA_VERSION_KEY
            ),
            "3",
        )
        self.assertEqual(await self.connection.fetchval(f"select count(*) from {LEDGER_TABLE}"), 3)

    async def test_failure_rolls_back_ddl_ledger_and_version_then_is_retryable(self) -> None:
        await make_versioned_schema(self.connection)
        failing = migration(
            "m1", 1, 2, "create table rolled_back_marker(id integer primary key)", fail=True
        )
        registry = MigrationRegistry((failing,))

        with self.assertRaisesRegex(SchemaMigrationError, "migration_failed"):
            await run_forward_migrations(self.connection, target_version=2, registry=registry)

        self.assertIsNone(
            await self.connection.fetchrow(
                "select name from sqlite_master where type='table' and name='rolled_back_marker'"
            )
        )
        self.assertEqual(
            await self.connection.fetchval(
                "select value from schema_metadata where key=$1", SCHEMA_VERSION_KEY
            ),
            "1",
        )
        self.assertEqual(await self.connection.fetchval(f"select count(*) from {LEDGER_TABLE}"), 1)

        retry = migration("m1", 1, 2, "create table rolled_back_marker(id integer primary key)")
        result = await run_forward_migrations(
            self.connection, target_version=2, registry=MigrationRegistry((retry,))
        )
        self.assertEqual(result.applied_migration_ids, ("m1",))
        self.assertIsNotNone(
            await self.connection.fetchrow(
                "select name from sqlite_master where type='table' and name='rolled_back_marker'"
            )
        )

    async def test_drift_and_unknown_or_future_versions_fail_without_repair(self) -> None:
        await make_versioned_schema(self.connection, version=3)
        registry = MigrationRegistry(())
        with self.assertRaises(UnsupportedSchemaVersionError):
            await run_forward_migrations(self.connection, target_version=2, registry=registry)

        await self.connection.execute(
            f"""create table {LEDGER_TABLE} (
                migration_id text primary key, from_version integer not null,
                to_version integer not null, checksum text not null, applied_at text not null
            )"""
        )
        await self.connection.execute(
            f"insert into {LEDGER_TABLE}(migration_id,from_version,to_version,checksum,applied_at) "
            "values ($1,$2,$3,$4,$5)",
            "baseline_v3", 3, 3, "wrong", "2026-01-01T00:00:00+00:00",
        )
        with self.assertRaises(SchemaMigrationDriftError):
            await run_forward_migrations(self.connection, target_version=3, registry=registry)

    async def test_malformed_metadata_refuses_without_creating_a_ledger(self) -> None:
        await self.connection.execute("create table schema_metadata (key text primary key, value text not null)")
        await self.connection.execute(
            "insert into schema_metadata(key,value) values ($1,$2)", SCHEMA_VERSION_KEY, "not-a-version"
        )

        with self.assertRaisesRegex(SchemaMigrationError, "version_invalid"):
            await run_forward_migrations(
                self.connection, target_version=2, registry=MigrationRegistry()
            )

        self.assertIsNone(
            await self.connection.fetchrow(
                f"select name from sqlite_master where type='table' and name='{LEDGER_TABLE}'"
            )
        )

    async def test_ledger_with_a_missing_completed_step_is_rejected_as_drift(self) -> None:
        await make_versioned_schema(self.connection, version=3)
        first = migration("m1", 1, 2, "create table first_step(id integer)")
        second = migration("m2", 2, 3, "create table second_step(id integer)")
        registry = MigrationRegistry((first, second))
        await self.connection.execute(
            f"""create table {LEDGER_TABLE} (
                migration_id text primary key, from_version integer not null,
                to_version integer not null, checksum text not null, applied_at text not null
            )"""
        )
        await self.connection.execute(
            f"insert into {LEDGER_TABLE}(migration_id,from_version,to_version,checksum,applied_at) "
            "values ($1,$2,$3,$4,$5)",
            "baseline_v1", 1, 1, migration_checksum("MindCore released schema baseline v1"),
            "2026-01-01T00:00:00+00:00",
        )
        await self.connection.execute(
            f"insert into {LEDGER_TABLE}(migration_id,from_version,to_version,checksum,applied_at) "
            "values ($1,$2,$3,$4,$5)",
            second.migration_id, second.from_version, second.to_version, second.checksum,
            "2026-01-01T00:00:00+00:00",
        )

        with self.assertRaisesRegex(SchemaMigrationDriftError, "noncontiguous"):
            await run_forward_migrations(self.connection, target_version=3, registry=registry)

    async def test_changed_registered_definition_is_rejected_after_it_was_applied(self) -> None:
        await make_versioned_schema(self.connection)
        applied = migration("m1", 1, 2, "create table original_step(id integer)")
        await run_forward_migrations(
            self.connection, target_version=2, registry=MigrationRegistry((applied,))
        )
        changed = migration("m1", 1, 2, "create table changed_step(id integer)")

        with self.assertRaisesRegex(SchemaMigrationDriftError, "ledger_drift"):
            await run_forward_migrations(
                self.connection, target_version=2, registry=MigrationRegistry((changed,))
            )

    async def test_empty_bootstrap_records_baseline_ledger_and_validates_contract(self) -> None:
        result = await ensure_turso_schema_current(self.connection, baseline_sql=BASELINE_SQL)

        self.assertTrue(result.bootstrapped)
        self.assertEqual(result.final_version, int(CURRENT_TURSO_BASELINE_VERSION))
        report = await classify_turso_schema(self.connection)
        self.assertEqual(report.state, SchemaState.CURRENT)
        self.assertEqual(
            await self.connection.fetchval(
                f"select migration_id from {LEDGER_TABLE} order by migration_id"
            ),
            "baseline_v22",
        )

    async def test_current_schema_adopts_missing_ledger_without_replaying_user_rows(self) -> None:
        await execute_script(self.connection, BASELINE_SQL)
        await self.connection.execute(
            "insert into conversations(conversation_id,source_device,started_at,ended_at) "
            "values ($1,$2,$3,$4)",
            "durable-row", "desktop", "2026-09-10T00:00:00+00:00", None,
        )

        result = await ensure_turso_schema_current(self.connection, baseline_sql=BASELINE_SQL)

        self.assertFalse(result.bootstrapped)
        self.assertFalse(result.adopted_legacy)
        self.assertEqual(result.applied_migration_ids, ())
        self.assertEqual(
            await self.connection.fetchval(
                "select count(*) from conversations where conversation_id=$1", "durable-row"
            ),
            1,
        )
        self.assertEqual(await self.connection.fetchval(f"select count(*) from {LEDGER_TABLE}"), 1)

    async def test_compatible_legacy_schema_is_adopted_once_without_historical_replay(self) -> None:
        legacy_sql = BASELINE_SQL.replace(
            "CREATE TABLE schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);\n"
            "INSERT INTO schema_metadata(key,value) VALUES ('turso_baseline_version','22');\n",
            "",
            1,
        )
        await execute_script(self.connection, legacy_sql)
        await self.connection.execute(
            "insert into conversations(conversation_id,source_device,started_at,ended_at) "
            "values ($1,$2,$3,$4)",
            "legacy-row", "desktop", "2026-09-10T00:00:00+00:00", None,
        )

        result = await ensure_turso_schema_current(self.connection, baseline_sql=BASELINE_SQL)
        rerun = await ensure_turso_schema_current(self.connection, baseline_sql=BASELINE_SQL)

        self.assertTrue(result.adopted_legacy)
        self.assertFalse(rerun.adopted_legacy)
        self.assertEqual(await self.connection.fetchval(f"select count(*) from {LEDGER_TABLE}"), 1)
        self.assertEqual(
            await self.connection.fetchval(
                "select count(*) from conversations where conversation_id=$1", "legacy-row"
            ),
            1,
        )

    async def test_released_v21_upgrades_to_v22_once_and_preserves_rows(self) -> None:
        await execute_script(self.connection, released_v21_sql())
        await self.connection.execute(
            "insert into conversations(conversation_id,source_device,started_at,ended_at) "
            "values ($1,$2,$3,$4)",
            "preserved-v21-row", "desktop", "2026-09-12T00:00:00+00:00", None,
        )

        result = await ensure_turso_schema_current(self.connection, baseline_sql=BASELINE_SQL)
        rerun = await ensure_turso_schema_current(self.connection, baseline_sql=BASELINE_SQL)

        self.assertEqual(result.applied_migration_ids, ("022_turn_durability",))
        self.assertEqual(rerun.applied_migration_ids, ())
        self.assertEqual((result.initial_version, result.final_version), (21, 22))
        self.assertEqual(
            await self.connection.fetchval(
                "select count(*) from conversations where conversation_id=$1",
                "preserved-v21-row",
            ),
            1,
        )
        self.assertIsNotNone(
            await self.connection.fetchrow(
                "select name from sqlite_master where type='table' and name='chat_turns'"
            )
        )
        self.assertEqual(await self.connection.fetchval(f"select count(*) from {LEDGER_TABLE}"), 2)

    async def test_v22_fresh_and_migrated_turn_schema_are_equivalent(self) -> None:
        await execute_script(self.connection, released_v21_sql())
        await ensure_turso_schema_current(self.connection, baseline_sql=BASELINE_SQL)
        fresh_raw = libsql.connect(":memory:")
        fresh = TursoConnection(fresh_raw)
        try:
            await execute_script(fresh, BASELINE_SQL)
            for table in ("chat_turns", "chat_turn_stages"):
                with self.subTest(table=table):
                    migrated_columns = [
                        dict(row) for row in await self.connection.fetch(f"pragma table_info({table})")
                    ]
                    fresh_columns = [
                        dict(row) for row in await fresh.fetch(f"pragma table_info({table})")
                    ]
                    migrated_foreign_keys = [
                        dict(row)
                        for row in await self.connection.fetch(f"pragma foreign_key_list({table})")
                    ]
                    fresh_foreign_keys = [
                        dict(row) for row in await fresh.fetch(f"pragma foreign_key_list({table})")
                    ]
                    migrated_indexes = [
                        dict(row) for row in await self.connection.fetch(f"pragma index_list({table})")
                    ]
                    fresh_indexes = [
                        dict(row) for row in await fresh.fetch(f"pragma index_list({table})")
                    ]
                    self.assertEqual(migrated_columns, fresh_columns)
                    self.assertEqual(migrated_foreign_keys, fresh_foreign_keys)
                    self.assertEqual(migrated_indexes, fresh_indexes)
        finally:
            fresh_raw.close()

    async def test_file_backed_second_runner_observes_durable_ledger(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "migration.db"
            first_raw = libsql.connect(str(database))
            first = TursoConnection(first_raw)
            try:
                await make_versioned_schema(first)
                calls: list[str] = []
                step = migration("m1", 1, 2, "create table durable_marker(id integer primary key)", calls=calls)
                registry = MigrationRegistry((step,))
                await run_forward_migrations(first, target_version=2, registry=registry)
            finally:
                first_raw.close()

            second_raw = libsql.connect(str(database))
            second = TursoConnection(second_raw)
            try:
                result = await run_forward_migrations(second, target_version=2, registry=registry)
                self.assertEqual(result.applied_migration_ids, ())
                self.assertEqual(calls, ["m1"])
                self.assertEqual(await second.fetchval(f"select count(*) from {LEDGER_TABLE}"), 2)
            finally:
                second_raw.close()
