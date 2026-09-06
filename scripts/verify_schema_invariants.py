"""Read-only verification of the currently configured Turso schema."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import get_settings
from app.database.connection import close_pool, create_pool
from app.database.schema_contract import SchemaState, classify_turso_schema


async def verify(connection) -> None:
    report = await classify_turso_schema(connection)
    print(f"SCHEMA_STATE={report.state.value}")
    print(f"SCHEMA_VERSION={report.version or 'none'}")
    print(f"MISSING_TABLES={list(report.missing_tables)}")
    print(f"MISSING_COLUMNS={list(report.missing_columns)}")
    print(f"MISSING_CONSTRAINTS={list(report.missing_constraints)}")
    print(f"SCHEMA_INVARIANT_ERRORS={list(report.invariant_errors)}")
    if report.state != SchemaState.CURRENT:
        raise SystemExit(1)
    print("SCHEMA_INVARIANTS_OK")


async def main() -> None:
    pool = await create_pool(get_settings())
    if pool is None:
        raise RuntimeError("Database is not configured")
    try:
        async with pool.acquire() as connection:
            await verify(connection)
    finally:
        await close_pool(pool)


if __name__ == "__main__":
    asyncio.run(main())
