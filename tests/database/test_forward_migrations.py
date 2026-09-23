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
    SchemaBootstrapError,
    SchemaMigrationDriftError,
    SchemaMigrationError,
    UnsupportedSchemaVersionError,
    _bootstrap_empty_database,
    ensure_turso_schema_current,
    migration_checksum,
    _apply_turn_context_v23,
    TURN_CONTEXT_V23_DEFINITION,
    AUTONOMY_EXECUTION_V24_DEFINITION,
    _apply_autonomy_execution_v24,
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


def released_v22_sql() -> str:
    sql = released_v23_sql().replace(
        "INSERT INTO schema_metadata(key,value) VALUES ('turso_baseline_version','23');",
        "INSERT INTO schema_metadata(key,value) VALUES ('turso_baseline_version','22');",
        1,
    )
    sql = sql.replace(
        ",\n  initiator_actor TEXT NOT NULL DEFAULT 'user' CHECK(initiator_actor IN ('user','persona','system')),\n"
        "  trigger_type TEXT NOT NULL DEFAULT 'user_message' CHECK(trigger_type IN ('user_message','autonomy_decision','system_event')),\n"
        "  input_source TEXT NOT NULL DEFAULT 'text' CHECK(\n"
        "    input_source IN ('text','internal') AND (\n"
        "      (initiator_actor='user' AND trigger_type='user_message' AND input_source='text') OR\n"
        "      (initiator_actor='persona' AND trigger_type='autonomy_decision' AND input_source='internal') OR\n"
        "      (initiator_actor='system' AND trigger_type='system_event' AND input_source='internal')\n"
        "    )\n"
        "  )",
        "",
        1,
    )
    return sql


def released_v23_sql() -> str:
    sql = BASELINE_SQL.replace(
        "INSERT INTO schema_metadata(key,value) VALUES ('turso_baseline_version','24');",
        "INSERT INTO schema_metadata(key,value) VALUES ('turso_baseline_version','23');",
        1,
    )
    start = sql.index("-- OBJECT table autonomy_executions (schema 24)")
    end = sql.index("-- OBJECT table preference_evidence", start)
    sql = sql[:start] + sql[end:]
    for name in (
        "uq_autonomy_execution_active_dedupe",
        "idx_autonomy_executions_recovery",
        "idx_autonomy_executions_conversation",
        "idx_autonomy_executions_anchor_failures",
    ):
        start = sql.find(f"-- OBJECT index {name}\n")
        if start >= 0:
            end = sql.index(";\n", start) + 2
            sql = sql[:start] + sql[end:]
    return sql


def released_v21_sql() -> str:
    sql = released_v22_sql().replace(
        "INSERT INTO schema_metadata(key,value) VALUES ('turso_baseline_version','22');",
        "INSERT INTO schema_metadata(key,value) VALUES ('turso_baseline_version','21');",
        1,
    )
    start = sql.index("-- OBJECT table chat_turns (schema 23)")
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


