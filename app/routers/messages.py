import logging
from uuid import UUID
import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status

from app.database.connection import get_pool
from app.schemas.messages import MessageCreate, MessageRead
from app.services import repository

router = APIRouter(prefix="/messages", tags=["messages"])
logger = logging.getLogger("diana.messages")


def _database_error_code(exc: Exception) -> str | int | None:
    """Return provider error metadata without relying on a specific driver."""
    return (
        getattr(exc, "sqlite_errorcode", None)
        or getattr(exc, "code", None)
        or getattr(exc, "sqlstate", None)
    )


def _safe_error_message(exc: Exception) -> str:
    """Keep operational logs useful without leaking a database URL or token."""
    return " ".join(str(exc).split())[:300]


@router.post("", response_model=MessageRead, status_code=status.HTTP_201_CREATED)
async def create_message(payload: MessageCreate, pool: asyncpg.Pool = Depends(get_pool)) -> dict:
    return await repository.create_message(pool, payload)


@router.delete("/{message_id}", status_code=status.HTTP_200_OK)
async def delete_message(message_id: UUID, pool: asyncpg.Pool = Depends(get_pool)) -> dict:
    try:
        deleted = await repository.delete_message(pool, message_id)
    except Exception as exc:
        logger.error(
            "MESSAGE_DELETE_DB_ERROR message_id=%s error_type=%s error_code=%s error=%s",
            message_id,
            type(exc).__name__,
            _database_error_code(exc),
            _safe_error_message(exc),
        )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "MESSAGE_DELETE_CONFLICT",
                "message": "Message could not be deleted.",
            },
        ) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Message not found.")
    logger.info("MESSAGE_DELETE_OK message_id=%s", message_id)
    return {"id": str(message_id), "deleted": True}
