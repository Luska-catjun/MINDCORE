import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.database.connection import get_pool
from app.schemas.relationship import RelationshipCreate, RelationshipRead
from app.services import repository

router = APIRouter(prefix="/relationship", tags=["relationship"])


@router.get("", response_model=RelationshipRead)
async def read_relationship(
    user_label: str = Query(default="primary_user", max_length=120),
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict:
    relationship = await repository.get_relationship(pool, user_label=user_label)
    if relationship is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Relationship is not set.")
    return relationship


@router.post("", response_model=RelationshipRead, status_code=status.HTTP_201_CREATED)
async def write_relationship(payload: RelationshipCreate, pool: asyncpg.Pool = Depends(get_pool)) -> dict:
    return await repository.upsert_relationship(pool, payload)
