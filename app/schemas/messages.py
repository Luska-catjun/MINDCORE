from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from app.models.enums import MessageRole
from app.schemas.common import DianaBaseModel


class MessageCreate(DianaBaseModel):
    conversation_id: UUID
    role: MessageRole
    content: str = Field(min_length=1)
    source_device: str | None = Field(default=None, max_length=120)
    sequence: int | None = Field(default=None, ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MessageRead(MessageCreate):
    id: UUID
    timestamp: datetime
    created_at: datetime
