from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from app.schemas.common import DianaBaseModel


class StateCreate(DianaBaseModel):
    mood: str | None = Field(default=None, max_length=120)
    energy: float | None = Field(default=None, ge=0, le=1)
    focus: str | None = Field(default=None, max_length=240)
    values: dict[str, Any] = Field(default_factory=dict)
    source_device: str | None = Field(default=None, max_length=120)


class StateRead(StateCreate):
    id: UUID | int
    created_at: datetime | None = None
    updated_at: datetime
