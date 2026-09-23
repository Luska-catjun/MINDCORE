"""Authenticated, content-free incremental events for proactive Persona turns."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel


router = APIRouter(prefix="/autonomy", tags=["autonomy"])


class ProactiveEvent(BaseModel):
    message_id: UUID
    conversation_id: UUID
    persona_id: str
    created_at: datetime


class ProactiveEventBatch(BaseModel):
    events: list[ProactiveEvent]
    latest_created_at: datetime | None
    latest_message_id: UUID | None


@router.get("/events", response_model=ProactiveEventBatch)
async def proactive_events(
    request: Request,
    after_created_at: str | None = Query(default=None, max_length=80),
    after_message_id: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=100),
) -> ProactiveEventBatch:
    settings = request.app.state.settings
    persona_id = settings.persona_id
    if not persona_id:
        raise HTTPException(status_code=503, detail="Persona scope is unavailable.")
    if (after_created_at is None) != (after_message_id is None):
        raise HTTPException(status_code=400, detail="Both event cursor fields are required.")
    cursor_time: datetime | None = None
    cursor_id: UUID | None = None
    if after_created_at is not None and after_message_id is not None:
        try:
            cursor_time = datetime.fromisoformat(after_created_at.replace("Z", "+00:00"))
            if cursor_time.tzinfo is None or cursor_time.utcoffset() is None:
                raise ValueError
            cursor_id = UUID(after_message_id)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Event cursor is invalid.") from None
    pool: Any = request.app.state.db_pool
    async with pool.acquire() as connection:
        if cursor_time is None:
            rows = await connection.fetch(
                """select message.id as message_id,message.conversation_id,message.created_at
                   from chat_turns turn join messages message
                     on message.id=turn.assistant_message_id
                   where turn.initiator_actor='persona'
                     and turn.trigger_type='autonomy_decision'
                     and turn.input_source='internal' and turn.status='complete'
                     and message.role='diana'
                   order by message.created_at desc,message.id desc limit 1"""
            )
            events: list[ProactiveEvent] = []
        else:
            rows = await connection.fetch(
                """select message.id as message_id,message.conversation_id,message.created_at
                   from chat_turns turn join messages message
                     on message.id=turn.assistant_message_id
                   where turn.initiator_actor='persona'
                     and turn.trigger_type='autonomy_decision'
                     and turn.input_source='internal' and turn.status='complete'
                     and message.role='diana'
                     and (message.created_at > $1 or (message.created_at = $1 and message.id > $2))
                   order by message.created_at asc,message.id asc limit $3""",
                cursor_time, cursor_id, limit,
            )
            events = [ProactiveEvent(
                message_id=UUID(str(row["message_id"])),
                conversation_id=UUID(str(row["conversation_id"])),
                persona_id=persona_id,
                created_at=_aware_utc(row["created_at"]),
            ) for row in rows]
        latest = rows[0] if cursor_time is None and rows else None
        if cursor_time is not None and rows:
            latest = rows[-1]
    return ProactiveEventBatch(
        events=events,
        # With no history, the zero cursor avoids a timestamp race: a message
        # created while this first request is in flight must be returned by
        # the next incremental poll rather than falling behind a `now` cursor.
        latest_created_at=_aware_utc(latest["created_at"]) if latest else (
            datetime.min.replace(tzinfo=timezone.utc) if cursor_time is None else cursor_time
        ),
        latest_message_id=UUID(str(latest["message_id"])) if latest else (
            UUID(int=0) if cursor_time is None else cursor_id
        ),
    )


def _aware_utc(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
