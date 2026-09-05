import asyncio
from typing import Any
from uuid import UUID
from uuid import uuid4
from datetime import datetime, timezone
import logging
from time import perf_counter

import asyncpg

from app.schemas.conversations import ConversationCreate
from app.schemas.episodes import EpisodeCreate
from app.schemas.identity import IdentityCreate
from app.schemas.messages import MessageCreate
from app.schemas.relationship import RelationshipCreate
from app.schemas.state import StateCreate
from app.database.normalization import normalize_json_object, normalize_json_value

logger = logging.getLogger("diana.repository")
MESSAGE_SEQUENCE_MAX_ATTEMPTS = 3


async def update_observed_memory(connection: Any, *, memory_id: UUID, content: str, normalized_content: str, updated_at: str) -> bool:
    row = await connection.fetchrow(
        """update memories set content=$1, normalized_content=$2, updated_at=$3
           where memory_id=$4 returning memory_id""",
        content, normalized_content, updated_at, memory_id,
    )
    return row is not None


async def delete_observed_memory(connection: Any, *, memory_id: UUID) -> bool:
    return await connection.fetchrow("delete from memories where memory_id=$1 returning memory_id", memory_id) is not None


async def update_observed_knowledge(connection: Any, *, knowledge_id: UUID, summary: str, updated_at: str) -> bool:
    row = await connection.fetchrow(
        "update diana_knowledge set summary=$1, updated_at=$2 where knowledge_id=$3 returning knowledge_id",
        summary, updated_at, knowledge_id,
    )
    return row is not None


async def delete_observed_knowledge(connection: Any, *, knowledge_id: UUID) -> bool:
    exists = await connection.fetchrow("select knowledge_id from diana_knowledge where knowledge_id=$1", knowledge_id)
    if exists is None:
        return False
    await connection.execute("delete from diana_knowledge_facts where knowledge_id=$1", knowledge_id)
    await connection.execute("delete from diana_knowledge where knowledge_id=$1", knowledge_id)
    return True


async def update_observed_persona_preference(connection: Any, *, preference_id: UUID, display_name: str, updated_at: str) -> bool:
    row = await connection.fetchrow(
        """update diana_preferences set display_name=$1, updated_at=$2
           where diana_preference_id=$3 returning diana_preference_id""",
        display_name, updated_at, preference_id,
    )
    return row is not None


async def delete_observed_persona_preference(connection: Any, *, preference_id: UUID) -> bool:
    exists = await connection.fetchrow("select diana_preference_id from diana_preferences where diana_preference_id=$1", preference_id)
    if exists is None:
        return False
    await connection.execute("delete from diana_preference_evidence where diana_preference_id=$1", preference_id)
    await connection.execute("delete from diana_preferences where diana_preference_id=$1", preference_id)
    return True


async def update_observed_self_model(connection: Any, *, item_id: UUID, summary: str, updated_at: str) -> bool:
    row = await connection.fetchrow(
        "update diana_self_model set summary=$1, updated_at=$2 where id=$3 returning id",
        summary, updated_at, item_id,
    )
    return row is not None


async def delete_observed_self_model(connection: Any, *, item_id: UUID) -> bool:
    return await connection.fetchrow("delete from diana_self_model where id=$1 returning id", item_id) is not None


async def update_observed_narrative(connection: Any, *, item_id: UUID, summary: str, updated_at: str) -> bool:
    row = await connection.fetchrow(
        "update diana_narratives set summary=$1, updated_at=$2 where id=$3 returning id",
        summary, updated_at, item_id,
    )
    return row is not None


async def delete_observed_narrative(connection: Any, *, item_id: UUID) -> bool:
    return await connection.fetchrow("delete from diana_narratives where id=$1 returning id", item_id) is not None


def _json_object(value: Any) -> dict[str, Any]:
    return normalize_json_object(value)


def _json_value(value: Any, default: Any) -> Any:
    return normalize_json_value(value, default)


