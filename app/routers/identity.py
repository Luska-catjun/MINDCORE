import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.database.connection import get_pool
from app.schemas.identity import IdentityCreate, IdentityRead
from app.services import repository

router = APIRouter(prefix="/identity", tags=["identity"])


@router.get("", response_model=IdentityRead)
async def read_identity(request: Request, pool: asyncpg.Pool = Depends(get_pool)) -> dict:
    identity = await repository.get_identity(pool)
    if identity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Identity is not set.")
    return {
        **identity,
        "display_name": request.app.state.settings.persona_display_name,
    }


@router.post("", response_model=IdentityRead, status_code=status.HTTP_201_CREATED)
async def write_identity(payload: IdentityCreate, request: Request, pool: asyncpg.Pool = Depends(get_pool)) -> dict:
    # The desktop/environment configuration owns the Persona name. The legacy
    # identity record remains responsible only for its descriptive fields.
    configured = payload.model_copy(
        update={"display_name": request.app.state.settings.persona_display_name}
    )
    return await repository.upsert_identity(pool, configured)
