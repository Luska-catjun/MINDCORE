"""Typed, durable provenance for a single MindCore turn.

This is intentionally small.  It identifies who initiated a turn and how it
entered the runtime; it never carries message content, display names, prompts,
or provider data.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


class ActorKind(StrEnum):
    USER = "user"
    PERSONA = "persona"
    SYSTEM = "system"


class TurnTrigger(StrEnum):
    USER_MESSAGE = "user_message"
    AUTONOMY_DECISION = "autonomy_decision"
    SYSTEM_EVENT = "system_event"


class TurnInputSource(StrEnum):
    TEXT = "text"
    INTERNAL = "internal"


@dataclass(frozen=True)
class ActorContext:
    """The role that initiates a turn, without identity-bearing data."""

    kind: ActorKind

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", ActorKind(self.kind))


@dataclass(frozen=True)
class TurnContext:
    """Minimal provenance for a turn's initiator, trigger, and input source."""

    initiator: ActorContext
    trigger: TurnTrigger
    input_source: TurnInputSource

    def __post_init__(self) -> None:
        if not isinstance(self.initiator, ActorContext):
            raise TypeError("turn_context_initiator_invalid")
        object.__setattr__(self, "trigger", TurnTrigger(self.trigger))
        object.__setattr__(self, "input_source", TurnInputSource(self.input_source))
        if (
            self.initiator.kind,
            self.trigger,
            self.input_source,
        ) not in _VALID_COMBINATIONS:
            raise ValueError("turn_context_combination_invalid")

    @classmethod
    def user_text(cls) -> "TurnContext":
        """Canonical context for the only currently executable turn path."""
        return cls(
            initiator=ActorContext(ActorKind.USER),
            trigger=TurnTrigger.USER_MESSAGE,
            input_source=TurnInputSource.TEXT,
        )

    @classmethod
    def from_durable(cls, row: Mapping[str, Any]) -> "TurnContext":
        """Reconstruct the authoritative context stored with ``chat_turns``."""
        return cls(
            initiator=ActorContext(ActorKind(str(row["initiator_actor"]))),
            trigger=TurnTrigger(str(row["trigger_type"])),
            input_source=TurnInputSource(str(row["input_source"])),
        )

    def durable_values(self) -> tuple[str, str, str]:
        return (str(self.initiator.kind), str(self.trigger), str(self.input_source))


_VALID_COMBINATIONS = frozenset(
    {
        (ActorKind.USER, TurnTrigger.USER_MESSAGE, TurnInputSource.TEXT),
        # These two are representable for future milestones only.  M1 does not
        # create messages, provider calls, or scheduled work from either path.
        (ActorKind.PERSONA, TurnTrigger.AUTONOMY_DECISION, TurnInputSource.INTERNAL),
        (ActorKind.SYSTEM, TurnTrigger.SYSTEM_EVENT, TurnInputSource.INTERNAL),
    }
)
