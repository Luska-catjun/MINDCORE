"""Apply the additive Self Model v0.1 schema to the configured Turso database."""
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
        raise RuntimeError("Self Model Turso migration requires DATABASE_BACKEND=turso.")
    pool = await create_pool(settings)
    if pool is None:
        raise RuntimeError("Turso database is not configured.")
    statements = [part.strip() for part in (PROJECT_ROOT / "db/migrations/016_self_model_shadow_v01.sql").read_text().split(";")]
    statements = ["\n".join(line for line in part.splitlines() if not line.strip().startswith("--")).strip() for part in statements]
    try:
        async with pool.acquire() as connection:
            for statement in statements:
                if statement:
                    await connection.execute(statement)
            fks = await connection.fetch("pragma foreign_key_list('diana_self_model_evidence')")
            conversation_fk = next((row for row in fks if row["from"] == "source_conversation_id"), None)
            if conversation_fk is not None and conversation_fk["to"] != "conversation_id":
                # The first v0.1 draft referenced a non-key conversations.id.
                # Rebuild only this additive child table and preserve every row.
                await connection.execute("""create table diana_self_model_evidence__v01_rebuild (
                    id text primary key, fingerprint text not null unique,
                    self_model_id text not null references diana_self_model(id) on delete cascade,
                    evidence_type text not null, direction text not null check (direction in ('support','contradict')),
                    weight real not null check (weight > 0 and weight <= 1),
                    source_narrative_id text references diana_narratives(id) on delete set null,
                    source_preference_id text references diana_preferences(diana_preference_id) on delete set null,
                    source_decision_id text references decision_log(id) on delete set null,
                    source_episode_id text references episodes(episode_id) on delete set null,
                    source_conversation_id text references conversations(conversation_id) on delete set null,
                    created_at text not null)""")
                await connection.execute("""insert into diana_self_model_evidence__v01_rebuild(
                    id,fingerprint,self_model_id,evidence_type,direction,weight,source_narrative_id,source_preference_id,
                    source_decision_id,source_episode_id,source_conversation_id,created_at)
                    select id,fingerprint,self_model_id,evidence_type,direction,weight,source_narrative_id,source_preference_id,
                    source_decision_id,source_episode_id,source_conversation_id,created_at from diana_self_model_evidence""")
                await connection.execute("drop table diana_self_model_evidence")
                await connection.execute("alter table diana_self_model_evidence__v01_rebuild rename to diana_self_model_evidence")
                await connection.execute("create index if not exists idx_diana_self_model_evidence_belief on diana_self_model_evidence(self_model_id, created_at desc)")
                await connection.execute("create index if not exists idx_diana_self_model_evidence_episode on diana_self_model_evidence(source_episode_id)")
            if await connection.fetch("pragma foreign_key_check"):
                raise RuntimeError("Self Model migration produced foreign-key violations.")
        print("SELF_MODEL_TURSO_MIGRATION_OK")
    finally:
        await close_pool(pool)


if __name__ == "__main__":
    asyncio.run(main())
