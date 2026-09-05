"""Apply the additive Narrative v0.1 schema to the configured Turso database."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings
from app.database.connection import close_pool, create_pool


async def main() -> None:
    settings = get_settings()
    if settings.database_backend.lower() != "turso":
        raise RuntimeError("Narrative Turso migration requires DATABASE_BACKEND=turso.")
    pool = await create_pool(settings)
    if pool is None:
        raise RuntimeError("Turso database is not configured.")
    statements = [statement.strip() for statement in (PROJECT_ROOT / "db/migrations/015_narrative_shadow_v01.sql").read_text().split(";") if statement.strip()]
    # Comments can precede a valid statement after splitting; retain its SQL.
    statements = ["\n".join(line for line in statement.splitlines() if not line.strip().startswith("--")).strip() for statement in statements]
    try:
        async with pool.acquire() as connection:
            for statement in statements:
                if statement:
                    await connection.execute(statement)
        print("NARRATIVE_TURSO_MIGRATION_OK")
    finally:
        await close_pool(pool)


if __name__ == "__main__":
    asyncio.run(main())
