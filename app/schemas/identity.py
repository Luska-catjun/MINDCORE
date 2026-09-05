from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from app.schemas.common import DianaBaseModel


class IdentityCreate(DianaBaseModel):
    display_name: str = Field(default="Persona", max_length=120)
    description: str | None = None
    traits: dict[str, Any] = Field(default_factory=dict)
    system_notes: dict[str, Any] = Field(default_factory=dict)


class IdentityRead(IdentityCreate):
    id: UUID
    created_at: datetime
    updated_at: datetime
