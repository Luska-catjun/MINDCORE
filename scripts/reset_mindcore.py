"""Safely remove accumulated Diana conversation and MindCore data, never schema."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings
from app.database.connection import create_pool, close_pool

TABLES = (
    # Children precede every referenced parent for both PostgreSQL and Turso.
    "diana_self_model_evidence", "diana_self_model", "diana_narrative_evidence", "diana_narratives", "diana_preference_evidence",
    "diana_knowledge_facts", "diana_knowledge", "decision_log", "emotion_attributions", "relationship_log", "state_log",
    "diana_working_memory_items",
    "preference_evidence", "preferences", "memories", "episodes", "experiences", "messages", "conversations",
)
# Deleting the singleton also removes its v0.3 emotion_vector; the next
# deterministic state write recreates the neutral {} baseline.
SINGLETONS = ("relationship", "diana_state")


async def main(apply: bool) -> None:
    settings = get_settings()
    pool = await create_pool(settings)
    if pool is None:
        raise RuntimeError("SUPABASE_DB_URL is required.")
    try:
        async with pool.acquire() as connection:
            if settings.database_backend.lower() == "turso":
                existing = {row["name"] for row in await connection.fetch("select name from sqlite_master where type='table'")}
            else:
                existing = set(await connection.fetchval("select array_agg(tablename) from pg_tables where schemaname = 'public'"))
            targets = [table for table in TABLES if table in existing]
            counts = {table: await connection.fetchval(f"select count(*) from {table}") for table in targets}
            singleton_counts = {table: await connection.fetchval(f"select count(*) from {table}") for table in SINGLETONS if table in existing}
            print("DRY RUN" if not apply else "APPLYING RESET")
            for table, count in {**counts, **singleton_counts}.items():
                print(f"{table}: {count}")
            if not apply:
                return
            async with connection.transaction():
                for table in targets:
                    await connection.execute(f"delete from {table}")
                for table in SINGLETONS:
                    if table in existing:
                        await connection.execute(f"delete from {table}")
    finally:
        await close_pool(pool)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Perform deletion after the confirmation guard.")
    parser.add_argument("--confirm", help="Must equal RESET_DIANA_MINDCORE when --apply is used.")
    args = parser.parse_args()
    if args.apply and args.confirm != "RESET_DIANA_MINDCORE":
        parser.error("--apply requires --confirm RESET_DIANA_MINDCORE")
    asyncio.run(main(args.apply))
