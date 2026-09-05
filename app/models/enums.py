from enum import StrEnum


class MessageRole(StrEnum):
    user = "user"
    diana = "diana"
    system = "system"
    tool = "tool"


class ConversationStatus(StrEnum):
    active = "active"
    archived = "archived"
