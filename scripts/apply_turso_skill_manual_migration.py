"""Safely apply the one-time Skill Manual Working Memory schema upgrade."""
from __future__ import annotations
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.config import get_settings
from app.database.connection import close_pool, create_pool


async def main() -> None:
    settings = get_settings()
    if settings.database_backend.lower() != "turso":
        raise RuntimeError("Skill Manual migration requires DATABASE_BACKEND=turso.")
    pool = await create_pool(settings)
    try:
        async with pool.acquire() as connection:
            result = await apply_skill_manual_migration(connection)
            if await connection.fetch("pragma foreign_key_check"):
                raise RuntimeError("foreign key check failed")
        print(result)
    finally:
        await close_pool(pool)


async def apply_skill_manual_migration(connection) -> str:
    """Apply only the expected legacy shape; initialized DBs are strict no-ops."""
    exists = await connection.fetchval(
        "select 1 from sqlite_master where type='table' and name='diana_working_memory_items'"
    )
    if not exists:
        raise RuntimeError("Working Memory table is absent; refusing to treat a partial database as fresh.")
    ddl = str(await connection.fetchval(
        "select sql from sqlite_master where type='table' and name='diana_working_memory_items'"
    ) or "")
    if "active_skill" in ddl:
        return "SKILL_MANUAL_TURSO_MIGRATION_ALREADY_APPLIED"
    required = {"active_topic", "open_loop", "active_memory_ref"}
    if not all(slot in ddl for slot in required) or "slot_type" not in ddl:
        raise RuntimeError("Unexpected Working Memory schema; refusing destructive table rebuild.")
    before_count = int(await connection.fetchval("select count(*) from diana_working_memory_items") or 0)
    before_ids = await connection.fetch("select id from diana_working_memory_items order by id")
    async with connection.transaction():
        for statement in (ROOT / "db/migrations/021_skill_manual_v01.sql").read_text().split(";"):
            if statement.strip():
                await connection.execute(statement)
        after_count = int(await connection.fetchval("select count(*) from diana_working_memory_items") or 0)
        after_ids = await connection.fetch("select id from diana_working_memory_items order by id")
        if after_count != before_count or after_ids != before_ids:
            raise RuntimeError("Working Memory row parity check failed; transaction rolled back.")
        if not await connection.fetchval("select 1 from sqlite_master where type='index' and name='idx_wm_conversation_active'"):
            raise RuntimeError("Working Memory lookup index missing after rebuild.")
    return "SKILL_MANUAL_TURSO_MIGRATION_OK"


if __name__ == "__main__":
    asyncio.run(main())
