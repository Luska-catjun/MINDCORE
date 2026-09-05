from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from app.schemas.common import DianaBaseModel


Weight = float


class EpisodeCreate(DianaBaseModel):
    conversation_id: UUID | None = None
    message_id: UUID | None = None
    title: str | None = Field(default=None, max_length=240)
    content: str = Field(min_length=1)
    source_device: str | None = Field(default=None, max_length=120)
    sequence: int | None = Field(default=None, ge=1)
    importance: Weight = Field(default=0, ge=0, le=1)
    emotional_impact: Weight = Field(default=0, ge=0, le=1)
    valence: Weight = Field(default=0, ge=0, le=1)
    novelty: Weight = Field(default=0, ge=0, le=1)
    confidence: Weight = Field(default=0, ge=0, le=1)
    relationship_impact: Weight = Field(default=0, ge=0, le=1)
    personal_relevance: Weight = Field(default=0, ge=0, le=1)
    recall_frequency: Weight = Field(default=0, ge=0, le=1)
    context_relevance: Weight = Field(default=0, ge=0, le=1)
    memory_strength: Weight = Field(default=0, ge=0, le=1)
    decay: Weight = Field(default=0, ge=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EpisodeRead(EpisodeCreate):
    id: UUID
    timestamp: datetime
    created_at: datetime
