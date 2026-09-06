"""Thin HTTP adapter for Diana chat turns."""

import asyncpg
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status

from app.database.connection import get_pool
from app.models.enums import MessageRole
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat_turn_coordinator import ChatTurnCoordinator
from app.services.llm_errors import LLMError


router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse, status_code=status.HTTP_201_CREATED)
async def send_chat_message(
    payload: ChatRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict:
    """Compatibility entry point retained for direct callers and ASGI tests."""
    if payload.role != MessageRole.user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Chat requests must use role='user'.",
        )
    try:
        return await ChatTurnCoordinator(
            pool=pool,
            snapshot_scope=getattr(request.app.state, "cognitive_snapshot_scope", None),
        ).execute(
            payload=payload,
            background_tasks=background_tasks,
            settings=request.app.state.settings,
            identity_prompt=request.app.state.diana_identity_prompt,
        )
    except LLMError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=exc.safe_detail(),
        ) from exc
