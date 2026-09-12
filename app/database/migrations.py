"""Forward-only schema migration support for local libSQL and remote Turso.

The fresh baseline remains the authoritative way to create an empty database.
This module deliberately starts the durable upgrade chain at that baseline;
historical ``db/migrations/001..021`` files are not replayed for users whose
database already represents the released baseline.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sys
from typing import Any

from app.database.schema_contract import (
    CURRENT_TURSO_BASELINE_VERSION,
    SCHEMA_VERSION_KEY,
    SchemaState,
    classify_turso_schema,
)


LEDGER_TABLE = "schema_migration_ledger"
MigrationApply = Callable[[Any], Awaitable[None]]


class SchemaMigrationError(RuntimeError):
    """A safe, metadata-only database schema migration failure."""


class MigrationRegistryError(SchemaMigrationError):
    pass


class UnsupportedSchemaVersionError(SchemaMigrationError):
    pass


class SchemaMigrationDriftError(SchemaMigrationError):
    pass


class SchemaMigrationConcurrencyError(SchemaMigrationError):
    pass


def migration_checksum(definition: str) -> str:
    """Return the stable checksum recorded for an immutable migration definition."""
    return hashlib.sha256(definition.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MigrationDefinition:
    migration_id: str
    from_version: int
    to_version: int
    definition: str
    apply: MigrationApply

    @property
    def checksum(self) -> str:
        """Hash the immutable migration definition recorded in the ledger."""
        return migration_checksum(self.definition)


@dataclass(frozen=True)
class MigrationRunResult:
    initial_version: int
    final_version: int
    applied_migration_ids: tuple[str, ...]
    adopted_legacy: bool = False
    bootstrapped: bool = False


class MigrationRegistry:
    """An explicit, deterministic sequence of future forward migrations."""

    def __init__(self, migrations: Iterable[MigrationDefinition] = ()) -> None:
        self._migrations = tuple(migrations)
        self._by_id = {migration.migration_id: migration for migration in self._migrations}
        self._by_from = {migration.from_version: migration for migration in self._migrations}
        self.validate()

    @property
    def migrations(self) -> tuple[MigrationDefinition, ...]:
        return self._migrations

    def validate(self) -> None:
        ids: set[str] = set()
        starts: set[int] = set()
        ends: set[int] = set()
        ordered = sorted(self._migrations, key=lambda migration: migration.from_version)
        for migration in ordered:
            if not migration.migration_id or migration.migration_id in ids:
                raise MigrationRegistryError("migration_registry_duplicate_id")
            if migration.from_version in starts or migration.to_version in ends:
                raise MigrationRegistryError("migration_registry_duplicate_version")
            if migration.to_version <= migration.from_version:
                raise MigrationRegistryError("migration_registry_not_forward")
            if migration.to_version != migration.from_version + 1:
                raise MigrationRegistryError("migration_registry_version_gap")
            if not migration.definition:
                raise MigrationRegistryError("migration_registry_invalid_definition")
            ids.add(migration.migration_id)
            starts.add(migration.from_version)
            ends.add(migration.to_version)
        for previous, following in zip(ordered, ordered[1:]):
            if following.from_version != previous.to_version:
                raise MigrationRegistryError("migration_registry_version_gap")

    def plan(self, from_version: int, target_version: int) -> tuple[MigrationDefinition, ...]:
        if from_version > target_version:
            raise UnsupportedSchemaVersionError("unsupported_newer_schema_version")
        planned: list[MigrationDefinition] = []
        version = from_version
        while version < target_version:
            migration = self._by_from.get(version)
            if migration is None:
                raise MigrationRegistryError("migration_registry_missing_forward_step")
            planned.append(migration)
            version = migration.to_version
        return tuple(planned)

    def by_id(self, migration_id: str) -> MigrationDefinition | None:
        return self._by_id.get(migration_id)


TURN_DURABILITY_V22_DEFINITION = """022_turn_durability
create table chat_turns (
  turn_id text primary key,
  conversation_id text not null references conversations(conversation_id) on delete cascade,
  user_message_id text references messages(id) on delete set null,
  assistant_message_id text references messages(id) on delete set null,
  status text not null check(status in ('pending','core_failed','core_completed','partial','complete')),
  created_at text not null,
  updated_at text not null,
  core_completed_at text,
  completed_at text,
  last_failed_stage text,
  safe_error_category text
);
create table chat_turn_stages (
  turn_id text not null references chat_turns(turn_id) on delete cascade,
  stage_name text not null,
  status text not null check(status in ('pending','running','completed','failed')),
  retry_policy text not null check(retry_policy in ('automatic','manual')),
  attempt_count integer not null default 0 check(attempt_count >= 0),
  started_at text,
  completed_at text,
  last_error_category text,
  primary key(turn_id, stage_name)
);
create index idx_chat_turns_recovery on chat_turns(status, updated_at);
create index idx_chat_turn_stages_recovery on chat_turn_stages(status, retry_policy, attempt_count);
"""


async def _apply_turn_durability_v22(connection: Any) -> None:
    for statement in TURN_DURABILITY_V22_DEFINITION.split("\n", 1)[1].split(";"):
        if statement.strip():
            await connection.execute(statement)


# Historical 001..021 SQL files remain outside automatic replay. Released
# upgrades append immutable, contiguous definitions to this registry.
FORWARD_MIGRATIONS = MigrationRegistry((
    MigrationDefinition(
        "022_turn_durability",
        21,
        22,
        TURN_DURABILITY_V22_DEFINITION,
        _apply_turn_durability_v22,
    ),
))


def _is_released_v21_turn_durability_upgrade(report: Any) -> bool:
    """Accept only the exact released v21 capability gap for migration 022."""
    return (
        report.version == "21"
        and report.version_issue == "unsupported:21"
        and set(report.missing_tables) == {"chat_turns", "chat_turn_stages"}
        and not report.missing_columns
        and not report.missing_constraints
        and not report.invariant_errors
    )


def _baseline_checksum(version: int) -> str:
    return migration_checksum(f"MindCore released schema baseline v{version}")


async def _tables(connection: Any) -> set[str]:
    rows = await connection.fetch(
        "select name from sqlite_master where type='table' and name not like 'sqlite_%'"
    )
    return {str(row["name"]) for row in rows}


async def _schema_version(connection: Any) -> int | None:
    tables = await _tables(connection)
    if "schema_metadata" not in tables:
        return None
    columns = {str(row["name"]) for row in await connection.fetch('pragma table_info("schema_metadata")')}
    if not {"key", "value"}.issubset(columns):
        raise SchemaMigrationError("database_schema_metadata_invalid")
    value = await connection.fetchval(
        "select value from schema_metadata where key=$1", SCHEMA_VERSION_KEY
    )
    if value is None:
        raise SchemaMigrationError("database_schema_version_missing")
    text = str(value)
    if not text.isdecimal():
        raise SchemaMigrationError("database_schema_version_invalid")
    return int(text)


async def _ensure_ledger_table(connection: Any) -> None:
    await connection.execute(
        f"""create table if not exists {LEDGER_TABLE} (
            migration_id text primary key,
            from_version integer not null,
            to_version integer not null,
            checksum text not null,
            applied_at text not null
        )"""
    )
    columns = {str(row["name"]) for row in await connection.fetch(f"pragma table_info({LEDGER_TABLE})")}
    if columns != {"migration_id", "from_version", "to_version", "checksum", "applied_at"}:
        raise SchemaMigrationError("database_migration_ledger_invalid")


async def _ledger_rows(connection: Any) -> list[dict[str, Any]]:
    return await connection.fetch(
        f"select migration_id, from_version, to_version, checksum, applied_at from {LEDGER_TABLE} "
        "order by to_version asc, migration_id asc"
    )


async def _validate_ledger(
    connection: Any,
    *,
    registry: MigrationRegistry,
    current_version: int,
) -> list[dict[str, Any]]:
    rows = await _ledger_rows(connection)
    if not rows:
        return rows
    highest_version = -1
    for index, row in enumerate(rows):
        migration_id = str(row["migration_id"])
        from_version = int(row["from_version"])
        to_version = int(row["to_version"])
        checksum = str(row["checksum"])
        if migration_id == f"baseline_v{to_version}":
            if (
                index != 0
                or from_version != to_version
                or checksum != _baseline_checksum(to_version)
            ):
                raise SchemaMigrationDriftError("database_migration_ledger_drift")
        else:
            definition = registry.by_id(migration_id)
            if definition is None or (
                definition.from_version != from_version
                or definition.to_version != to_version
                or definition.checksum != checksum
            ):
                raise SchemaMigrationDriftError("database_migration_ledger_drift")
            if highest_version < 0 or from_version != highest_version:
                raise SchemaMigrationDriftError("database_migration_ledger_noncontiguous")
        if to_version <= highest_version:
            raise SchemaMigrationDriftError("database_migration_ledger_noncontiguous")
        highest_version = to_version
    if highest_version != current_version:
        raise SchemaMigrationDriftError("database_migration_ledger_version_mismatch")
    return rows


async def _adopt_version_into_ledger(
    connection: Any,
    *,
    version: int,
    registry: MigrationRegistry,
) -> None:
    async with connection.transaction():
        await _ensure_ledger_table(connection)
        rows = await _ledger_rows(connection)
        if not rows:
            await connection.execute(
                f"""insert into {LEDGER_TABLE}(
                    migration_id, from_version, to_version, checksum, applied_at
                ) values ($1,$2,$3,$4,$5)""",
                f"baseline_v{version}",
                version,
                version,
                _baseline_checksum(version),
                datetime.now(timezone.utc),
            )
        await _validate_ledger(connection, registry=registry, current_version=version)


async def run_forward_migrations(
    connection: Any,
    *,
    target_version: int,
    registry: MigrationRegistry,
) -> MigrationRunResult:
    """Apply only explicitly registered future steps, with durable exactly-once evidence."""
    initial_version = await _schema_version(connection)
    if initial_version is None:
        raise SchemaMigrationError("database_schema_metadata_missing")
    plan = registry.plan(initial_version, target_version)
    await _adopt_version_into_ledger(
        connection, version=initial_version, registry=registry
    )
    applied: list[str] = []
    for migration in plan:
        applied_here = False
        try:
            async with connection.transaction():
                current_version = await _schema_version(connection)
                if current_version == migration.to_version:
                    await _validate_ledger(
                        connection, registry=registry, current_version=current_version
                    )
                    continue
                if current_version != migration.from_version:
                    raise SchemaMigrationConcurrencyError("database_schema_migration_concurrent")
                await _validate_ledger(
                    connection, registry=registry, current_version=current_version
                )
                existing = await connection.fetchrow(
                    f"select migration_id, from_version, to_version, checksum from {LEDGER_TABLE} "
                    "where migration_id=$1",
                    migration.migration_id,
                )
                if existing is not None:
                    raise SchemaMigrationDriftError("database_migration_ledger_duplicate")
                await migration.apply(connection)
                await connection.execute(
                    f"""insert into {LEDGER_TABLE}(
                        migration_id, from_version, to_version, checksum, applied_at
                    ) values ($1,$2,$3,$4,$5)""",
                    migration.migration_id,
                    migration.from_version,
                    migration.to_version,
                    migration.checksum,
                    datetime.now(timezone.utc),
                )
                updated = await connection.fetchval(
                    """update schema_metadata set value=$1
                       where key=$2 and value=$3 returning value""",
                    str(migration.to_version),
                    SCHEMA_VERSION_KEY,
                    str(migration.from_version),
                )
                if updated != str(migration.to_version):
                    raise SchemaMigrationConcurrencyError("database_schema_migration_concurrent")
                applied_here = True
        except SchemaMigrationError:
            raise
        except Exception as error:
            raise SchemaMigrationError("database_schema_migration_failed") from error
        if applied_here:
            applied.append(migration.migration_id)
    final_version = await _schema_version(connection)
    if final_version != target_version:
        raise SchemaMigrationError("database_schema_migration_incomplete")
    await _validate_ledger(connection, registry=registry, current_version=final_version)
    return MigrationRunResult(initial_version, final_version, tuple(applied))


def _baseline_path() -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return root / "db" / "turso" / "baseline_v1.sql"


async def _bootstrap_empty_database(connection: Any, baseline_sql: str) -> None:
    async with connection.transaction():
        for statement in baseline_sql.split(";"):
            if statement.strip():
                await connection.execute(statement)


async def _adopt_compatible_legacy_database(
    connection: Any,
    *,
    target_version: int,
    registry: MigrationRegistry,
) -> None:
    async with connection.transaction():
        await connection.execute(
            "create table schema_metadata (key text primary key, value text not null)"
        )
        await connection.execute(
            "insert into schema_metadata(key,value) values ($1,$2)",
            SCHEMA_VERSION_KEY,
            str(target_version),
        )
        await _ensure_ledger_table(connection)
        await connection.execute(
            f"""insert into {LEDGER_TABLE}(
                migration_id, from_version, to_version, checksum, applied_at
            ) values ($1,$2,$3,$4,$5)""",
            f"baseline_v{target_version}",
            target_version,
            target_version,
            _baseline_checksum(target_version),
            datetime.now(timezone.utc),
        )
        await _validate_ledger(connection, registry=registry, current_version=target_version)


async def ensure_turso_schema_current(
    connection: Any,
    *,
    registry: MigrationRegistry = FORWARD_MIGRATIONS,
    target_version: int = int(CURRENT_TURSO_BASELINE_VERSION),
    baseline_sql: str | None = None,
) -> MigrationRunResult:
    """Bootstrap empty databases, adopt released legacy DBs, or run future steps.

    This is intentionally a mutating startup/initialize operation. Connection
    preflight must continue to use ``SELECT 1`` only.
    """
    report = await classify_turso_schema(connection)
    bootstrapped = False
    adopted_legacy = False
    if report.state == SchemaState.EMPTY:
        await _bootstrap_empty_database(
            connection,
            baseline_sql if baseline_sql is not None else _baseline_path().read_text(encoding="utf-8"),
        )
        bootstrapped = True
        report = await classify_turso_schema(connection)
    if report.state == SchemaState.COMPATIBLE_LEGACY:
        await _adopt_compatible_legacy_database(
            connection, target_version=target_version, registry=registry
        )
        adopted_legacy = True
    elif report.state == SchemaState.PARTIAL_OR_UNKNOWN:
        version = await _schema_version(connection)
        if version is None:
            raise SchemaMigrationError("database_schema_incompatible")
        if version > target_version:
            raise UnsupportedSchemaVersionError("unsupported_newer_schema_version")
        if version == target_version:
            raise SchemaMigrationError("database_schema_incompatible")
        if version == 21 and target_version == 22:
            if not _is_released_v21_turn_durability_upgrade(report):
                raise SchemaMigrationError("PARTIAL_OR_UNKNOWN database_schema_incompatible")
        else:
            raise SchemaMigrationError("PARTIAL_OR_UNKNOWN database_schema_incompatible")
        # A registered forward chain may repair an older supported release;
        # an absent path is rejected before any mutation is attempted.
        registry.plan(version, target_version)
    result = await run_forward_migrations(
        connection, target_version=target_version, registry=registry
    )
    final_report = await classify_turso_schema(connection)
    if final_report.state != SchemaState.CURRENT:
        raise SchemaMigrationError("database_schema_migration_validation_failed")
    return MigrationRunResult(
        result.initial_version,
        result.final_version,
        result.applied_migration_ids,
        adopted_legacy=adopted_legacy,
        bootstrapped=bootstrapped,
    )
