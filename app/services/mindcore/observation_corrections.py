"""Explicit, bounded manual corrections for durable Observation records.

These operations deliberately alter only user-facing text or remove a single
durable record.  They never tune cognitive scores, thresholds, or evidence.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from app.services.memory_service import normalize_memory_content
from app.services import repository


def _text(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("A corrected value is required.")
    if len(value) > 4000:
        raise ValueError("The corrected value is too long.")
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _changed(changed: bool) -> None:
    if not changed:
        raise KeyError("Observation item not found.")


async def update_memory(pool: Any, memory_id: UUID, content: str) -> None:
    content = _text(content)
    async with pool.acquire() as connection:
        _changed(await repository.update_observed_memory(connection, memory_id=memory_id, content=content, normalized_content=normalize_memory_content(content), updated_at=_now()))


async def delete_memory(pool: Any, memory_id: UUID) -> None:
    async with pool.acquire() as connection:
        _changed(await repository.delete_observed_memory(connection, memory_id=memory_id))


async def update_knowledge(pool: Any, knowledge_id: UUID, summary: str) -> None:
    async with pool.acquire() as connection:
        _changed(await repository.update_observed_knowledge(connection, knowledge_id=knowledge_id, summary=_text(summary), updated_at=_now()))


async def delete_knowledge(pool: Any, knowledge_id: UUID) -> None:
    async with pool.acquire() as connection:
        async with connection.transaction():
            _changed(await repository.delete_observed_knowledge(connection, knowledge_id=knowledge_id))


async def update_persona_preference(pool: Any, preference_id: UUID, display_name: str) -> None:
    async with pool.acquire() as connection:
        _changed(await repository.update_observed_persona_preference(connection, preference_id=preference_id, display_name=_text(display_name), updated_at=_now()))


async def delete_persona_preference(pool: Any, preference_id: UUID) -> None:
    async with pool.acquire() as connection:
        async with connection.transaction():
            _changed(await repository.delete_observed_persona_preference(connection, preference_id=preference_id))


async def update_self_model(pool: Any, item_id: UUID, summary: str) -> None:
    async with pool.acquire() as connection:
        _changed(await repository.update_observed_self_model(connection, item_id=item_id, summary=_text(summary), updated_at=_now()))


async def delete_self_model(pool: Any, item_id: UUID) -> None:
    async with pool.acquire() as connection:
        _changed(await repository.delete_observed_self_model(connection, item_id=item_id))


async def update_narrative(pool: Any, item_id: UUID, summary: str) -> None:
    async with pool.acquire() as connection:
        _changed(await repository.update_observed_narrative(connection, item_id=item_id, summary=_text(summary), updated_at=_now()))


async def delete_narrative(pool: Any, item_id: UUID) -> None:
    async with pool.acquire() as connection:
        _changed(await repository.delete_observed_narrative(connection, item_id=item_id))
