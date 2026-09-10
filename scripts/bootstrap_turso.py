"""Create the current schema on a truly empty Turso/libSQL database only."""
from __future__ import annotations
import asyncio, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.config import get_settings
from app.database.connection import create_pool,close_pool
from app.database.migrations import ensure_turso_schema_current
from app.database.schema_contract import (
    CURRENT_TURSO_BASELINE_VERSION,
    SchemaState,
    classify_turso_schema,
)

BASELINE=ROOT/'db/turso/baseline_v1.sql'

async def bootstrap(connection) -> str:
    report = await classify_turso_schema(connection)
    if report.state == SchemaState.PARTIAL_OR_UNKNOWN and (
        report.version is None
        or not report.version.isdecimal()
        or int(report.version) >= int(CURRENT_TURSO_BASELINE_VERSION)
    ):
        raise RuntimeError(f'{report.details()}; refusing bootstrap overwrite.')
    result = await ensure_turso_schema_current(
        connection, baseline_sql=BASELINE.read_text(encoding="utf-8")
    )
    if result.bootstrapped:
        return f'TURSO_BOOTSTRAP_OK version={CURRENT_TURSO_BASELINE_VERSION}'
    if result.adopted_legacy:
        return 'TURSO_BOOTSTRAP_COMPATIBLE_LEGACY'
    if not result.applied_migration_ids:
        return 'TURSO_BOOTSTRAP_ALREADY_INITIALIZED'
    return f'TURSO_BOOTSTRAP_MIGRATED version={CURRENT_TURSO_BASELINE_VERSION}'

async def main():
    settings=get_settings()
    if settings.database_backend.lower()!='turso': raise RuntimeError('DATABASE_BACKEND=turso is required')
    pool=await create_pool(settings)
    try:
        async with pool.acquire() as connection: print(await bootstrap(connection))
    finally: await close_pool(pool)
if __name__=='__main__': asyncio.run(main())
