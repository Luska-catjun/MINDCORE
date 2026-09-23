"""Safe result metadata for one explicit proactive execution."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ProactiveExecutionResult:
    turn_id: UUID
    conversation_id: UUID
    message_id: UUID
    intention_key: str
    status: str
    created_at: datetime
