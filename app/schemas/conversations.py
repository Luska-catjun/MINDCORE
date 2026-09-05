from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from app.models.enums import ConversationStatus
from app.schemas.common import DianaBaseModel


class ConversationCreate(DianaBaseModel):
    title: str | None = Field(default=None, max_length=240)
    source_device: str | None = Field(default=None, max_length=120)
    status: ConversationStatus = ConversationStatus.active
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConversationRead(ConversationCreate):
    id: UUID
    started_at: datetime
    last_message_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
