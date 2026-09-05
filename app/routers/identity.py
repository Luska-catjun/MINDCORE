import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status

from app.database.connection import get_pool
from app.schemas.identity import IdentityCreate, IdentityRead
from app.services import repository

router = APIRouter(prefix="/identity", tags=["identity"])


@router.get("", response_model=IdentityRead)
async def read_identity(pool: asyncpg.Pool = Depends(get_pool)) -> dict:
    identity = await repository.get_identity(pool)
    if identity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Identity is not set.")
    return identity


@router.post("", response_model=IdentityRead, status_code=status.HTTP_201_CREATED)
async def write_identity(payload: IdentityCreate, pool: asyncpg.Pool = Depends(get_pool)) -> dict:
    return await repository.upsert_identity(pool, payload)
