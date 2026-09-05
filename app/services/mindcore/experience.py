"""Own immutable Experience creation and retrieval for completed interactions.

An Experience is optional downstream evidence: callers must handle a failed
write as ``None`` and may still finalize an Episode without it.
"""

from __future__ import annotations

import logging
from time import perf_counter
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from app.services.mindcore.working_memory import WorkingMemoryState

logger = logging.getLogger("diana.experience")

STATE_FIELDS = (
    "emotion",
    "emotion_intensity",
    "emotion_vector",
    "mood_valence",
    "energy",
    "curiosity",
    "stress",
)
OUTCOME_TYPES = {
    "neutral", "information", "task_progress", "success", "failure",
    "support", "correction", "planning",
}


def snapshot_state(state: dict[str, Any] | None) -> dict[str, Any] | None:
    if state is None:
        return None
    return {field: state.get(field) for field in STATE_FIELDS}


def classify_outcome(user_text: str) -> str:
    """A deliberately coarse deterministic classification, never an LLM judgment."""
    text = user_text.casefold()
    if any(term in text for term in ("실패", "망했", "failed", "failure")):
        return "failure"
    if any(term in text for term in ("성공", "끝냈", "완료", "success", "completed")):
        return "success"
    if any(term in text for term in ("계획", "예정", "plan", "planning")):
        return "planning"
    if any(term in text for term in ("힘든", "걱정", "불안", "help", "struggling")):
        return "support"
    if any(term in text for term in ("아니", "정정", "틀렸", "actually", "correction")):
        return "correction"
    if "?" in text or any(term in text for term in ("왜", "어떻게", "what", "how", "why")):
        return "information"
    return "neutral"


def _memory_ids(memories: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for memory in memories:
        memory_id = memory.get("memory_id")
        if memory_id is None:
            continue
        value = str(memory_id)
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


async def record_experience(
    pool: asyncpg.Pool,
    *,
    conversation_id: UUID,
    user_message_id: UUID,
    assistant_message_id: UUID,
    user_text: str,
    selected_memories: list[dict[str, Any]],
    state_before: dict[str, Any] | None,
    state_after: dict[str, Any] | None,
    working_memory: WorkingMemoryState | None,
) -> dict[str, Any]:
    """Insert one immutable record, returning an existing row on a duplicate retry."""
    started = perf_counter()
    before = snapshot_state(state_before)
    after = snapshot_state(state_after)
    record_values = (
        uuid4(),
        conversation_id,
        user_message_id,
        assistant_message_id,
        working_memory.current_focus if working_memory else None,
        _memory_ids(selected_memories),
        before,
        after,
        before != after if before is not None and after is not None else False,
        classify_outcome(user_text),
        datetime.now(timezone.utc),
    )
    async with pool.acquire() as connection:
        record = await connection.fetchrow(
            """
            insert into experiences (
                experience_id, conversation_id, user_message_id, assistant_message_id, current_focus,
                activated_memory_ids, state_before, state_after, state_changed, outcome_type, created_at
            )
            values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            on conflict (user_message_id, assistant_message_id) do nothing
            returning *
            """,
            *record_values,
        )
        if record is None:
            record = await connection.fetchrow(
                """
                select * from experiences
                where user_message_id = $1 and assistant_message_id = $2
                """,
                user_message_id,
                assistant_message_id,
            )
    result = dict(record)
    logger.info(
        "Experience recorded id=%s latency_ms=%.2f activated_memories=%s outcome=%s",
        result["experience_id"], (perf_counter() - started) * 1000,
        len(result["activated_memory_ids"]), result["outcome_type"],
    )
    return result


async def get_recent_experiences(pool: asyncpg.Pool, *, limit: int = 50) -> list[dict[str, Any]]:
    async with pool.acquire() as connection:
        records = await connection.fetch(
            "select * from experiences order by created_at desc limit $1",
            limit,
        )
    return [dict(record) for record in records]


async def get_experiences_for_conversation(
    pool: asyncpg.Pool,
    conversation_id: UUID,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    async with pool.acquire() as connection:
        records = await connection.fetch(
            """
            select * from experiences
            where conversation_id = $1
            order by created_at desc
            limit $2
            """,
            conversation_id,
            limit,
        )
    return [dict(record) for record in records]
