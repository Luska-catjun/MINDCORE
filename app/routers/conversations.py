from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Query, status

from app.database.connection import get_pool
from app.schemas.conversations import ConversationCreate, ConversationRead
from app.schemas.messages import MessageRead
from app.services import repository

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post("", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
async def create_conversation(payload: ConversationCreate, pool: asyncpg.Pool = Depends(get_pool)) -> dict:
    return await repository.create_conversation(pool, payload)


@router.get("", response_model=list[ConversationRead])
async def list_conversations(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    pool: asyncpg.Pool = Depends(get_pool),
) -> list[dict]:
    return await repository.list_conversations(pool, limit=limit, offset=offset)


@router.get("/{conversation_id}/messages", response_model=list[MessageRead])
async def list_conversation_messages(
    conversation_id: UUID,
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    latest: bool = Query(default=False),
    pool: asyncpg.Pool = Depends(get_pool),
) -> list[dict]:
    return await repository.list_messages(pool, conversation_id, limit=limit, offset=offset, latest=latest)
