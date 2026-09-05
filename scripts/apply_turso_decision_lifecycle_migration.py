from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.database.connection import close_pool, create_pool


async def apply_lifecycle_migration(connection) -> None:
    """Idempotently add lifecycle fields and recover legacy JSON provenance."""
    existing = {row["name"] for row in await connection.fetch("pragma table_info(decision_log)")}
    additions = {
        "conversation_id": "alter table decision_log add column conversation_id text",
        "decision_domain": "alter table decision_log add column decision_domain text",
        "status": "alter table decision_log add column status text not null default 'active' check(status in ('active','executed','superseded','cancelled','expired'))",
        "updated_at": "alter table decision_log add column updated_at text",
        "resolved_at": "alter table decision_log add column resolved_at text",
    }
    async with connection.transaction():
        for column, statement in additions.items():
            if column not in existing:
                await connection.execute(statement)
        await connection.execute("update decision_log set status='active' where status is null")
        await connection.execute("update decision_log set updated_at=created_at where updated_at is null")
        legacy_rows = await connection.fetch("select id,new_value from decision_log where conversation_id is null")
        for row in legacy_rows:
            payload = row["new_value"] if isinstance(row["new_value"], dict) else {}
            conversation_id = payload.get("conversation_id")
            if conversation_id:
                await connection.execute(
                    "update decision_log set conversation_id=$1 where id=$2", conversation_id, row["id"],
                )
        await connection.execute("create index if not exists idx_decision_log_lifecycle on decision_log(conversation_id, decision_domain, status, updated_at desc)")


async def main() -> None:
    settings = get_settings()
    if settings.database_backend.lower() != "turso":
        raise RuntimeError("DATABASE_BACKEND=turso is required")
    pool = await create_pool(settings)
    try:
        async with pool.acquire() as connection:
            await apply_lifecycle_migration(connection)
            invalid = await connection.fetch("pragma foreign_key_check")
            if invalid:
                raise RuntimeError(f"foreign key check failed: {invalid}")
        print("DECISION_LIFECYCLE_TURSO_MIGRATION_OK")
    finally:
        await close_pool(pool)


if __name__ == "__main__":
    asyncio.run(main())