async def get_identity(pool: asyncpg.Pool) -> dict[str, Any] | None:
    async with pool.acquire() as connection:
        record = await connection.fetchrow(
            """
            select id, core_identity, personality, speech_style, preferences,
                initial_relationship, is_active, created_at
            from diana_identity
            where is_active = true
            order by created_at desc
            limit 1
            """
        )
    if record is None:
        return None
    core_identity = _json_object(record["core_identity"])
    return {
        "id": record["id"],
        "display_name": core_identity.get("display_name", "Persona"),
        "description": core_identity.get("description"),
        "traits": record["personality"] or {},
        "system_notes": {
            "core_identity": core_identity,
            "speech_style": _json_value(record["speech_style"], None),
            "preferences": _json_value(record["preferences"], None),
            "initial_relationship": _json_value(record["initial_relationship"], None),
            "is_active": record["is_active"],
        },
        "created_at": record["created_at"],
        "updated_at": record["created_at"],
    }


async def upsert_identity(pool: asyncpg.Pool, payload: IdentityCreate) -> dict[str, Any]:
    """Map the legacy API payload onto the active Supabase identity schema."""
    async with pool.acquire() as connection:
        existing = await connection.fetchrow(
            """
            select id, core_identity, speech_style, preferences, initial_relationship
            from diana_identity where is_active = true order by created_at desc limit 1
            """,
        )
        notes = payload.system_notes
        core = notes.get("core_identity") if isinstance(notes.get("core_identity"), dict) else {}
        core = {**core, "display_name": payload.display_name, "description": payload.description}
        if existing is None:
            await connection.execute(
                """insert into diana_identity(id, core_identity, personality, speech_style, preferences, initial_relationship, is_active, created_at)
                   values($1,$2,$3,$4,$5,$6,true,$7)""",
                uuid4(), core, payload.traits, notes.get("speech_style"), notes.get("preferences"), notes.get("initial_relationship"), datetime.now(timezone.utc),
            )
        else:
            await connection.execute(
                """update diana_identity set core_identity=$2, personality=$3,
                   speech_style=$4, preferences=$5, initial_relationship=$6 where id=$1""",
                existing["id"], core, payload.traits,
                notes.get("speech_style", existing["speech_style"]),
                notes.get("preferences", existing["preferences"]),
                notes.get("initial_relationship", existing["initial_relationship"]),
            )
    identity = await get_identity(pool)
    assert identity is not None
    return identity


async def create_conversation(pool: asyncpg.Pool, payload: ConversationCreate) -> dict[str, Any]:
    conversation_id = uuid4()
    created_at = datetime.now(timezone.utc)
    async with pool.acquire() as connection:
        record = await connection.fetchrow(
            """
            insert into conversations (conversation_id, source_device, started_at)
            values ($1, $2, $3)
            returning conversation_id, source_device, started_at, ended_at
            """,
            conversation_id, payload.source_device or "unknown", created_at,
        )
    return {
        "id": record["conversation_id"],
        "title": payload.title,
        "source_device": record["source_device"],
        "status": payload.status,
        "metadata": payload.metadata,
        "started_at": record["started_at"],
        "last_message_at": record["ended_at"],
        "created_at": record["started_at"],
        "updated_at": record["started_at"],
    }


