import asyncpg
from fastapi import APIRouter, Depends, Query, status

from app.database.connection import get_pool
from app.schemas.episodes import EpisodeCreate, EpisodeRead
from app.services import repository

router = APIRouter(prefix="/episodes", tags=["episodes"])


@router.post("", response_model=EpisodeRead, status_code=status.HTTP_201_CREATED)
async def create_episode(payload: EpisodeCreate, pool: asyncpg.Pool = Depends(get_pool)) -> dict:
    return await repository.create_episode(pool, payload)


@router.get("", response_model=list[EpisodeRead])
async def list_episodes(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    pool: asyncpg.Pool = Depends(get_pool),
) -> list[dict]:
    return await repository.list_episodes(pool, limit=limit, offset=offset)
