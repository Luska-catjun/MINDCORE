"""Read-only verification of the currently configured Turso schema."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import get_settings
from app.database.connection import close_pool, create_pool


async def main() -> None:
    pool = await create_pool(get_settings())
    if pool is None:
        raise RuntimeError("Database is not configured")
    try:
        async with pool.acquire() as connection:
            tables = await connection.fetch("select name from sqlite_master where type='table' and name not like 'sqlite_%'")
            invalid: list[str] = []
            for table in tables:
                name = table["name"]
                columns = {item["name"]: item["notnull"] for item in await connection.fetch(f'pragma table_info("{name}")')}
                for fk in await connection.fetch(f'pragma foreign_key_list("{name}")'):
                    if fk["on_delete"].upper() == "SET NULL" and columns[fk["from"]]:
                        invalid.append(f"{name}.{fk['from']}")
            temporary = [item["name"] for item in tables if item["name"].endswith("__cascade") or item["name"].endswith("_new") or item["name"].endswith("_old")]
            fk_check = await connection.fetch("pragma foreign_key_check")
            decision_columns = {
                item["name"] for item in await connection.fetch("pragma table_info(decision_log)")
            }
            required_decision_columns = {
                "conversation_id", "decision_domain", "status", "updated_at", "resolved_at",
            }
            missing_decision_columns = sorted(required_decision_columns - decision_columns)
            print("SCHEMA_VERSION_TRACKING_MISSING")
            print(f"SET_NULL_NOT_NULL={invalid}")
            print(f"TEMP_TABLES={temporary}")
            print(f"FOREIGN_KEY_CHECK={fk_check}")
            print(f"DECISION_LIFECYCLE_COLUMNS_MISSING={missing_decision_columns}")
            if invalid or temporary or fk_check or missing_decision_columns:
                raise SystemExit(1)
            print("SCHEMA_INVARIANTS_OK")
    finally:
        await close_pool(pool)


if __name__ == "__main__":
    asyncio.run(main())
