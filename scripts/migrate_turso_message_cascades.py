"""Repair Turso message-deletion FK policies without losing durable provenance.

SQLite cannot alter a column's nullability in place. This migration recreates
only tables that have an actual ``SET NULL + NOT NULL`` contradiction. That is
important for remote libSQL: rebuilding parents that still have live children
is neither necessary nor safe. Rows, inline constraints, named indexes, and
triggers are preserved. Re-running normalizes an FK action instead of appending
another ON DELETE clause.
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.database.connection import close_pool, create_pool


FK_ACTIONS: dict[str, dict[tuple[str, str, str], str]] = {
    "messages": {("conversation_id", "conversations", "conversation_id"): "CASCADE"},
    "experiences": {
        ("user_message_id", "messages", "id"): "CASCADE",
        ("assistant_message_id", "messages", "id"): "CASCADE",
    },
    "episodes": {
        ("conversation_id", "conversations", "conversation_id"): "SET NULL",
        ("user_message_id", "messages", "id"): "SET NULL",
        ("assistant_message_id", "messages", "id"): "SET NULL",
        ("experience_id", "experiences", "experience_id"): "SET NULL",
    },
    "memories": {("source_message_id", "messages", "id"): "SET NULL"},
    "diana_knowledge_facts": {("source_message_id", "messages", "id"): "SET NULL"},
    "diana_preference_evidence": {
        ("source_message_id", "messages", "id"): "SET NULL",
        ("source_experience_id", "experiences", "experience_id"): "CASCADE",
    },
    "preference_evidence": {
        ("message_id", "messages", "id"): "CASCADE",
        ("experience_id", "experiences", "experience_id"): "CASCADE",
    },
    "emotion_attributions": {("source_experience_id", "experiences", "experience_id"): "SET NULL"},
    "relationship_log": {("source_experience_id", "experiences", "experience_id"): "SET NULL"},
}


def _normalized_fk_sql(sql: str, column: str, parent_table: str, parent_column: str, action: str) -> str:
    """Set exactly one ON DELETE action for a quoted SQLite FK declaration."""
    prefix = (
        rf'FOREIGN KEY \("{re.escape(column)}"\) REFERENCES '
        rf'"{re.escape(parent_table)}" \("{re.escape(parent_column)}"\)'
    )
    return re.sub(
        rf"({prefix})(?: ON DELETE (?:SET NULL|CASCADE|RESTRICT|NO ACTION))*",
        rf"\1 ON DELETE {action}",
        sql,
    )


def _make_column_nullable(sql: str, column: str) -> str:
    """Remove only NOT NULL from the named, quoted table column."""
    pattern = rf'("{re.escape(column)}"\s+[A-Z_]+(?:\([^)]*\))?)\s+NOT NULL'
    updated, substitutions = re.subn(pattern, r"\1", sql, count=1)
    if substitutions != 1:
        raise RuntimeError(f"Could not make {column} nullable while rebuilding table")
    return updated


async def _set_null_not_null_columns(connection, table: str) -> set[str]:
    columns = {item["name"]: item for item in await connection.fetch(f'PRAGMA table_info("{table}")')}
    return {
        fk["from"]
        for fk in await connection.fetch(f'PRAGMA foreign_key_list("{table}")')
        if fk["on_delete"].upper() == "SET NULL" and columns[fk["from"]]["notnull"]
    }


async def _not_null_columns(connection, table: str) -> set[str]:
    return {item["name"] for item in await connection.fetch(f'PRAGMA table_info("{table}")') if item["notnull"]}


async def _all_invalid_set_null_columns(connection) -> list[tuple[str, str]]:
    tables = await connection.fetch("select name from sqlite_master where type='table' and name not like 'sqlite_%'")
    return [
        (table["name"], column)
        for table in tables
        for column in await _set_null_not_null_columns(connection, table["name"])
    ]


async def _remove_stale_copy(connection, table: str) -> None:
    """Clean up an unused copy left by an interrupted older migration safely."""
    copy = f"{table}__cascade"
    exists = await connection.fetchrow("select name from sqlite_master where type='table' and name=$1", copy)
    if exists is None:
        return
    original_count = await connection.fetchval(f'SELECT count(*) FROM "{table}"')
    copy_count = await connection.fetchval(f'SELECT count(*) FROM "{copy}"')
    if original_count != copy_count:
        raise RuntimeError(f"Refusing to remove stale {copy}: row count differs from {table}")
    await connection.execute(f'DROP TABLE "{copy}"')


async def _rebuild_table(connection, table: str) -> None:
    row = await connection.fetchrow("select sql from sqlite_master where type='table' and name=$1", table)
    if row is None or not row["sql"]:
        raise RuntimeError(f"Missing CREATE TABLE SQL for {table}")
    create_sql = row["sql"].replace(f'CREATE TABLE "{table}"', f'CREATE TABLE "{table}__cascade"', 1)
    for (column, parent_table, parent_column), action in FK_ACTIONS[table].items():
        create_sql = _normalized_fk_sql(create_sql, column, parent_table, parent_column, action)

    # Nullability is derived from the actual schema, rather than an assumption
    # embedded in a migration file. Every SET NULL target must be nullable.
    not_null = await _not_null_columns(connection, table)
    nullable_targets = await _set_null_not_null_columns(connection, table)
    nullable_targets.update(
        column
        for (column, _parent_table, _parent_column), action in FK_ACTIONS[table].items()
        if action == "SET NULL" and column in not_null
    )
    for column in nullable_targets:
        create_sql = _make_column_nullable(create_sql, column)

    indexes = await connection.fetch("select sql from sqlite_master where type='index' and tbl_name=$1 and sql is not null", table)
    triggers = await connection.fetch("select sql from sqlite_master where type='trigger' and tbl_name=$1 and sql is not null", table)
    count = await connection.fetchval(f'SELECT count(*) FROM "{table}"')
    await connection.execute(create_sql)
    await connection.execute(f'INSERT INTO "{table}__cascade" SELECT * FROM "{table}"')
    if await connection.fetchval(f'SELECT count(*) FROM "{table}__cascade"') != count:
        raise RuntimeError(f"Row count changed while rebuilding {table}")
    await connection.execute(f'DROP TABLE "{table}"')
    await connection.execute(f'ALTER TABLE "{table}__cascade" RENAME TO "{table}"')
    for index in indexes:
        await connection.execute(index["sql"])
    for trigger in triggers:
        await connection.execute(trigger["sql"])


async def main() -> None:
    pool = await create_pool(get_settings())
    assert pool is not None
    try:
        async with pool.acquire() as connection:
            for table in FK_ACTIONS:
                await _remove_stale_copy(connection, table)
            invalid_before = await _all_invalid_set_null_columns(connection)
            unmanaged = [item for item in invalid_before if item[0] not in FK_ACTIONS]
            if unmanaged:
                raise RuntimeError(f"Unmanaged SET NULL columns are NOT NULL: {unmanaged}")
            for table in sorted({table for table, _column in invalid_before}):
                await _rebuild_table(connection, table)
            violations = await connection.fetch("PRAGMA foreign_key_check")
            if violations:
                raise RuntimeError(f"Foreign-key violations after migration: {violations}")
            invalid = await _all_invalid_set_null_columns(connection)
            if invalid:
                raise RuntimeError(f"SET NULL columns still NOT NULL: {invalid}")
            print("TURSO_MESSAGE_CASCADE_MIGRATION_OK")
            print("TURSO_SET_NULL_NULLABILITY_OK")
    finally:
        await close_pool(pool)


if __name__ == "__main__":
    asyncio.run(main())
