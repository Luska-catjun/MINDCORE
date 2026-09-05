"""Create the current schema on a truly empty Turso/libSQL database only."""
from __future__ import annotations
import asyncio, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.config import get_settings
from app.database.connection import create_pool,close_pool

BASELINE=ROOT/'db/turso/baseline_v1.sql'
CORE={'conversations','messages','episodes','diana_state','diana_working_memory_items'}

async def bootstrap(connection) -> str:
    tables={str(row['name']) for row in await connection.fetch("select name from sqlite_master where type='table' and name not like 'sqlite_%'")}
    if not tables:
        async with connection.transaction():
            for statement in BASELINE.read_text().split(';'):
                if statement.strip(): await connection.execute(statement)
        if await connection.fetch('pragma foreign_key_check') or (await connection.fetchval('pragma integrity_check'))!='ok':
            raise RuntimeError('Fresh baseline integrity verification failed')
        return 'TURSO_BOOTSTRAP_OK version=21'
    if 'schema_metadata' in tables and CORE.issubset(tables): return 'TURSO_BOOTSTRAP_ALREADY_INITIALIZED'
    if CORE.issubset(tables): return 'TURSO_BOOTSTRAP_EXISTING_SCHEMA_SKIPPED'
    raise RuntimeError('PARTIAL_OR_UNKNOWN database; refusing bootstrap overwrite.')

async def main():
    settings=get_settings()
    if settings.database_backend.lower()!='turso': raise RuntimeError('DATABASE_BACKEND=turso is required')
    pool=await create_pool(settings)
    try:
        async with pool.acquire() as connection: print(await bootstrap(connection))
    finally: await close_pool(pool)
if __name__=='__main__': asyncio.run(main())
