from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from app.schemas.common import DianaBaseModel


class RelationshipCreate(DianaBaseModel):
    user_label: str = Field(default="primary_user", max_length=120)
    closeness: float = Field(default=0, ge=0, le=1)
    trust: float = Field(default=0, ge=0, le=1)
    familiarity: float = Field(default=0, ge=0, le=1)
    notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    source_device: str | None = Field(default=None, max_length=120)


class RelationshipRead(RelationshipCreate):
    id: UUID | int
    created_at: datetime | None = None
    updated_at: datetime
