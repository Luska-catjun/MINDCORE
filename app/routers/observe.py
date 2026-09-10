"""Authenticated MindCore observation endpoints and bounded corrections."""

from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.config import Settings
from app.database.connection import get_pool
from app.database.normalization import normalize_json_array, normalize_json_object
from app.services.episode_service import delete_episode
from app.services.memory_service import calculate_effective_memory_strength
from app.services.runtime_diagnostics import snapshot
from app.services import repository
from app.services.mindcore.world_model import get_observed_world_model
from app.services.mindcore.attention import get_last_attention_snapshot
from app.services.mindcore import observation_corrections
from app.services.mindcore.knowledge import apply_knowledge_authority

router = APIRouter(prefix="/observe", tags=["observe"])


class CorrectionText(BaseModel):
    value: str = Field(min_length=1, max_length=4000)


def _correction_error(error: Exception) -> HTTPException:
    if isinstance(error, KeyError):
        return HTTPException(status_code=404, detail="Observation item not found.")
    if isinstance(error, ValueError):
        return HTTPException(status_code=422, detail=str(error))
    return HTTPException(status_code=409, detail="Could not apply this correction. Nothing was changed.")


def _rows(rows: list[asyncpg.Record]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _json_object(value: Any) -> dict[str, Any]:
    """Normalize JSONB and Turso TEXT JSON for Observation projections."""
    return normalize_json_object(value)


def _json_list(value: Any) -> list[Any]:
    return normalize_json_array(value)


def _in_placeholders(values: list[Any]) -> str:
    return ", ".join(f"${index}" for index in range(1, len(values) + 1))


def _timed(payload: dict[str, Any], started_at: float) -> dict[str, Any]:
    payload["query_latency_ms"] = round((perf_counter() - started_at) * 1000, 1)
    return payload


@router.get("/memory")
async def observe_memory(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    sort: Literal["recent", "strongest", "most_recalled"] = "recent",
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict[str, Any]:
    started_at = perf_counter()
    order_by = {
        "recent": "created_at desc",
        "strongest": "memory_strength desc, updated_at desc",
        "most_recalled": "recall_frequency desc, updated_at desc",
    }[sort]
    async with pool.acquire() as connection:
        records = await connection.fetch(
            f"""select memory_id as id, content, importance, memory_strength,
                       recall_frequency, created_at, last_recalled_at, source_episode_id, updated_at
                from memories order by {order_by} limit $1 offset $2""",
            limit,
            offset,
        )
        total = await connection.fetchval("select count(*) from memories")
    items = _rows(records)
    now = datetime.now(timezone.utc)
    for item in items:
        item["effective_strength"] = calculate_effective_memory_strength(
            item["memory_strength"], item["importance"],
            last_recalled_at=item["last_recalled_at"], updated_at=item["updated_at"],
            created_at=item["created_at"], current_time=now,
        )
        item.pop("updated_at", None)
    return _timed({"items": items, "total": total, "limit": limit, "offset": offset, "sort": sort}, started_at)


@router.patch("/memory/{memory_id}")
async def correct_observed_memory(memory_id: UUID, body: CorrectionText, pool: asyncpg.Pool = Depends(get_pool)) -> dict[str, Any]:
    try:
        await observation_corrections.update_memory(pool, memory_id, body.value)
    except Exception as exc:
        raise _correction_error(exc) from exc
    return {"id": str(memory_id), "corrected": True}


@router.delete("/memory/{memory_id}")
async def delete_observed_memory(memory_id: UUID, pool: asyncpg.Pool = Depends(get_pool)) -> dict[str, Any]:
    try:
        await observation_corrections.delete_memory(pool, memory_id)
    except Exception as exc:
        raise _correction_error(exc) from exc
    return {"id": str(memory_id), "deleted": True}

@router.get("/messages")
async def observe_messages(limit: int = Query(default=100, ge=1, le=200), offset: int = Query(default=0, ge=0), conversation_id: str | None = None, pool: asyncpg.Pool = Depends(get_pool)) -> dict[str, Any]:
    started_at=perf_counter()
    rows, total = await repository.list_messages_for_observation(
        pool, conversation_id=conversation_id, limit=limit, offset=offset
    )
    return _timed({"items": rows, "total": total, "limit": limit, "offset": offset}, started_at)


@router.get("/emotion")
async def observe_emotion(
    attribution_limit: int = Query(default=20, ge=1, le=100),
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict[str, Any]:
    started_at = perf_counter()
    async with pool.acquire() as connection:
        state = await connection.fetchrow(
            """select emotion as primary_emotion, emotion_intensity as primary_intensity,
                      emotion_vector, mood_valence, energy, curiosity, stress, updated_at
               from diana_state where id=1"""
        )
        attributions = await connection.fetch(
            """select emotion_attribution_id as id, emotion, delta, resulting_value,
                      cause_type, cause_summary, source_type, source_id,
                      source_experience_id, episode_id, created_at
               from emotion_attributions order by created_at desc limit $1""",
            attribution_limit,
        )
    state_payload = dict(state) if state else None
    if state_payload is not None:
        state_payload["emotion_vector"] = _json_object(state_payload.get("emotion_vector"))
    return _timed({"state": state_payload, "attributions": _rows(attributions)}, started_at)


@router.get("/preferences")
async def observe_preferences(
    evidence_limit: int = Query(default=5, ge=1, le=20),
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict[str, Any]:
    started_at = perf_counter()
    async with pool.acquire() as connection:
        diana_rows = await connection.fetch(
            """select diana_preference_id as id, subject_key as subject, display_name, status, affinity,
                      confidence, evidence_count, positive_evidence, negative_evidence, curiosity_evidence,
                      first_observed_at, last_observed_at, stabilized_at
               from diana_preferences
               order by case status when 'stable' then 3 when 'tentative' then 2 else 1 end desc,
                        last_observed_at desc"""
        )
        user_rows = await connection.fetch(
            """select preference_id as id, subject, value, preference_type, status, confidence,
                      evidence_count, first_seen_at, last_seen_at
               from preferences where owner_type='user' order by last_seen_at desc"""
        )
        evidence_rows = await connection.fetch(
            """select diana_preference_id, signal_type, signal_value, experience_id,
                       episode_id, created_at
                from (
                     select evidence.diana_preference_id, evidence.signal_type, evidence.signal_value,
                            evidence.source_experience_id as experience_id, evidence.episode_id, evidence.created_at,
                            row_number() over (
                                partition by evidence.diana_preference_id
                                order by evidence.created_at desc
                            ) as evidence_rank
                     from diana_preference_evidence as evidence
                     join diana_preferences as preference
                       on preference.diana_preference_id = evidence.diana_preference_id
                ) where evidence_rank <= $1
                order by created_at desc""",
            evidence_limit,
        ) if diana_rows else []
    evidence_by_preference: dict[str, list[dict[str, Any]]] = {}
    for item in _rows(evidence_rows):
        key = str(item.pop("diana_preference_id"))
        evidence_by_preference.setdefault(key, [])
        if len(evidence_by_preference[key]) < evidence_limit:
            evidence_by_preference[key].append(item)
    diana_items = _rows(diana_rows)
    for item in diana_items:
        item["recent_evidence"] = evidence_by_preference.get(str(item["id"]), [])
    return _timed({"diana_preferences": diana_items, "user_preferences": _rows(user_rows)}, started_at)


@router.patch("/preferences/persona/{preference_id}")
async def correct_persona_preference(preference_id: UUID, body: CorrectionText, pool: asyncpg.Pool = Depends(get_pool)) -> dict[str, Any]:
    try:
        await observation_corrections.update_persona_preference(pool, preference_id, body.value)
    except Exception as exc:
        raise _correction_error(exc) from exc
    return {"id": str(preference_id), "corrected": True}


@router.delete("/preferences/persona/{preference_id}")
async def delete_persona_preference(preference_id: UUID, pool: asyncpg.Pool = Depends(get_pool)) -> dict[str, Any]:
    try:
        await observation_corrections.delete_persona_preference(pool, preference_id)
    except Exception as exc:
        raise _correction_error(exc) from exc
    return {"id": str(preference_id), "deleted": True}


@router.get("/episodes")
async def observe_episodes(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict[str, Any]:
    started_at = perf_counter()
    async with pool.acquire() as connection:
        records = await connection.fetch(
            """select e.episode_id, e.conversation_id, e.user_message_id, e.assistant_message_id,
                      e.experience_id, e.episode_type, e.topic_key, e.provenance, e.is_grounded,
                      e.started_at, e.ended_at, e.summary,
                      count(distinct ea.emotion_attribution_id) as emotion_count,
                      count(distinct rl.relationship_log_id) as relationship_change_count,
                      count(distinct dpe.diana_preference_evidence_id) as preference_evidence_count,
                      count(distinct m.memory_id) as memory_count
               from episodes e
               left join emotion_attributions ea on ea.episode_id=e.episode_id
               left join relationship_log rl on rl.episode_id=e.episode_id
               left join diana_preference_evidence dpe on dpe.episode_id=e.episode_id
               left join memories m on m.source_episode_id=e.episode_id
               group by e.episode_id
               order by coalesce(e.started_at, e.created_at) desc
               limit $1 offset $2""",
            limit, offset,
        )
        total = await connection.fetchval("select count(*) from episodes")
    return _timed({"items": _rows(records), "total": total, "limit": limit, "offset": offset}, started_at)


@router.delete("/episodes/{episode_id}")
async def delete_observed_episode(episode_id: UUID, pool: asyncpg.Pool = Depends(get_pool)) -> dict[str, Any]:
    try:
        await delete_episode(pool, episode_id=episode_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Episode not found.") from exc
    return {"id": str(episode_id), "deleted": True}


@router.get("/decisions")
async def observe_decisions(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict[str, Any]:
    started_at = perf_counter()
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """select id, target, new_value, reason, source_episode_ids, status, decision_domain, updated_at, resolved_at, created_at
               from decision_log order by created_at desc limit $1 offset $2""", limit, offset,
        )
        total = await connection.fetchval("select count(*) from decision_log")
    items: list[dict[str, Any]] = []
    for row in _rows(rows):
        payload = _json_object(row.pop("new_value", None))
        episode_ids = _json_list(row.pop("source_episode_ids", None))
        items.append({
            **row, "decision_type": payload.get("decision_type", row.get("target")),
            "chosen": payload.get("chosen"), "options": payload.get("options"), "confidence": payload.get("confidence"),
            "conversation_id": payload.get("conversation_id"), "user_message_id": payload.get("user_message_id"),
            "assistant_message_id": payload.get("assistant_message_id"),
            "episode_ids": episode_ids,
        })
    return _timed({"items": items, "total": total, "limit": limit, "offset": offset}, started_at)


@router.patch("/narratives/{item_id}")
async def correct_observed_narrative(item_id: UUID, body: CorrectionText, request: Request, pool: asyncpg.Pool = Depends(get_pool)) -> dict[str, Any]:
    try:
        await observation_corrections.update_narrative(pool, item_id, body.value, request.app.state.cognitive_snapshot_scope)
    except Exception as exc:
        raise _correction_error(exc) from exc
    return {"id": str(item_id), "corrected": True}


@router.delete("/narratives/{item_id}")
async def delete_observed_narrative(item_id: UUID, request: Request, pool: asyncpg.Pool = Depends(get_pool)) -> dict[str, Any]:
    try:
        await observation_corrections.delete_narrative(pool, item_id, request.app.state.cognitive_snapshot_scope)
    except Exception as exc:
        raise _correction_error(exc) from exc
    return {"id": str(item_id), "deleted": True}


@router.get("/narratives")
async def observe_narratives(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict[str, Any]:
    started_at = perf_counter()
    async with pool.acquire() as connection:
        narratives = await connection.fetch(
            """select * from diana_narratives
               order by case status when 'established' then 3 when 'emerging' then 2 else 1 end desc,
                        confidence desc, last_observed_at desc limit $1 offset $2""", limit, offset,
        )
        total = await connection.fetchval("select count(*) from diana_narratives")
        narrative_ids = [row["id"] for row in narratives]
        evidence = await connection.fetch(
            f"""select id,narrative_id,episode_id,decision_id,preference_evidence_id,emotion_attribution_id,
                       memory_id,relationship_log_id,knowledge_id,evidence_type,signal_value,created_at
                from diana_narrative_evidence
                where narrative_id in ({_in_placeholders(narrative_ids)})
                order by created_at desc""",
            *narrative_ids,
        ) if narrative_ids else []
    by_narrative: dict[str, list[dict[str, Any]]] = {}
    for item in _rows(evidence):
        by_narrative.setdefault(str(item.pop("narrative_id")), []).append(item)
    attention_items = {
        str(item.source_id): item
        for item in get_last_attention_snapshot().items
        if item.source_type == "narrative" and item.source_id is not None
    }
    items = _rows(narratives)
    for item in items:
        item["evidence"] = by_narrative.get(str(item["id"]), [])
        attention = attention_items.get(str(item["id"]))
        item["activation_eligible"] = bool(item.get("evidence_count", 0)) and item.get("status") in {"emerging", "established"}
        item["attention_score"] = attention.score if attention else None
        item["attention_reasons"] = list(attention.reasons) if attention else []
    return _timed({"items": items, "total": total, "limit": limit, "offset": offset}, started_at)


@router.patch("/self-model/{item_id}")
async def correct_observed_self_model(item_id: UUID, body: CorrectionText, request: Request, pool: asyncpg.Pool = Depends(get_pool)) -> dict[str, Any]:
    try:
        await observation_corrections.update_self_model(pool, item_id, body.value, request.app.state.cognitive_snapshot_scope)
    except Exception as exc:
        raise _correction_error(exc) from exc
    return {"id": str(item_id), "corrected": True}


@router.delete("/self-model/{item_id}")
async def delete_observed_self_model(item_id: UUID, request: Request, pool: asyncpg.Pool = Depends(get_pool)) -> dict[str, Any]:
    try:
        await observation_corrections.delete_self_model(pool, item_id, request.app.state.cognitive_snapshot_scope)
    except Exception as exc:
        raise _correction_error(exc) from exc
    return {"id": str(item_id), "deleted": True}


@router.get("/self-model")
async def observe_self_model(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict[str, Any]:
    """Read-only grounded Self Model with current activation metadata."""
    started_at = perf_counter()
    async with pool.acquire() as connection:
        beliefs = await connection.fetch(
            """select * from diana_self_model order by case status when 'established' then 3 when 'emerging' then 2 else 1 end desc,
                      confidence desc,last_reinforced_at desc limit $1 offset $2""", limit, offset,
        )
        total = await connection.fetchval("select count(*) from diana_self_model")
        self_model_ids = [row["id"] for row in beliefs]
        evidence = await connection.fetch(
            f"""select id,self_model_id,evidence_type,direction,weight,source_narrative_id,source_preference_id,
                       source_decision_id,source_episode_id,source_conversation_id,created_at
                from diana_self_model_evidence
                where self_model_id in ({_in_placeholders(self_model_ids)})
                order by created_at desc""",
            *self_model_ids,
        ) if self_model_ids else []
    by_belief: dict[str, list[dict[str, Any]]] = {}
    for item in _rows(evidence):
        by_belief.setdefault(str(item.pop("self_model_id")), []).append(item)
    items = _rows(beliefs)
    attention_items = {
        str(item.source_id): item for item in get_last_attention_snapshot().items
        if item.source_type == "self_model" and item.source_id is not None
    }
    for item in items:
        item["evidence"] = by_belief.get(str(item["id"]), [])
        sources = {
            "narrative" if evidence.get("source_narrative_id") else "preference" if evidence.get("source_preference_id") else "decision" if evidence.get("source_decision_id") else "unknown"
            for evidence in item["evidence"]
        }
        independent = {str(evidence.get("source_episode_id") or evidence.get("source_conversation_id") or evidence["id"]) for evidence in item["evidence"]}
        attention = attention_items.get(str(item["id"]))
        item["activation_eligible"] = item.get("status") in {"emerging", "established"} and bool(item["evidence"])
        item["independent_source_count"] = len(independent)
        item["source_types"] = sorted(sources)
        item["current"] = bool(attention and attention.score > .05)
        item["attention_score"] = attention.score if attention else None
    return _timed({"items": items, "total": total, "limit": limit, "offset": offset}, started_at)


@router.get("/relationship")
async def observe_relationship(
    limit: int = Query(default=20, ge=1, le=100),
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict[str, Any]:
    started_at = perf_counter()
    async with pool.acquire() as connection:
        state = await connection.fetchrow(
            "select familiarity, trust, affection, conflict, updated_at from relationship where id=1"
        )
        logs = await connection.fetch(
            """select relationship_log_id as id, delta, reason, episode_id, created_at
               from relationship_log order by created_at desc limit $1""", limit
        )
    log_payload = _rows(logs)
    for item in log_payload:
        item["delta"] = _json_object(item.get("delta"))
    return _timed({"state": dict(state) if state else None, "logs": log_payload}, started_at)


@router.get("/knowledge")
async def observe_knowledge(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    sort: Literal["recent", "reinforced", "confidence"] = "recent",
    search: str | None = Query(default=None, max_length=80),
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict[str, Any]:
    started_at = perf_counter()
    order_by = {
        "recent": "first_learned_at desc",
        "reinforced": "reinforcement_count desc, last_reinforced_at desc",
        "confidence": "confidence desc, last_reinforced_at desc",
    }[sort]
    pattern = f"%{search.strip()}%" if search and search.strip() else None
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            f"""select knowledge_id as id, subject_key, canonical_name, knowledge_type, summary,
                       confidence, status, source_type, source_id, source_episode_id,
                       first_learned_at, last_reinforced_at, reinforcement_count, learning_session_count
                from diana_knowledge
                where ($1 is null or lower(canonical_name) like lower($1) or lower(summary) like lower($1))
                order by {order_by} limit $2 offset $3""",
            pattern, limit, offset,
        )
        total = await connection.fetchval(
            "select count(*) from diana_knowledge where ($1 is null or lower(canonical_name) like lower($1) or lower(summary) like lower($1))",
            pattern,
        )
        story_ids = [row["id"] for row in rows if row["knowledge_type"] == "story"]
        fact_rows = await connection.fetch(
            """select knowledge_id, knowledge_fact_id as id, fact_text, knowledge_scope, source_type,
                      source_message_id, source_episode_id, confidence, reinforcement_count,
                      contradiction_count, first_learned_at, last_reinforced_at
               from diana_knowledge_facts where knowledge_id in (""" + _in_placeholders(story_ids) + ") order by first_learned_at asc",
            *story_ids,
        ) if story_ids else []
    facts_by_knowledge: dict[str, list[dict[str, Any]]] = {}
    for fact in _rows(fact_rows):
        knowledge_id = str(fact.pop("knowledge_id"))
        facts_by_knowledge.setdefault(knowledge_id, []).append(fact)
    items = _rows(rows)
    for index, item in enumerate(items):
        facts = facts_by_knowledge.get(str(item["id"]), [])
        item["facts"] = facts
        item["fact_count"] = len(facts)
        items[index] = apply_knowledge_authority(item)
    return _timed({"items": items, "total": total, "limit": limit, "offset": offset, "sort": sort}, started_at)


@router.patch("/knowledge/{knowledge_id}")
async def correct_observed_knowledge(knowledge_id: UUID, body: CorrectionText, pool: asyncpg.Pool = Depends(get_pool)) -> dict[str, Any]:
    try:
        await observation_corrections.update_knowledge(pool, knowledge_id, body.value)
    except Exception as exc:
        raise _correction_error(exc) from exc
    return {"id": str(knowledge_id), "corrected": True}


@router.delete("/knowledge/{knowledge_id}")
async def delete_observed_knowledge(knowledge_id: UUID, pool: asyncpg.Pool = Depends(get_pool)) -> dict[str, Any]:
    try:
        await observation_corrections.delete_knowledge(pool, knowledge_id)
    except Exception as exc:
        raise _correction_error(exc) from exc
    return {"id": str(knowledge_id), "deleted": True}


@router.get("/stats")
async def observe_stats(pool: asyncpg.Pool = Depends(get_pool)) -> dict[str, Any]:
    started_at = perf_counter()
    async with pool.acquire() as connection:
        counts = await connection.fetchrow(
            """select
                 (select count(*) from conversations) as conversations,
                 (select count(*) from diana_working_memory_items where status='active') as working_memory_active,
                 (select count(*) from messages) as messages,
                 (select count(*) from memories) as memories,
                 (select count(*) from experiences) as experiences,
                 (select count(*) from episodes) as episodes,
                 (select count(*) from decision_log) as decisions,
                 (select count(*) from diana_narratives) as narratives_total,
                 (select count(*) from diana_narratives where status='candidate') as narratives_candidate,
                 (select count(*) from diana_narratives where status='emerging') as narratives_emerging,
                 (select count(*) from diana_narratives where status='established') as narratives_established,
                 (select count(*) from diana_narrative_evidence) as narrative_evidence,
                 (select count(*) from diana_self_model) as self_model_total,
                 (select count(*) from diana_self_model where status='candidate') as self_model_candidate,
                 (select count(*) from diana_self_model where status='emerging') as self_model_emerging,
                 (select count(*) from diana_self_model where status='established') as self_model_established,
                 (select count(*) from diana_self_model_evidence) as self_model_evidence,
                 (select count(*) from emotion_attributions) as emotion_attributions,
                 (select count(*) from diana_preferences) as diana_preferences,
                 (select count(*) from diana_preference_evidence) as diana_preference_evidence,
                 (select count(*) from preferences where owner_type='user') as user_preferences,
                 (select count(*) from relationship_log) as relationship_logs,
                 (select count(*) from diana_knowledge) as knowledge_total,
                 (select count(*) from diana_knowledge where status='introduced') as knowledge_introduced,
                 (select count(*) from diana_knowledge where status='known') as knowledge_known,
                 (select count(*) from diana_knowledge where status='well_known') as knowledge_well_known,
                 (select count(*) from diana_preferences where status='stable') as stable_diana_preferences,
                 (select count(*) from diana_preferences where status='tentative') as tentative_diana_preferences,
                 (select count(*) from diana_preferences where status='curious') as curious_diana_preferences,
                 (select count(*) from episodes where is_grounded) as grounded_episodes,
                 (select count(*) from episodes where not is_grounded) as hypothetical_episodes"""
        )
    return _timed({"counts": dict(counts)}, started_at)


@router.get("/goals-needs")
async def observe_goals_needs(pool: asyncpg.Pool = Depends(get_pool)) -> dict[str, Any]:
    """Read-only motivational state for the future Intention layer."""
    started_at = perf_counter()
    async with pool.acquire() as connection:
        needs = await connection.fetch("select need_key,value,baseline,updated_at,last_triggered_at from diana_needs order by need_key")
        goals = await connection.fetch("""select id,summary,origin_need,priority,status,progress,confidence,conversation_id,source_type,source_id,created_at,updated_at,expires_at
            from diana_goals order by priority desc, updated_at desc limit 50""")
        events = await connection.fetch("""select need_key,delta,before_value,after_value,reason,source_type,source_id,conversation_id,created_at
            from diana_need_events order by created_at desc limit 20""")
    return _timed({"needs": _rows(needs), "goals": _rows(goals), "events": _rows(events)}, started_at)

@router.get("/intentions")
async def observe_intentions(limit: int = Query(default=50, ge=1, le=100), pool: asyncpg.Pool = Depends(get_pool)) -> dict[str, Any]:
    started_at = perf_counter()
    async with pool.acquire() as connection:
        rows = await connection.fetch("""select id,conversation_id,user_message_id,assistant_message_id,action,target,reason_code,confidence,source_refs,constraints,created_at
            from diana_response_intentions order by created_at desc limit $1""", limit)
    items = _rows(rows)
    for item in items:
        # These fields are JSON TEXT in Turso and native JSON in PostgreSQL.
        # Decode only this table's explicitly typed columns.
        item["source_refs"] = _json_object(item.get("source_refs"))
        item["constraints"] = _json_list(item.get("constraints"))
    return _timed({"items": items, "total": len(items), "limit": limit, "offset": 0}, started_at)


@router.get("/debug")
async def observe_debug(request: Request) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    from app.services.llm import provider_model

    diagnostics = snapshot()
    attention = get_last_attention_snapshot()
    return {
        "app_env": settings.environment,
        "primary_llm_provider": settings.llm_provider,
        "fallback_provider": settings.llm_fallback_provider,
        "active_model": provider_model(settings, settings.llm_provider),
        "fallback_model": provider_model(settings, settings.llm_fallback_provider) if settings.llm_fallback_provider else None,
        "diana_timezone": settings.diana_timezone,
        "identity_prompt_chars": len(request.app.state.diana_identity_prompt),
        "attention": {
            "primary": vars(attention.primary_focus) if attention.primary_focus else None,
            "secondary": [vars(item) for item in attention.secondary_focuses],
            "items": [vars(item) for item in attention.items],
            "latency_ms": attention.latency_ms,
            "storage": "request_scoped_in_memory",
        },
        **diagnostics,
    }


@router.get("/world-model")
async def observe_world_model(request: Request) -> dict[str, Any]:
    """Read-only ephemeral current-world state; it is never durable knowledge."""
    settings: Settings = request.app.state.settings
    state = get_observed_world_model(timezone_name=settings.diana_timezone)
    def fact(item: Any) -> dict[str, Any]:
        return {
            "key": item.key, "value": item.value, "source": item.source,
            "confidence": item.confidence, "observed_at": item.observed_at,
            "expires_at": item.expires_at, "status": item.status, "scope": item.scope,
            "source_id": item.source_id,
        }
    weather = fact(state.weather) if state.weather else {"key": "weather", "value": "unknown", "status": "unknown"}
    return {
        "temporal_facts": [fact(item) for item in state.temporal_facts],
        "weather": weather,
        "current_user_facts": [fact(item) for item in state.user_facts],
        "hypotheses": [fact(item) for item in state.hypotheses],
        "updated_at": state.updated_at,
        "storage": "ephemeral",
    }
