import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status

from app.database.connection import get_pool
from app.schemas.state import StateCreate, StateRead
from app.services import repository

router = APIRouter(prefix="/state", tags=["state"])


@router.get("", response_model=StateRead)
async def read_state(pool: asyncpg.Pool = Depends(get_pool)) -> dict:
    state = await repository.get_state(pool)
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="State is not set.")
    return state


@router.post("", response_model=StateRead, status_code=status.HTTP_201_CREATED)
async def write_state(payload: StateCreate, pool: asyncpg.Pool = Depends(get_pool)) -> dict:
    return await repository.upsert_state(pool, payload)
