from __future__ import annotations
import asyncio
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from app.config import get_settings
from app.database.connection import close_pool, create_pool

async def main() -> None:
    settings=get_settings()
    if settings.database_backend.lower() != 'turso': raise RuntimeError('DATABASE_BACKEND=turso is required')
    pool=await create_pool(settings)
    try:
        async with pool.acquire() as c:
            for statement in Path('db/migrations/018_goals_needs_v01.sql').read_text().split(';'):
                if statement.strip(): await c.execute(statement)
            if await c.fetch('pragma foreign_key_check'): raise RuntimeError('foreign key check failed')
        print('GOALS_NEEDS_TURSO_MIGRATION_OK')
    finally: await close_pool(pool)
if __name__ == '__main__': asyncio.run(main())
