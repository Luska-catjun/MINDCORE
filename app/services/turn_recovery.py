"""Bounded recovery of safe post-cognition work for durable chat turns."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Mapping
from uuid import UUID
from weakref import WeakKeyDictionary

from app.services.error_safety import safe_error_type
from app.services.mindcore.decisions import (
    apply_grounded_decision_execution,
    cancel_active_decision_from_reply,
    link_decision_episode,
)
from app.services.mindcore.goals import (
    apply_grounded_goal_progress_from_event,
    satisfy_story_goals,
)
from app.services.mindcore.internal_state import link_attributions_to_experience
from app.services.mindcore.knowledge import acquire_user_knowledge
from app.services.mindcore.narrative import update_narratives_for_episode
from app.services.mindcore.preferences import update_preference_from_experience
from app.services.mindcore.relationship import update_relationship_from_experience
from app.services.mindcore.self_model import update_self_model_shadow
from app.services.mindcore.snapshot_scope import CognitiveSnapshotScope
from app.services.turn_durability import (
    StageNotClaimed,
    TurnDurability,
    stage_rows_for_recovery,
)


logger = logging.getLogger("diana.turn_recovery")
RECOVERY_BATCH_LIMIT = 10

RecoveryHandler = Callable[[Any], Awaitable[Any]]
_POOL_TURN_LOCKS: WeakKeyDictionary[Any, dict[str, asyncio.Lock]] = WeakKeyDictionary()


async def _no_op(_pool: Any) -> None:
    """Complete a stage whose durable prerequisite proves no work exists."""
    return None


def _identifier(value: Any) -> UUID | str | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except ValueError:
        return str(value)


async def _recovery_inputs(pool: Any, turn_id: UUID | str) -> dict[str, Any] | None:
    async with pool.acquire() as connection:
        turn = await connection.fetchrow("select * from chat_turns where turn_id=$1", turn_id)
        if turn is None or turn["user_message_id"] is None or turn["assistant_message_id"] is None:
            return None
        user = await connection.fetchrow(
            "select id,conversation_id,sequence,content from messages where id=$1",
            turn["user_message_id"],
        )
        assistant = await connection.fetchrow(
            "select id,conversation_id,sequence,content from messages where id=$1",
            turn["assistant_message_id"],
        )
        if user is None or assistant is None:
            return None
        experience = await connection.fetchrow(
            """select * from experiences
               where user_message_id=$1 and assistant_message_id=$2""",
            user["id"],
            assistant["id"],
        )
        episode = await connection.fetchrow(
            """select * from episodes
               where user_message_id=$1 and assistant_message_id=$2
               order by created_at desc limit 1""",
            user["id"],
            assistant["id"],
        )
        decisions = await connection.fetch(
            "select id,new_value from decision_log where conversation_id=$1 order by created_at desc",
            turn["conversation_id"],
        )
        decision = next(
            (
                row
                for row in decisions
                if isinstance(row.get("new_value"), dict)
                and str(row["new_value"].get("user_message_id") or "") == str(user["id"])
                and str(row["new_value"].get("assistant_message_id") or "") == str(assistant["id"])
            ),
            None,
        )
    return {
        "turn": dict(turn),
        "user": dict(user),
        "assistant": dict(assistant),
        "experience": dict(experience) if experience is not None else None,
        "episode": dict(episode) if episode is not None else None,
        "decision": dict(decision) if decision is not None else None,
    }


async def _story_keys_for_message(pool: Any, message_id: UUID | str) -> list[str]:
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """select distinct knowledge.subject_key
               from diana_knowledge knowledge
               join diana_knowledge_facts fact on fact.knowledge_id=knowledge.knowledge_id
               where fact.source_message_id=$1 and knowledge.knowledge_type='story'""",
            message_id,
        )
    return [str(row["subject_key"]) for row in rows]


def _production_handlers(
    inputs: dict[str, Any], snapshot_scope: CognitiveSnapshotScope | None
) -> Mapping[str, RecoveryHandler | None]:
    turn = inputs["turn"]
    user = inputs["user"]
    assistant = inputs["assistant"]
    experience = inputs["experience"]
    episode = inputs["episode"]
    decision = inputs["decision"]
    conversation_id = _identifier(turn["conversation_id"])
    user_id = _identifier(user["id"])
    assistant_id = _identifier(assistant["id"])
    experience_id = _identifier(experience["experience_id"]) if experience else None
    episode_id = _identifier(episode["episode_id"]) if episode else None

    handlers: dict[str, RecoveryHandler | None] = {
        "decision_cancel": (
            _no_op
            if decision is not None
            else lambda stage_pool: cancel_active_decision_from_reply(
                stage_pool, conversation_id, str(assistant["content"])
            )
        ),
        "emotion_attribution_link": (
            (lambda stage_pool: link_attributions_to_experience(
                stage_pool, message_id=user_id, experience_id=experience_id
            ))
            if experience_id is not None else _no_op
        ),
        "relationship": (
            (lambda stage_pool: update_relationship_from_experience(
                stage_pool, experience_id, user_text=str(user["content"]), current_state=None
            ))
            if experience_id is not None else _no_op
        ),
        "preferences": (
            (lambda stage_pool: update_preference_from_experience(
                stage_pool, experience_id, user_id, str(user["content"])
            ))
            if experience_id is not None else _no_op
        ),
        "decision_episode_link": (
            (lambda stage_pool: link_decision_episode(
                stage_pool,
                decision_id=_identifier(decision["id"]),
                episode_id=episode_id,
            ))
            if decision is not None and episode_id is not None else _no_op
        ),
        "knowledge": lambda stage_pool: acquire_user_knowledge(
            stage_pool,
            user_text=str(user["content"]),
            user_message_id=user_id,
            source_episode_id=episode_id,
            episode_is_grounded=True,
            conversation_id=conversation_id,
        ),
        "goal_fulfillment": lambda stage_pool: _recover_goal_fulfillment(
            stage_pool, conversation_id, user_id
        ),
        "decision_execution": (
            _no_op
            if decision is not None
            else lambda stage_pool: apply_grounded_decision_execution(
                stage_pool,
                conversation_id=conversation_id,
                user_text=str(user["content"]),
                episode_id=episode_id,
            )
        ),
        "goal_progress": lambda stage_pool: apply_grounded_goal_progress_from_event(
            stage_pool,
            conversation_id=conversation_id,
            user_text=str(user["content"]),
            source_id=str(episode_id or user_id),
        ),
        "narrative": (
            (lambda stage_pool: update_narratives_for_episode(
                stage_pool, episode_id=episode_id, snapshot_scope=snapshot_scope
            ))
            if episode_id is not None else _no_op
        ),
        "self_model": (
            (lambda stage_pool: update_self_model_shadow(
                stage_pool, episode_id=episode_id, snapshot_scope=snapshot_scope
            ))
            if episode_id is not None else _no_op
        ),
    }
    return handlers


async def _recover_goal_fulfillment(
    pool: Any, conversation_id: UUID | str, user_message_id: UUID | str
) -> int:
    keys = await _story_keys_for_message(pool, user_message_id)
    return await satisfy_story_goals(pool, conversation_id, keys)


async def _resume_turn_unlocked(
    pool: Any,
    turn_id: UUID | str,
    *,
    snapshot_scope: CognitiveSnapshotScope | None = None,
    handlers: Mapping[str, RecoveryHandler | None] | None = None,
) -> dict[str, Any] | None:
    """Resume only automatic stages; never issue a provider request."""
    durability = TurnDurability(pool)
    turn = await durability.get_turn(turn_id)
    if turn is None or turn.get("core_completed_at") is None:
        return turn
    inputs = await _recovery_inputs(pool, turn_id)
    if inputs is None:
        await durability.terminalize_missing_input(turn_id)
        return await durability.get_turn(turn_id)
    recovery_handlers = handlers or _production_handlers(inputs, snapshot_scope)
    stage_status = {
        str(row["stage_name"]): str(row["status"])
        for row in await durability.get_stages(turn_id)
    }
    for row in await stage_rows_for_recovery(durability, turn_id):
        stage_name = str(row["stage_name"])
        handler = recovery_handlers.get(stage_name)
        # A missing dependency is not a successful no-op. Keep it pending so a
        # completed prerequisite or a later manual repair can make it runnable.
        if handler is None:
            continue
        if stage_name in {"narrative", "self_model"} and stage_status.get("episode") != "completed":
            continue
        if stage_name in {"emotion_attribution_link", "relationship", "preferences"} and stage_status.get("experience") != "completed":
            continue
        if stage_name == "decision_episode_link" and (
            stage_status.get("decision") != "completed"
            or stage_status.get("episode") != "completed"
        ):
            continue
        if stage_name == "goal_fulfillment" and stage_status.get("knowledge") != "completed":
            continue
        try:
            await durability.run_stage(turn_id, stage_name, handler)
            stage_status[stage_name] = "completed"
        except StageNotClaimed:
            continue
        except Exception as error:
            logger.warning(
                "TURN_RECOVERY turn=%s stage=%s status=failed category=%s",
                turn_id, stage_name, safe_error_type(error),
            )
    return await durability.get_turn(turn_id)


async def resume_turn(
    pool: Any,
    turn_id: UUID | str,
    *,
    snapshot_scope: CognitiveSnapshotScope | None = None,
    handlers: Mapping[str, RecoveryHandler | None] | None = None,
) -> dict[str, Any] | None:
    """Serialize same-process duplicate recovery while DB claims remain authoritative."""
    locks = _POOL_TURN_LOCKS.setdefault(pool, {})
    lock = locks.setdefault(str(turn_id), asyncio.Lock())
    async with lock:
        return await _resume_turn_unlocked(
            pool, turn_id, snapshot_scope=snapshot_scope, handlers=handlers
        )


async def recover_incomplete_turns(
    pool: Any,
    *,
    snapshot_scope: CognitiveSnapshotScope | None = None,
    limit: int = RECOVERY_BATCH_LIMIT,
) -> int:
    """Process one bounded startup batch without retrying incomplete cores."""
    durability = TurnDurability(pool)
    turn_ids = await durability.incomplete_turn_ids(limit=max(0, min(limit, RECOVERY_BATCH_LIMIT)))
    recovered = 0
    for turn_id in turn_ids:
        try:
            await resume_turn(pool, turn_id, snapshot_scope=snapshot_scope)
            recovered += 1
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                "TURN_RECOVERY turn=%s status=failed category=%s",
                turn_id, safe_error_type(error),
            )
    return recovered
