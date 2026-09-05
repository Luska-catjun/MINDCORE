"""Apply additive Working Memory v1 schema to configured Turso."""
from __future__ import annotations
import asyncio, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.config import get_settings
from app.database.connection import create_pool,close_pool
async def main():
 settings=get_settings()
 if settings.database_backend.lower()!='turso':raise RuntimeError('Working Memory migration requires DATABASE_BACKEND=turso.')
 pool=await create_pool(settings)
 try:
  async with pool.acquire() as c:
   for part in (ROOT/'db/migrations/017_working_memory_v01.sql').read_text().split(';'):
    if part.strip():await c.execute(part)
   if await c.fetch('pragma foreign_key_check'):raise RuntimeError('foreign key check failed')
  print('WORKING_MEMORY_TURSO_MIGRATION_OK')
 finally:await close_pool(pool)
if __name__=='__main__':asyncio.run(main())
