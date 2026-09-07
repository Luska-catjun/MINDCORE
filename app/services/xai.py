"""xAI Responses API adapter."""

from app.config import Settings
from app.services.responses_api import generate_memory_candidate_for, generate_reply_for


async def generate_reply(settings: Settings, user_message: str, *, dynamic_context: str | None = None, identity_prompt: str | None = None) -> str:
    return await generate_reply_for("xai", settings, user_message, dynamic_context=dynamic_context, identity_prompt=identity_prompt)


async def generate_memory_candidate(settings: Settings, user_content: str, diana_content: str) -> str:
    return await generate_memory_candidate_for("xai", settings, user_content, diana_content)
