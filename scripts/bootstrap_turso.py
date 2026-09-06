"""Create the current schema on a truly empty Turso/libSQL database only."""
from __future__ import annotations
import asyncio, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.config import get_settings
from app.database.connection import create_pool,close_pool
from app.database.schema_contract import (
    CURRENT_TURSO_BASELINE_VERSION,
    SchemaState,
    classify_turso_schema,
)

BASELINE=ROOT/'db/turso/baseline_v1.sql'

async def bootstrap(connection) -> str:
    report = await classify_turso_schema(connection)
    if report.state == SchemaState.EMPTY:
        async with connection.transaction():
            for statement in BASELINE.read_text().split(';'):
                if statement.strip(): await connection.execute(statement)
        verified = await classify_turso_schema(connection)
        if verified.state != SchemaState.CURRENT:
            raise RuntimeError(f'Fresh baseline verification failed: {verified.details()}')
        return f'TURSO_BOOTSTRAP_OK version={CURRENT_TURSO_BASELINE_VERSION}'
    if report.state == SchemaState.CURRENT:
        return 'TURSO_BOOTSTRAP_ALREADY_INITIALIZED'
    if report.state == SchemaState.COMPATIBLE_LEGACY:
        return 'TURSO_BOOTSTRAP_COMPATIBLE_LEGACY'
    raise RuntimeError(f'{report.details()}; refusing bootstrap overwrite.')

async def main():
    settings=get_settings()
    if settings.database_backend.lower()!='turso': raise RuntimeError('DATABASE_BACKEND=turso is required')
    pool=await create_pool(settings)
    try:
        async with pool.acquire() as connection: print(await bootstrap(connection))
    finally: await close_pool(pool)
if __name__=='__main__': asyncio.run(main())