def unversioned_released_v21_sql() -> str:
    return released_v21_sql().replace(
        "CREATE TABLE schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);\n"
        "INSERT INTO schema_metadata(key,value) VALUES ('turso_baseline_version','21');\n",
        "",
        1,
    )


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
        self.assertNotIn("ON DELETE SET NULL ON DELETE SET NULL", BASELINE_SQL)
        result = await ensure_turso_schema_current(self.connection, baseline_sql=BASELINE_SQL)

        self.assertTrue(result.bootstrapped)
        self.assertEqual(result.final_version, int(CURRENT_TURSO_BASELINE_VERSION))
        report = await classify_turso_schema(self.connection)
        self.assertEqual(report.state, SchemaState.CURRENT)
        self.assertEqual(
            await self.connection.fetchval(
                f"select migration_id from {LEDGER_TABLE} order by migration_id"
            ),
            "baseline_v24",
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

    async def test_fresh_bootstrap_failure_reports_safe_structure_and_rolls_back(self) -> None:
        sql = """-- OBJECT table schema_metadata
create table schema_metadata(key text primary key, value text not null);
-- OBJECT table schema_migration_ledger
create table schema_migration_ledger(migration_id text primary key);
-- OBJECT table retry_marker
create table retry_marker(id integer primary key);
"""

        class FailingConnection:
            def __init__(self, connection: TursoConnection) -> None:
                self.connection = connection
                self.execute_count = 0

            def transaction(self):
                return self.connection.transaction()

            async def execute(self, statement: str) -> None:
                self.execute_count += 1
                if self.execute_count == 2:
                    raise RuntimeError("secret-bearing driver detail must stay internal")
                await self.connection.execute(statement)

        with self.assertRaises(SchemaBootstrapError) as caught:
            await _bootstrap_empty_database(FailingConnection(self.connection), sql)

        self.assertEqual(caught.exception.statement_index, 2)
        self.assertEqual(caught.exception.object_name, "schema_migration_ledger")
        self.assertEqual(caught.exception.exception_class, "RuntimeError")
        self.assertEqual(str(caught.exception), "database_bootstrap_failed")
        self.assertIsInstance(caught.exception.__cause__, RuntimeError)
        self.assertEqual(
            await self.connection.fetch(
                "select name from sqlite_master where type='table' and name not like 'sqlite_%'"
            ),
            [],
        )

        await _bootstrap_empty_database(self.connection, sql)
        self.assertEqual(
            {row["name"] for row in await self.connection.fetch(
                "select name from sqlite_master where type='table' and name not like 'sqlite_%'"
            )},
            {"schema_metadata", "schema_migration_ledger", "retry_marker"},
        )

    async def test_compatible_legacy_schema_is_adopted_once_without_historical_replay(self) -> None:
        legacy_sql = BASELINE_SQL.replace(
            "CREATE TABLE schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);\n"
            "INSERT INTO schema_metadata(key,value) VALUES ('turso_baseline_version','24');\n",
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

    async def test_released_v21_upgrades_through_v24_once_and_preserves_rows(self) -> None:
        await execute_script(self.connection, released_v21_sql())
        await self.connection.execute(
            "insert into conversations(conversation_id,source_device,started_at,ended_at) "
            "values ($1,$2,$3,$4)",
            "preserved-v21-row", "desktop", "2026-09-12T00:00:00+00:00", None,
        )

        result = await ensure_turso_schema_current(self.connection, baseline_sql=BASELINE_SQL)
        rerun = await ensure_turso_schema_current(self.connection, baseline_sql=BASELINE_SQL)

        self.assertEqual(result.applied_migration_ids, (
            "022_turn_durability", "023_turn_context", "024_autonomy_execution_persistence"
        ))
        self.assertEqual(rerun.applied_migration_ids, ())
        self.assertEqual((result.initial_version, result.final_version), (21, 24))
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
        self.assertEqual(await self.connection.fetchval(f"select count(*) from {LEDGER_TABLE}"), 4)

    async def test_unversioned_exact_v21_is_adopted_then_upgraded_once(self) -> None:
        await execute_script(self.connection, unversioned_released_v21_sql())
        await self.connection.execute(
            "insert into conversations(conversation_id,source_device,started_at,ended_at) values($1,$2,$3,$4)",
            "legacy-conversation", "desktop", "2026-09-12T00:00:00+00:00", None,
        )
        await self.connection.execute(
            "insert into messages(id,conversation_id,sequence,role,content,source_device,created_at) values($1,$2,$3,$4,$5,$6,$7)",
            "legacy-message", "legacy-conversation", 1, "user", "preserved", "desktop", "2026-09-12T00:00:00+00:00",
        )
        await self.connection.execute(
            "insert into diana_needs(need_key,value,baseline,updated_at,last_triggered_at,metadata) values($1,$2,$3,$4,$5,$6)",
            "curiosity", 0.6, 0.5, "2026-09-12T00:00:00+00:00", None, "{}",
        )
        await self.connection.execute(
            "insert into diana_goals(id,goal_key,goal_type,summary,origin_need,priority,status,progress,confidence,conversation_id,source_type,source_id,created_at,updated_at,expires_at,metadata) values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$13,$14,$15)",
            "legacy-goal", "legacy-goal-key", "conversation", "preserved", "curiosity", 0.5,
            "active", 0.1, 0.8, "legacy-conversation", "message", "legacy-message",
            "2026-09-12T00:00:00+00:00", None, "{}",
        )
        await self.connection.execute(
            "insert into episodes(episode_id,conversation_id,sequence,summary,recall_frequency,source_device,created_at,user_message_id,episode_type,provenance,is_grounded,updated_at) values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$7)",
            "legacy-episode", "legacy-conversation", 1, "preserved", 0, "desktop",
            "2026-09-12T00:00:00+00:00", "legacy-message", "conversation", "runtime", 1,
        )
        await self.connection.execute(
            "insert into memories(memory_id,content,normalized_content,memory_type,importance,source_conversation_id,source_message_id,created_at,updated_at,recall_frequency,memory_strength,source_episode_id) values($1,$2,$3,$4,$5,$6,$7,$8,$8,$9,$10,$11)",
            "legacy-memory", "preserved", "preserved", "fact", 0.8, "legacy-conversation",
            "legacy-message", "2026-09-12T00:00:00+00:00", 0, 0.8, "legacy-episode",
        )
        await self.connection.execute(
            "insert into preferences(preference_id,owner_type,subject,value,preference_type,status,confidence,evidence_count,first_seen_at,last_seen_at,created_at,updated_at) values($1,$2,$3,$4,$5,$6,$7,$8,$9,$9,$9,$9)",
            "legacy-preference", "user", "tea", "green", "like", "active", 0.8, 1,
            "2026-09-12T00:00:00+00:00",
        )
        await self.connection.execute(
            "insert into relationship(id,familiarity,trust,affection,shared_experience,conflict_history,updated_at,conflict) values($1,$2,$3,$4,$5,$6,$7,$8)",
            1, 0.8, 0.8, 0.8, 1.0, "[]", "2026-09-12T00:00:00+00:00", 0.0,
        )
        report = await classify_turso_schema(self.connection)
        self.assertEqual(report.state, SchemaState.PARTIAL_OR_UNKNOWN)
        self.assertEqual(set(report.missing_tables), {
            "chat_turns", "chat_turn_stages", "autonomy_executions"
        })
        self.assertIsNone(await self.connection.fetchrow(
            "select name from sqlite_master where type='table' and name='schema_metadata'"
        ))

        result = await ensure_turso_schema_current(self.connection, baseline_sql=BASELINE_SQL)
        rerun = await ensure_turso_schema_current(self.connection, baseline_sql=BASELINE_SQL)

        self.assertEqual((result.initial_version, result.final_version), (21, 24))
        self.assertTrue(result.adopted_legacy)
        self.assertEqual(result.applied_migration_ids, (
            "022_turn_durability", "023_turn_context", "024_autonomy_execution_persistence"
        ))
        self.assertEqual(rerun.applied_migration_ids, ())
        self.assertEqual(await self.connection.fetchval(
            "select value from schema_metadata where key=$1", SCHEMA_VERSION_KEY
        ), "24")
        self.assertEqual(
            [row["migration_id"] for row in await self.connection.fetch(
                f"select migration_id from {LEDGER_TABLE} order by to_version, migration_id"
            )],
            ["baseline_v21", "022_turn_durability", "023_turn_context", "024_autonomy_execution_persistence"],
        )
        self.assertEqual(await self.connection.fetchval(
            "select count(*) from conversations where conversation_id=$1", "legacy-conversation"
        ), 1)
        self.assertEqual(await self.connection.fetchval(
            "select count(*) from messages where id=$1", "legacy-message"
        ), 1)
        self.assertEqual(await self.connection.fetchval(
            "select value from diana_needs where need_key='curiosity'"
        ), 0.6)
        self.assertEqual(await self.connection.fetchval(
            "select summary from diana_goals where id='legacy-goal'"
        ), "preserved")
        self.assertEqual(await self.connection.fetchval(
            "select content from memories where memory_id='legacy-memory'"
        ), "preserved")
        self.assertEqual(await self.connection.fetchval(
            "select summary from episodes where episode_id='legacy-episode'"
        ), "preserved")
        self.assertEqual(await self.connection.fetchval(
            "select value from preferences where preference_id='legacy-preference'"
        ), "green")
        self.assertEqual(await self.connection.fetchval(
            "select trust from relationship where id=1"
        ), 0.8)

    async def test_unversioned_v21_with_extra_contract_drift_is_refused_without_adoption(self) -> None:
        for damaged in ("table", "index", "constraint", "ledger"):
            with self.subTest(damaged=damaged):
                raw = libsql.connect(":memory:")
                connection = TursoConnection(raw)
                try:
                    sql = unversioned_released_v21_sql()
                    if damaged == "constraint":
                        sql = sql.replace(
                            'UNIQUE ("conversation_id", "sequence"), ', "", 1
                        )
                    await execute_script(connection, sql)
                    if damaged == "table":
                        await connection.execute("drop table diana_identity")
                    elif damaged == "index":
                        await connection.execute("drop index idx_wm_conversation_active")
                    else:
                        await connection.execute(
                            f"create table {LEDGER_TABLE}(migration_id text primary key, from_version integer, to_version integer, checksum text, applied_at text)"
                        )

                    with self.assertRaisesRegex(SchemaMigrationError, "database_schema_incompatible"):
                        await ensure_turso_schema_current(connection, baseline_sql=BASELINE_SQL)
                    self.assertIsNone(await connection.fetchrow(
                        "select name from sqlite_master where type='table' and name='schema_metadata'"
                    ))
                finally:
                    raw.close()

    async def test_unversioned_v21_adoption_failure_leaves_retryable_versioned_v21(self) -> None:
        await execute_script(self.connection, unversioned_released_v21_sql())
        failing = migration("022_turn_durability", 21, 22, "create table failure_marker(id integer)", fail=True)
        with self.assertRaisesRegex(SchemaMigrationError, "migration_failed"):
            await ensure_turso_schema_current(
                self.connection, baseline_sql=BASELINE_SQL, target_version=22,
                registry=MigrationRegistry((failing,))
            )

        self.assertEqual(await self.connection.fetchval(
            "select value from schema_metadata where key=$1", SCHEMA_VERSION_KEY
        ), "21")
        self.assertEqual(await self.connection.fetchval(
            f"select count(*) from {LEDGER_TABLE} where migration_id='baseline_v21'"
        ), 1)
        self.assertIsNone(await self.connection.fetchrow(
            "select name from sqlite_master where type='table' and name='chat_turns'"
        ))
        retry = await ensure_turso_schema_current(self.connection, baseline_sql=BASELINE_SQL)
        self.assertEqual(retry.applied_migration_ids, (
            "022_turn_durability", "023_turn_context", "024_autonomy_execution_persistence"
        ))

    async def test_v22_fresh_and_migrated_turn_schema_are_equivalent(self) -> None:
        await execute_script(self.connection, released_v22_sql())
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

    async def test_released_v22_backfills_turn_context_once_and_preserves_turn_rows(self) -> None:
        await execute_script(self.connection, released_v22_sql())
        await self.connection.execute(
            "insert into conversations(conversation_id,source_device,started_at,ended_at) values($1,$2,$3,$4)",
            "legacy-turn-conversation", "desktop", "2026-09-12T00:00:00+00:00", None,
        )
        await self.connection.execute(
            "insert into messages(id,conversation_id,sequence,role,content,source_device,created_at) values($1,$2,$3,$4,$5,$6,$7)",
            "legacy-user", "legacy-turn-conversation", 1, "user", "preserved", "desktop", "2026-09-12T00:00:00+00:00",
        )
        await self.connection.execute(
            "insert into chat_turns(turn_id,conversation_id,user_message_id,assistant_message_id,status,created_at,updated_at,core_completed_at,completed_at,last_failed_stage,safe_error_category) values($1,$2,$3,$4,$5,$6,$6,null,null,null,null)",
            "legacy-turn", "legacy-turn-conversation", "legacy-user", None, "pending", "2026-09-12T00:00:00+00:00",
        )

        result = await ensure_turso_schema_current(self.connection, baseline_sql=BASELINE_SQL)
        rerun = await ensure_turso_schema_current(self.connection, baseline_sql=BASELINE_SQL)
        row = await self.connection.fetchrow("select * from chat_turns where turn_id=$1", "legacy-turn")

        self.assertEqual(result.applied_migration_ids, (
            "023_turn_context", "024_autonomy_execution_persistence"
        ))
        self.assertEqual(rerun.applied_migration_ids, ())
        self.assertEqual((row["user_message_id"], row["assistant_message_id"]), ("legacy-user", None))
        self.assertEqual(
            (row["initiator_actor"], row["trigger_type"], row["input_source"]),
            ("user", "user_message", "text"),
        )

    async def test_turn_context_migration_failure_rolls_back_columns_ledger_and_version(self) -> None:
        await execute_script(self.connection, released_v22_sql())

        async def fail_after_turn_context(connection: TursoConnection) -> None:
            await _apply_turn_context_v23(connection)
            raise RuntimeError("intentional turn context failure")

        failing = MigrationDefinition(
            "023_turn_context", 22, 23, TURN_CONTEXT_V23_DEFINITION, fail_after_turn_context
        )
        with self.assertRaisesRegex(SchemaMigrationError, "migration_failed"):
            await run_forward_migrations(
                self.connection, target_version=23, registry=MigrationRegistry((failing,))
            )

        columns = {row["name"] for row in await self.connection.fetch("pragma table_info(chat_turns)")}
        self.assertFalse({"initiator_actor", "trigger_type", "input_source"} & columns)
        self.assertEqual(
            await self.connection.fetchval(
                "select value from schema_metadata where key=$1", SCHEMA_VERSION_KEY
            ),
            "22",
        )

    async def test_released_v23_adds_autonomy_execution_authority_once(self) -> None:
        await execute_script(self.connection, released_v23_sql())
        await self.connection.execute(
            "insert into conversations(conversation_id,source_device,started_at,ended_at) values($1,$2,$3,$4)",
            "v23-conversation", "desktop", "2026-09-12T00:00:00Z", None,
        )
        result = await ensure_turso_schema_current(self.connection, baseline_sql=BASELINE_SQL)
        rerun = await ensure_turso_schema_current(self.connection, baseline_sql=BASELINE_SQL)
        self.assertEqual((result.initial_version, result.final_version), (23, 24))
        self.assertEqual(result.applied_migration_ids, ("024_autonomy_execution_persistence",))
        self.assertEqual(rerun.applied_migration_ids, ())
        self.assertEqual(await self.connection.fetchval(
            "select count(*) from conversations where conversation_id='v23-conversation'"
        ), 1)
        self.assertIsNotNone(await self.connection.fetchrow(
            "select name from sqlite_master where type='table' and name='autonomy_executions'"
        ))

    async def test_autonomy_execution_migration_failure_rolls_back_and_retries(self) -> None:
        await execute_script(self.connection, released_v23_sql())

        async def fail_after_autonomy(connection: TursoConnection) -> None:
            await _apply_autonomy_execution_v24(connection)
            raise RuntimeError("intentional autonomy migration failure")

        failing = MigrationDefinition(
            "024_autonomy_execution_persistence", 23, 24,
            AUTONOMY_EXECUTION_V24_DEFINITION, fail_after_autonomy,
        )
        with self.assertRaisesRegex(SchemaMigrationError, "migration_failed"):
            await run_forward_migrations(
                self.connection, target_version=24, registry=MigrationRegistry((failing,))
            )
        self.assertEqual(await self.connection.fetchval(
            "select value from schema_metadata where key=$1", SCHEMA_VERSION_KEY
        ), "23")
        self.assertIsNone(await self.connection.fetchrow(
            "select name from sqlite_master where type='table' and name='autonomy_executions'"
        ))
        retry = await ensure_turso_schema_current(self.connection, baseline_sql=BASELINE_SQL)
        self.assertEqual(retry.applied_migration_ids, ("024_autonomy_execution_persistence",))

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