async def list_conversations(pool: asyncpg.Pool, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    async with pool.acquire() as connection:
        records = await connection.fetch(
            """
            select conversation_id, source_device, started_at, ended_at
            from conversations
            order by started_at desc
            limit $1 offset $2
            """,
            limit,
            offset,
        )
    return [
        {
            "id": record["conversation_id"],
            "title": None,
            "source_device": record["source_device"],
            "status": "active",
            "metadata": {},
            "started_at": record["started_at"],
            "last_message_at": record["ended_at"],
            "created_at": record["started_at"],
            "updated_at": record["started_at"],
        }
        for record in records
    ]


async def create_message(pool: asyncpg.Pool, payload: MessageCreate) -> dict[str, Any]:
    total_started = perf_counter()
    for attempt in range(MESSAGE_SEQUENCE_MAX_ATTEMPTS):
        acquire_started = perf_counter()
        try:
            async with pool.acquire() as connection:
                acquire_ms = (perf_counter() - acquire_started) * 1000
                async with connection.transaction():
                    message_id = uuid4()
                    created_at = datetime.now(timezone.utc)
                    insert_started = perf_counter()
                    if payload.sequence is None:
                        # SQLite serializes the write statement itself, so MAX()+1 is
                        # evaluated atomically with this insert instead of on a stale
                        # prior SELECT result.  The durable unique constraint remains
                        # the final invariant for (conversation_id, sequence).
                        record = await connection.fetchrow(
                            """
                            insert into messages (id, conversation_id, role, source_device, sequence, content, created_at)
                            select $1, $2, $3, $4, coalesce(max(sequence), 0) + 1, $5, $6
                            from messages where conversation_id = $2
                            returning id, conversation_id, role, source_device, sequence, content, created_at
                            """,
                            message_id, payload.conversation_id, payload.role,
                            payload.source_device or "unknown", payload.content, created_at,
                        )
                    else:
                        record = await connection.fetchrow(
                            """
                            insert into messages (id, conversation_id, role, source_device, sequence, content, created_at)
                            values ($1, $2, $3, $4, $5, $6, $7)
                            returning id, conversation_id, role, source_device, sequence, content, created_at
                            """,
                            message_id, payload.conversation_id, payload.role,
                            payload.source_device or "unknown", payload.sequence,
                            payload.content, created_at,
                        )
                    insert_with_sequence_ms = (perf_counter() - insert_started) * 1000
                    commit_started = perf_counter()
                commit_ms = (perf_counter() - commit_started) * 1000
            break
        except ValueError as exc:
            retryable = payload.sequence is None and "database is locked" in str(exc).casefold()
            if not retryable or attempt + 1 == MESSAGE_SEQUENCE_MAX_ATTEMPTS:
                raise
            logger.warning("Message sequence insert retry attempt=%s error_type=%s", attempt + 1, type(exc).__name__)
            await asyncio.sleep(0.01 * (attempt + 1))
    logger.info(
        "DB_LATENCY operation=message_insert acquire_ms=%.2f insert_with_sequence_ms=%.2f "
        "commit_ms=%.2f attempts=%s total_ms=%.2f",
        acquire_ms, insert_with_sequence_ms, max(0.0, commit_ms),
        attempt + 1,
        (perf_counter() - total_started) * 1000,
    )
    return {
        **dict(record),
        "metadata": payload.metadata,
        "timestamp": record["created_at"],
    }


async def list_messages(
    pool: asyncpg.Pool,
    conversation_id: UUID,
    limit: int = 200,
    offset: int = 0,
    *,
    latest: bool = False,
) -> list[dict[str, Any]]:
    async with pool.acquire() as connection:
        if latest:
            # Select the most recent window efficiently, then restore the
            # existing old-to-new display order for the chat UI.
            records = await connection.fetch(
                """
                select id, conversation_id, role, source_device, sequence, content, created_at
                from (
                    select id, conversation_id, role, source_device, sequence, content, created_at
                    from messages
                    where conversation_id = $1
                    order by sequence desc, created_at desc
                    limit $2 offset $3
                )
                order by sequence asc, created_at asc
                """,
                conversation_id,
                limit,
                offset,
            )
        else:
            records = await connection.fetch(
                """
                select id, conversation_id, role, source_device, sequence, content, created_at
                from messages
                where conversation_id = $1
                order by sequence asc, created_at asc
                limit $2 offset $3
                """,
                conversation_id,
                limit,
                offset,
            )
    return [
        {
            **dict(record),
            "metadata": {},
            "timestamp": record["created_at"],
        }
        for record in records
    ]


async def list_messages_for_observation(
    pool: asyncpg.Pool,
    *,
    conversation_id: str | None,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    """Return the admin Message projection without exposing message SQL to routers."""
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """select id, conversation_id, role, content, created_at from messages
               where ($1 is null or conversation_id=$1)
               order by created_at desc limit $2 offset $3""",
            conversation_id,
            limit,
            offset,
        )
        total = await connection.fetchval(
            "select count(*) from messages where ($1 is null or conversation_id=$1)",
            conversation_id,
        )
    return [dict(row) for row in rows], int(total)


async def delete_message(pool: asyncpg.Pool, message_id: UUID) -> bool:
    """Delete a Message through its owner boundary.

    FK lifecycle rules own dependent Experience deletion and durable provenance
    detachment; this function deliberately performs no application-side cascade.
    """
    async with pool.acquire() as connection:
        async with connection.transaction():
            exists = await connection.fetchval(
                "select 1 from messages where id=$1", message_id
            )
            if not exists:
                return False
            await connection.execute("delete from messages where id=$1", message_id)
    return True


async def create_episode(pool: asyncpg.Pool, payload: EpisodeCreate) -> dict[str, Any]:
    episode_id = uuid4()
    created_at = datetime.now(timezone.utc)
    async with pool.acquire() as connection:
        record = await connection.fetchrow(
            """
            insert into episodes (
                episode_id, conversation_id, sequence, summary, source_device,
                importance, emotional_impact, personal_relevance,
                relationship_impact, novelty, confidence, recall_frequency,
                memory_strength, decay, created_at, started_at, ended_at, episode_type, provenance, is_grounded, updated_at
            )
            values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $15, $15, 'conversation', 'grounded_event', 1, $16)
            returning *
            """,
            episode_id, payload.conversation_id,
            payload.sequence or 1,
            payload.content,
            payload.source_device or "unknown",
            payload.importance,
            payload.emotional_impact,
            payload.personal_relevance,
            payload.relationship_impact,
            payload.novelty,
            payload.confidence,
            int(payload.recall_frequency),
            payload.memory_strength,
            payload.decay, created_at, created_at,
        )
    return {
        "id": record["episode_id"],
        "conversation_id": record["conversation_id"],
        "message_id": payload.message_id,
        "title": payload.title,
        "content": record["summary"],
        "source_device": record["source_device"],
        "sequence": record["sequence"],
        "importance": record["importance"],
        "emotional_impact": record["emotional_impact"],
        "valence": payload.valence,
        "novelty": record["novelty"],
        "confidence": record["confidence"],
        "relationship_impact": record["relationship_impact"],
        "personal_relevance": record["personal_relevance"],
        "recall_frequency": record["recall_frequency"],
        "context_relevance": payload.context_relevance,
        "memory_strength": record["memory_strength"],
        "decay": record["decay"],
        "metadata": payload.metadata,
        "timestamp": record["created_at"],
        "created_at": record["created_at"],
    }


async def list_episodes(pool: asyncpg.Pool, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    async with pool.acquire() as connection:
        records = await connection.fetch(
            """
            select *
            from episodes
            order by created_at desc
            limit $1 offset $2
            """,
            limit,
            offset,
        )
    return [
        {
            "id": record["episode_id"],
            "conversation_id": record["conversation_id"],
            "message_id": None,
            "title": None,
            "content": record["summary"],
            "source_device": record["source_device"],
            "sequence": record["sequence"],
            "importance": record["importance"] or 0,
            "emotional_impact": record["emotional_impact"] or 0,
            "valence": 0,
            "novelty": record["novelty"] or 0,
            "confidence": record["confidence"] or 0,
            "relationship_impact": record["relationship_impact"] or 0,
            "personal_relevance": record["personal_relevance"] or 0,
            "recall_frequency": record["recall_frequency"] or 0,
            "context_relevance": 0,
            "memory_strength": record["memory_strength"] or 0,
            "decay": record["decay"] or 0,
            "metadata": {},
            "timestamp": record["created_at"],
            "created_at": record["created_at"],
        }
        for record in records
    ]


async def get_state(pool: asyncpg.Pool) -> dict[str, Any] | None:
    async with pool.acquire() as connection:
        record = await connection.fetchrow(
            """
            select id, emotion, emotion_vector, energy, mood_valence, curiosity, stress, source_device, updated_at
            from diana_state
            where id = 1
            """
        )
    if record is None:
        return None
    return {
        "id": record["id"],
        "mood": record["emotion"],
        "energy": record["energy"],
        "focus": None,
        "values": {
            "mood_valence": record["mood_valence"],
            "curiosity": record["curiosity"],
            "stress": record["stress"],
            "emotion_vector": _json_object(record["emotion_vector"]),
        },
        "source_device": record["source_device"],
        "created_at": None,
        "updated_at": record["updated_at"],
    }


async def upsert_state(pool: asyncpg.Pool, payload: StateCreate) -> dict[str, Any]:
    updated_at = datetime.now(timezone.utc)
    from app.services.mindcore.internal_state import _empty_vector, _normalized_vector, primary_emotion
    vector = _normalized_vector(payload.values.get("emotion_vector") or _empty_vector(), None, None)
    emotion, intensity = primary_emotion(vector, payload.mood or "neutral")
    async with pool.acquire() as connection:
        record = await connection.fetchrow(
            """
            insert into diana_state (id, emotion, emotion_intensity, emotion_vector, energy, mood_valence, curiosity, stress, source_device, updated_at)
            values (1, $1, $2, $3, $4, $5, $6, $7, $8, $9)
            on conflict (id) do update set
                emotion = excluded.emotion,
                emotion_intensity = excluded.emotion_intensity,
                emotion_vector = excluded.emotion_vector,
                energy = excluded.energy,
                mood_valence = excluded.mood_valence,
                curiosity = excluded.curiosity,
                stress = excluded.stress,
                source_device = excluded.source_device,
                updated_at = excluded.updated_at
            returning id, emotion, emotion_vector, energy, mood_valence, curiosity, stress, source_device, updated_at
            """,
            emotion, intensity, vector,
            payload.energy,
            payload.values.get("mood_valence"),
            payload.values.get("curiosity"),
            payload.values.get("stress"),
            payload.source_device,
            updated_at,
        )
    return {
        "id": record["id"],
        "mood": record["emotion"],
        "energy": record["energy"],
        "focus": payload.focus,
        "values": {
            **payload.values,
            "mood_valence": record["mood_valence"],
            "curiosity": record["curiosity"],
            "stress": record["stress"],
            "emotion_vector": _json_object(record["emotion_vector"]),
        },
        "source_device": record["source_device"],
        "created_at": None,
        "updated_at": record["updated_at"],
    }


async def get_relationship(pool: asyncpg.Pool, user_label: str = "primary_user") -> dict[str, Any] | None:
    async with pool.acquire() as connection:
        record = await connection.fetchrow(
            """
            select id, familiarity, trust, affection, shared_experience, conflict_history, updated_at
            from relationship
            where id = 1
            """
        )
    if record is None:
        return None
    return {
        "id": record["id"],
        "user_label": user_label,
        "closeness": record["affection"] or 0,
        "trust": record["trust"] or 0,
        "familiarity": record["familiarity"] or 0,
        "notes": None,
        "metadata": {
            "shared_experience": record["shared_experience"],
            "conflict_history": _json_value(record["conflict_history"], []),
        },
        "source_device": None,
        "created_at": None,
        "updated_at": record["updated_at"],
    }


async def upsert_relationship(pool: asyncpg.Pool, payload: RelationshipCreate) -> dict[str, Any]:
    updated_at = datetime.now(timezone.utc)
    async with pool.acquire() as connection:
        record = await connection.fetchrow(
            """
            insert into relationship (id, familiarity, trust, affection, shared_experience, conflict_history, conflict, updated_at)
            values (1, $1, $2, $3, $4, $5, 0, $6)
            on conflict (id) do update set
                familiarity = excluded.familiarity,
                trust = excluded.trust,
                affection = excluded.affection,
                shared_experience = excluded.shared_experience,
                conflict_history = excluded.conflict_history,
                updated_at = excluded.updated_at
            returning id, familiarity, trust, affection, shared_experience, conflict_history, updated_at
            """,
            payload.familiarity,
            payload.trust,
            payload.closeness,
            payload.metadata.get("shared_experience"),
            payload.metadata.get("conflict_history", []),
            updated_at,
        )
    return {
        "id": record["id"],
        "user_label": payload.user_label,
        "closeness": record["affection"] or 0,
        "trust": record["trust"] or 0,
        "familiarity": record["familiarity"] or 0,
        "notes": payload.notes,
        "metadata": {
            **payload.metadata,
            "shared_experience": record["shared_experience"],
            "conflict_history": _json_value(record["conflict_history"], []),
        },
        "source_device": payload.source_device,
        "created_at": None,
        "updated_at": record["updated_at"],
    }
