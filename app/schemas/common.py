from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DianaBaseModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class MetadataMixin(BaseModel):
    metadata: dict[str, Any] = Field(default_factory=dict)


class Timestamped(DianaBaseModel):
    id: UUID
    created_at: datetime
    updated_at: datetime | None = None
