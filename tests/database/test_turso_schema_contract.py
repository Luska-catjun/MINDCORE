from __future__ import annotations

from contextlib import asynccontextmanager, redirect_stdout
import io
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import libsql
from fastapi.testclient import TestClient

from app.config import Settings
from app.database.migrations import SchemaMigrationError
from app.database.schema_contract import (
    CURRENT_TURSO_BASELINE_VERSION,
    SchemaState,
    classify_turso_schema,
)
from app.database.turso import TursoConnection
from app.desktop_backend import _setup_action
from app.main import create_app
from scripts.bootstrap_turso import bootstrap
from scripts.verify_schema_invariants import verify


ROOT = Path(__file__).resolve().parents[2]
BASELINE_SQL = (ROOT / "db" / "turso" / "baseline_v1.sql").read_text(encoding="utf-8")
V020_BASELINE_OBJECT = "v0.2.0:db/turso/baseline_v1.sql"
V020_BASELINE_SHA256 = "9572b4ce3d028739e356385c089aafebd1920a3066a18b455a5d83348476afb5"


def released_v020_baseline_sql() -> str:
    """Read the released v0.2.0 baseline rather than synthesizing a v21 DB."""
    result = subprocess.run(
        ["git", "show", V020_BASELINE_OBJECT],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise unittest.SkipTest("released v0.2.0 fixture is unavailable in this shallow checkout")
    import hashlib

    sql = result.stdout
    if hashlib.sha256(sql.encode("utf-8")).hexdigest() != V020_BASELINE_SHA256:
        raise AssertionError("released v0.2.0 baseline fixture checksum changed")
    return sql


class LocalPool:
    isolated = True

    def __init__(self) -> None:
        self.connection = TursoConnection(libsql.connect(":memory:"))
        self.closed = False

    @asynccontextmanager
    async def acquire(self):
        yield self.connection

    async def close(self) -> None:
        self.connection._connection.close()
        self.closed = True


async def apply_sql(connection: TursoConnection, sql: str) -> None:
    async with connection.transaction():
        for statement in sql.split(";"):
            if statement.strip():
                await connection.execute(statement)


def unversioned_released_v21_sql() -> str:
    sql = BASELINE_SQL.replace(
        "INSERT INTO schema_metadata(key,value) VALUES ('turso_baseline_version','23');",
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
    ).replace(
        "-- OBJECT index idx_chat_turn_stages_recovery\n"
        "CREATE INDEX idx_chat_turn_stages_recovery ON chat_turn_stages (status, retry_policy, attempt_count);\n",
        "",
        1,
    )
    return sql.replace(
        "CREATE TABLE schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);\n"
        "INSERT INTO schema_metadata(key,value) VALUES ('turso_baseline_version','21');\n",
        "",
        1,
    )


class TursoSchemaContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.pool = LocalPool()

    async def asyncTearDown(self) -> None:
        if not self.pool.closed:
            await self.pool.close()

    async def test_current_baseline_is_current_and_read_only(self) -> None:
        await apply_sql(self.pool.connection, BASELINE_SQL)
        before = await self.pool.connection.fetchval("select total_changes()")

        report = await classify_turso_schema(self.pool.connection)

        after = await self.pool.connection.fetchval("select total_changes()")
        self.assertEqual(report.state, SchemaState.CURRENT)
        self.assertEqual(report.version, CURRENT_TURSO_BASELINE_VERSION)
        self.assertEqual(report.missing_tables, ())
        self.assertEqual(report.missing_columns, ())
        self.assertEqual(report.missing_constraints, ())
        self.assertEqual(report.invariant_errors, ())
        self.assertEqual(after, before)
        output = io.StringIO()
        with redirect_stdout(output):
            await verify(self.pool.connection)
        self.assertIn("SCHEMA_STATE=CURRENT", output.getvalue())
        self.assertIn("SCHEMA_INVARIANTS_OK", output.getvalue())

    async def test_fake_five_table_schema_is_not_initialized_by_desktop(self) -> None:
        for table in (
            "conversations",
            "messages",
            "episodes",
            "diana_state",
            "diana_working_memory_items",
        ):
            await self.pool.connection.execute(f'create table "{table}" (placeholder text)')

        with TemporaryDirectory() as directory:
            config = Path(directory) / "mindcore.env"
            config.write_text("DATABASE_BACKEND=turso\n", encoding="utf-8")
            with (
                patch("app.desktop_backend._settings_from", return_value=object()),
                patch("app.database.connection.create_pool", return_value=self.pool),
                patch("app.database.connection.close_pool", return_value=None),
            ):
                self.assertEqual(await _setup_action("classify", str(config)), "PARTIAL_OR_UNKNOWN")
                # Desktop delegates all mutation policy to the migration
                # authority. An arbitrary partial schema remains rejected
                # there, rather than by an early desktop-only rule.
                with self.assertRaisesRegex(SchemaMigrationError, "database_schema_incompatible"):
                    await _setup_action("initialize", str(config))

        self.assertFalse(self.pool.closed)
        tables = {
            str(row["name"])
            for row in await self.pool.connection.fetch(
                "select name from sqlite_master where type='table' and name not like 'sqlite_%'"
            )
        }
        self.assertEqual(
            tables,
            {
                "conversations",
                "messages",
                "episodes",
                "diana_state",
                "diana_working_memory_items",
            },
        )

    async def test_missing_required_column_is_reported(self) -> None:
        await apply_sql(self.pool.connection, BASELINE_SQL)
        await self.pool.connection.execute("alter table decision_log drop column resolved_at")

        report = await classify_turso_schema(self.pool.connection)

        self.assertEqual(report.state, SchemaState.PARTIAL_OR_UNKNOWN)
        self.assertIn("decision_log.resolved_at", report.missing_columns)

    async def test_missing_required_unique_constraint_is_reported(self) -> None:
        sql = BASELINE_SQL.replace(
            'UNIQUE ("conversation_id", "sequence"), FOREIGN KEY ("conversation_id")',
            'FOREIGN KEY ("conversation_id")',
            1,
        )
        self.assertNotEqual(sql, BASELINE_SQL)
        await apply_sql(self.pool.connection, sql)

        report = await classify_turso_schema(self.pool.connection)

        self.assertEqual(report.state, SchemaState.PARTIAL_OR_UNKNOWN)
        self.assertIn("messages UNIQUE(conversation_id,sequence)", report.missing_constraints)

    async def test_missing_required_read_index_is_reported(self) -> None:
        await apply_sql(self.pool.connection, BASELINE_SQL)
        await self.pool.connection.execute("drop index idx_wm_conversation_active")

        report = await classify_turso_schema(self.pool.connection)

        self.assertEqual(report.state, SchemaState.PARTIAL_OR_UNKNOWN)
        self.assertIn(
            "diana_working_memory_items INDEX(conversation_id,status,salience)",
            report.missing_constraints,
        )

    async def test_partial_migration_is_rejected_without_mutation(self) -> None:
        await self.pool.connection.execute("create table schema_metadata (key text primary key, value text not null)")
        await self.pool.connection.execute(
            "insert into schema_metadata(key,value) values ('turso_baseline_version','21')"
        )
        await self.pool.connection.execute(
            "create table conversations (conversation_id text primary key, source_device text not null, started_at text not null, ended_at text)"
        )
        before = await self.pool.connection.fetchval("select total_changes()")

        with self.assertRaisesRegex(RuntimeError, "PARTIAL_OR_UNKNOWN"):
            await bootstrap(self.pool.connection)

        after = await self.pool.connection.fetchval("select total_changes()")
        self.assertEqual(after, before)
        self.assertIsNotNone(
            await self.pool.connection.fetchrow(
                "select name from sqlite_master where type='table' and name='conversations'"
            )
        )

    async def test_fresh_bootstrap_and_repeat_initialize(self) -> None:
        self.assertEqual(await bootstrap(self.pool.connection), "TURSO_BOOTSTRAP_OK version=23")
        await self.pool.connection.execute(
            "insert into conversations(conversation_id,source_device,started_at,ended_at) "
            "values ('preserved-user-row','desktop','2026-09-06T00:00:00Z',null)"
        )
        self.assertEqual(await bootstrap(self.pool.connection), "TURSO_BOOTSTRAP_ALREADY_INITIALIZED")
        report = await classify_turso_schema(self.pool.connection)
        self.assertEqual(report.state, SchemaState.CURRENT)
        self.assertEqual(
            await self.pool.connection.fetchval(
                "select count(*) from conversations where conversation_id='preserved-user-row'"
            ),
            1,
        )

    async def test_desktop_fresh_initialize_and_repeat_are_safe(self) -> None:
        with TemporaryDirectory() as directory:
            config = Path(directory) / "mindcore.env"
            config.write_text("DATABASE_BACKEND=turso\n", encoding="utf-8")
            with (
                patch("app.desktop_backend._settings_from", return_value=object()),
                patch("app.database.connection.create_pool", return_value=self.pool),
                patch("app.database.connection.close_pool", return_value=None),
            ):
                self.assertEqual(await _setup_action("classify", str(config)), "EMPTY")
                self.assertEqual(await _setup_action("initialize", str(config)), "BOOTSTRAPPED")
                self.assertEqual(await _setup_action("classify", str(config)), "INITIALIZED")
                self.assertEqual(await _setup_action("initialize", str(config)), "INITIALIZED")

    async def test_desktop_initialize_adopts_exact_unversioned_v21(self) -> None:
        await apply_sql(self.pool.connection, unversioned_released_v21_sql())
        with TemporaryDirectory() as directory:
            config = Path(directory) / "mindcore.env"
            config.write_text("DATABASE_BACKEND=turso\n", encoding="utf-8")
            with (
                patch("app.desktop_backend._settings_from", return_value=object()),
                patch("app.database.connection.create_pool", return_value=self.pool),
                patch("app.database.connection.close_pool", return_value=None),
            ):
                self.assertEqual(await _setup_action("classify", str(config)), "PARTIAL_OR_UNKNOWN")
                self.assertEqual(await _setup_action("initialize", str(config)), "INITIALIZED")

        report = await classify_turso_schema(self.pool.connection)
        self.assertEqual(report.state, SchemaState.CURRENT)

    async def test_desktop_initialize_directly_upgrades_the_released_v020_versioned_baseline(self) -> None:
        await apply_sql(self.pool.connection, released_v020_baseline_sql())
        await self.pool.connection.execute(
            "insert into conversations(conversation_id,source_device,started_at,ended_at) "
            "values ($1,$2,$3,$4)",
            "v020-preserved-conversation", "desktop", "2026-09-19T00:00:00+00:00", None,
        )
        self.assertEqual(
            await self.pool.connection.fetchval(
                "select value from schema_metadata where key='turso_baseline_version'"
            ),
            "21",
        )
        with TemporaryDirectory() as directory:
            config = Path(directory) / "mindcore.env"
            config.write_text("DATABASE_BACKEND=turso\n", encoding="utf-8")
            with (
                patch("app.desktop_backend._settings_from", return_value=object()),
                patch("app.database.connection.create_pool", return_value=self.pool),
                patch("app.database.connection.close_pool", return_value=None),
            ):
                self.assertEqual(await _setup_action("classify", str(config)), "PARTIAL_OR_UNKNOWN")
                self.assertEqual(await _setup_action("initialize", str(config)), "INITIALIZED")

        report = await classify_turso_schema(self.pool.connection)
        self.assertEqual(report.state, SchemaState.CURRENT)
        self.assertEqual(
            await self.pool.connection.fetchval(
                "select count(*) from conversations where conversation_id=$1",
                "v020-preserved-conversation",
            ),
            1,
        )
        self.assertIsNotNone(
            await self.pool.connection.fetchrow(
                "select name from sqlite_master where type='table' and name='chat_turns'"
            )
        )

    async def test_current_backend_startup_upgrades_released_v020_before_runtime_hydration(self) -> None:
        await apply_sql(self.pool.connection, released_v020_baseline_sql())
        await self.pool.connection.execute(
            "insert into conversations(conversation_id,source_device,started_at,ended_at) "
            "values ($1,$2,$3,$4)",
            "v020-startup-preserved", "desktop", "2026-09-19T00:00:00+00:00", None,
        )
        with TemporaryDirectory() as directory:
            identity = Path(directory) / "identity.txt"
            identity.write_text("generic identity", encoding="utf-8")
            settings = Settings(
                database_backend="turso",
                database_url="file::memory:",
                database_auth_token="test-token",
                persona_identity_path=str(identity),
            )
            async def create_local_pool(_settings: Settings) -> LocalPool:
                return self.pool

            async def do_not_close(_pool: LocalPool) -> None:
                return None

            with (
                patch("app.main.create_pool", create_local_pool),
                patch("app.main.close_pool", do_not_close),
            ):
                app = create_app(settings_override=settings)
                with TestClient(app) as client:
                    self.assertEqual(client.get("/health").json(), {"status": "ok", "db": "connected"})

        self.assertEqual(
            await self.pool.connection.fetchval(
                "select value from schema_metadata where key='turso_baseline_version'"
            ),
            "23",
        )
        self.assertEqual(
            await self.pool.connection.fetchval(
                "select count(*) from conversations where conversation_id=$1", "v020-startup-preserved"
            ),
            1,
        )

    async def test_desktop_database_preflight_remains_read_only(self) -> None:
        await self.pool.connection.execute("create table preflight_probe (id text primary key)")
        with TemporaryDirectory() as directory:
            config = Path(directory) / "mindcore.env"
            config.write_text("DATABASE_BACKEND=turso\n", encoding="utf-8")
            with (
                patch("app.desktop_backend._settings_from", return_value=object()),
                patch("app.database.connection.create_pool", return_value=self.pool),
                patch("app.database.connection.close_pool", return_value=None),
            ):
                self.assertEqual(await _setup_action("database", str(config)), "DATABASE_CONNECTED")

        self.assertIsNone(
            await self.pool.connection.fetchrow(
                "select name from sqlite_master where type='table' and name='schema_migration_ledger'"
            )
        )
        self.assertIsNotNone(
            await self.pool.connection.fetchrow(
                "select name from sqlite_master where type='table' and name='preflight_probe'"
            )
        )

    async def test_complete_versionless_schema_is_compatible_legacy(self) -> None:
        legacy_sql = BASELINE_SQL.replace(
            "CREATE TABLE schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);\n"
            "INSERT INTO schema_metadata(key,value) VALUES ('turso_baseline_version','23');\n",
            "",
            1,
        )
        self.assertNotEqual(legacy_sql, BASELINE_SQL)
        await apply_sql(self.pool.connection, legacy_sql)

        report = await classify_turso_schema(self.pool.connection)

        self.assertEqual(report.state, SchemaState.COMPATIBLE_LEGACY)
        self.assertEqual(await bootstrap(self.pool.connection), "TURSO_BOOTSTRAP_COMPATIBLE_LEGACY")

    async def test_unknown_schema_version_is_rejected(self) -> None:
        await apply_sql(self.pool.connection, BASELINE_SQL)
        await self.pool.connection.execute(
            "update schema_metadata set value='999' where key='turso_baseline_version'"
        )

        report = await classify_turso_schema(self.pool.connection)

        self.assertEqual(report.state, SchemaState.PARTIAL_OR_UNKNOWN)
        self.assertEqual(report.version_issue, "unsupported:999")

    async def test_verifier_uses_the_same_contract(self) -> None:
        for table in (
            "conversations",
            "messages",
            "episodes",
            "diana_state",
            "diana_working_memory_items",
        ):
            await self.pool.connection.execute(f'create table "{table}" (placeholder text)')

        with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            await verify(self.pool.connection)

    async def test_foreign_key_corruption_is_rejected(self) -> None:
        await apply_sql(self.pool.connection, BASELINE_SQL)
        raw = self.pool.connection._connection
        raw.execute("pragma foreign_keys=off")
        raw.execute(
            "insert into messages(id,conversation_id,sequence,role,content,source_device,created_at) "
            "values ('orphan','missing-conversation',1,'user','test','test','2026-09-06T00:00:00Z')"
        )
        raw.commit()
        raw.execute("pragma foreign_keys=on")

        report = await classify_turso_schema(self.pool.connection)

        self.assertEqual(report.state, SchemaState.PARTIAL_OR_UNKNOWN)
        self.assertIn("foreign_key_check", report.invariant_errors)


if __name__ == "__main__":
    unittest.main()
