"""One-way, non-destructive Supabase PostgreSQL -> Turso data migration.

Reads only from SUPABASE_DB_URL and refuses to write to a non-empty Turso
database. UUIDs, JSON, arrays, and timestamps are stored as stable TEXT/JSON
representations; vector columns retain their declared F32_BLOB dimension.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import libsql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.database.connection import close_pool, create_pool


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def sqlite_type(data_type: str, declared: str) -> str:
    if declared.startswith("vector("):
        return f"F32_BLOB({declared[7:-1]})"
    if data_type in {"uuid", "json", "jsonb", "ARRAY", "timestamp with time zone", "timestamp without time zone", "date"}:
        return "TEXT"
    if data_type == "boolean":
        return "INTEGER"
    if data_type in {"smallint", "integer", "bigint"}:
        return "INTEGER"
    if data_type in {"numeric", "real", "double precision"}:
        return "REAL"
    return "TEXT"


def value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bytes, int, float)):
        return value
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (UUID, datetime, date, Decimal)):
        return str(value)
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    return str(value)


async def main() -> None:
    settings = get_settings()
    if not settings.supabase_db_url or not settings.database_url or not settings.database_auth_token:
        raise RuntimeError("SUPABASE_DB_URL, DATABASE_URL, and DATABASE_AUTH_TOKEN are required.")
    source = await create_pool(settings)
    if source is None:
        raise RuntimeError("Supabase source is not configured.")
    target = libsql.connect(database=settings.database_url, auth_token=settings.database_auth_token)
    try:
        existing_tables = [row[0] for row in target.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'").fetchall()]
        async with source.acquire() as connection:
            tables = [row["table_name"] for row in await connection.fetch(
                "select table_name from information_schema.tables where table_schema='public' and table_type='BASE TABLE' order by table_name"
            )]
            columns = await connection.fetch(
                """select c.table_name, c.column_name, c.data_type, c.is_nullable, c.ordinal_position,
                          pg_catalog.format_type(a.atttypid,a.atttypmod) as declared_type
                   from information_schema.columns c
                   join pg_catalog.pg_class cl on cl.relname=c.table_name
                   join pg_catalog.pg_namespace ns on ns.oid=cl.relnamespace and ns.nspname=c.table_schema
                   join pg_catalog.pg_attribute a on a.attrelid=cl.oid and a.attname=c.column_name
                   where c.table_schema='public' order by c.table_name,c.ordinal_position"""
            )
            pk_rows = await connection.fetch(
                """select tc.table_name, kcu.column_name from information_schema.table_constraints tc
                   join information_schema.key_column_usage kcu on tc.constraint_name=kcu.constraint_name and tc.table_schema=kcu.table_schema
                   where tc.table_schema='public' and tc.constraint_type='PRIMARY KEY' order by tc.table_name,kcu.ordinal_position"""
            )
            unique_rows = await connection.fetch(
                """select tc.table_name, tc.constraint_name, kcu.column_name from information_schema.table_constraints tc
                   join information_schema.key_column_usage kcu on tc.constraint_name=kcu.constraint_name and tc.table_schema=kcu.table_schema
                   where tc.table_schema='public' and tc.constraint_type='UNIQUE' order by tc.table_name,tc.constraint_name,kcu.ordinal_position"""
            )
            fk_rows = await connection.fetch(
                """select tc.table_name, kcu.column_name, ccu.table_name as foreign_table_name, ccu.column_name as foreign_column_name
                   from information_schema.table_constraints tc
                   join information_schema.key_column_usage kcu on tc.constraint_name=kcu.constraint_name and tc.table_schema=kcu.table_schema
                   join information_schema.constraint_column_usage ccu on ccu.constraint_name=tc.constraint_name and ccu.table_schema=tc.table_schema
                   where tc.table_schema='public' and tc.constraint_type='FOREIGN KEY'"""
            )
            by_table: dict[str, list[Any]] = {table: [] for table in tables}
            for row in columns: by_table[row["table_name"]].append(row)
            pks: dict[str, list[str]] = {table: [] for table in tables}
            for row in pk_rows: pks[row["table_name"]].append(row["column_name"])
            uniques: dict[tuple[str, str], list[str]] = {}
            for row in unique_rows: uniques.setdefault((row["table_name"], row["constraint_name"]), []).append(row["column_name"])
            fks: dict[str, list[Any]] = {table: [] for table in tables}
            for row in fk_rows: fks[row["table_name"]].append(row)
            pending = set(tables)
            ordered_tables: list[str] = []
            while pending:
                ready = sorted(table for table in pending if all(row["foreign_table_name"] not in pending for row in fks[table]))
                if not ready:
                    # Source has a reference cycle; the remote session may not
                    # honor deferred foreign keys, so retain deterministic order.
                    ready = [sorted(pending)[0]]
                ordered_tables.extend(ready)
                pending.difference_update(ready)

            target.execute("PRAGMA foreign_keys = OFF")
            for table in tables:
                definitions = [f"{quote(row['column_name'])} {sqlite_type(row['data_type'], row['declared_type'])}{' NOT NULL' if row['is_nullable']=='NO' else ''}" for row in by_table[table]]
                if pks[table]: definitions.append("PRIMARY KEY (" + ", ".join(quote(c) for c in pks[table]) + ")")
                definitions.extend("UNIQUE (" + ", ".join(quote(c) for c in cols) + ")" for (t, _), cols in uniques.items() if t == table)
                definitions.extend(f"FOREIGN KEY ({quote(row['column_name'])}) REFERENCES {quote(row['foreign_table_name'])} ({quote(row['foreign_column_name'])})" for row in fks[table])
                if table not in existing_tables:
                    target.execute(f"CREATE TABLE {quote(table)} ({', '.join(definitions)})")

            counts: dict[str, int] = {}
            for table in ordered_tables:
                records = await connection.fetch(f"select * from {quote(table)}")
                target_count = target.execute(f"select count(*) from {quote(table)}").fetchone()[0]
                if target_count == len(records):
                    counts[table] = target_count
                    print("SKIP", table, target_count, flush=True)
                    continue
                if target_count:
                    raise RuntimeError(f"Refusing resume: {table} has {target_count} rows, source has {len(records)}.")
                names = [row["column_name"] for row in by_table[table]]
                placeholders = ", ".join("?" for _ in names)
                statement = f"INSERT INTO {quote(table)} ({', '.join(quote(name) for name in names)}) VALUES ({placeholders})"
                print("COPY", table, len(records), flush=True)
                target.executemany(statement, [tuple(value(record[name]) for name in names) for record in records])
                counts[table] = len(records)
                target.commit()
            target.execute("PRAGMA foreign_keys = ON")
            target.commit()
        print("MIGRATION_OK", json.dumps(counts, sort_keys=True))
    finally:
        target.close()
        await close_pool(source)


if __name__ == "__main__":
    asyncio.run(main())
