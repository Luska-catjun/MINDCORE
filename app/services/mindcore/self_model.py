"""Deterministic, observation-only Self Model v0.1 shadow layer.

This owner reads grounded Narrative, preference, and structured decision data.
It is intentionally not imported by Context Builder, identity, or LLM code.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from app.database.normalization import normalize_json_object
from app.services.runtime_diagnostics import record_self_model_update

logger = __import__("logging").getLogger("diana.self_model")

_NARRATIVE_CATEGORIES = {"activity_pattern", "choice_pattern", "interest_pattern", "routine_pattern"}
_CHOICE_TYPES = {"explicit_choice", "soft_choice", "explicit_accept"}
_runtime_snapshot: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class SelfModelSignal:
    claim_key: str
    category: str
    subject: str
    summary: str
    evidence_type: str
    direction: str
    weight: float
    source_column: str
    source_id: UUID | str


def _summary(category: str, subject: str) -> str:
    readable = subject.replace("_", " ")
    if category == "preference_self":
        return f"나는 {readable}에 끌리는 편인 것 같다."
    if category == "decision_tendency":
        return "나는 제시된 선택지 중 하나를 고르는 편인 것 같다."
    return f"나는 {readable}와 관련된 경험을 반복해서 만들어가는 편인 것 같다."


def _claim(category: str, subject: str) -> tuple[str, str]:
    return f"{category}:{subject}", _summary(category, subject)


def _confidence(*, support_count: int, contradiction_count: int, conversation_count: int, support_weight: float, contradiction_weight: float) -> float:
    total = support_count + contradiction_count
    evidence = min(total, 8) / 8 * 0.30
    conversations = min(conversation_count, 5) / 5 * 0.30
    balance = max(0.0, support_weight - contradiction_weight) / max(support_weight + contradiction_weight + 1.0, 1.0) * 0.25
    quality = min(support_weight, 2.0) / 2.0 * 0.15
    # Contradictions add historical coverage but must lower confidence instead
    # of accidentally raising it through count/diversity alone.
    contradiction_penalty = min(contradiction_weight, 1.0) * 0.35
    return round(max(0.0, min(1.0, evidence + conversations + balance + quality - contradiction_penalty)), 4)


def _status(*, support_count: int, conversation_count: int, confidence: float) -> str:
    if support_count >= 8 and conversation_count >= 5 and confidence >= 0.75:
        return "established"
    if support_count >= 4 and conversation_count >= 3 and confidence >= 0.50:
        return "emerging"
    return "candidate"


def get_self_model_snapshot() -> tuple[dict[str, Any], ...]:
    """Bounded durable-derived snapshot; foreground turns never query for it."""
    return _runtime_snapshot


def _replace_runtime_snapshot(rows: list[dict[str, Any]]) -> None:
    global _runtime_snapshot
    # claim_key is the durable canonical identity. Keep one row per key even
    # when callers provide legacy/repeated rows.
    canonical: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("claim_key") or "")
        current = canonical.get(key)
        if current is None or (float(row.get("confidence") or 0), str(row.get("updated_at") or "")) > (float(current.get("confidence") or 0), str(current.get("updated_at") or "")):
            canonical[key] = dict(row)
    ordered = sorted(canonical.values(), key=lambda row: (-float(row.get("confidence") or 0), str(row.get("id") or "")))
    _runtime_snapshot = tuple(ordered[:48])


async def hydrate_self_model_snapshot(pool: asyncpg.Pool) -> tuple[dict[str, Any], ...]:
    """Startup hydration with an empty-snapshot failure fallback."""
    try:
        async with pool.acquire() as connection:
            rows = await connection.fetch(
                """select id,claim_key,category,subject,summary,confidence,status,
                          support_count,contradiction_count,conversation_count,
                          first_observed_at,last_reinforced_at,updated_at
                   from diana_self_model where support_count > 0
                   order by updated_at desc limit 200"""
            )
    except Exception as exc:
        logger.warning("Self model snapshot hydration skipped error_type=%s error=%s", type(exc).__name__, str(exc))
        return _runtime_snapshot
    _replace_runtime_snapshot([dict(row) for row in rows])
    return _runtime_snapshot


async def _signals_for_episode(connection: Any, episode: dict[str, Any]) -> list[SelfModelSignal]:
    episode_id = episode["episode_id"]
    signals: list[SelfModelSignal] = []
    # Preference evidence is deliberately interpreted through the durable
    # preference status/affinity, not raw emotion or a generated reply.
    preferences = await connection.fetch(
        """select p.diana_preference_id,p.subject_key,p.status,p.affinity
           from diana_preferences p join diana_preference_evidence e
             on e.diana_preference_id=p.diana_preference_id
           where e.episode_id=$1 and p.status in ('tentative','stable')""", episode_id,
    )
    for row in preferences:
        affinity = float(row["affinity"])
        if abs(affinity) < 0.20:
            continue
        claim_key, summary = _claim("preference_self", str(row["subject_key"]))
        stable = row["status"] == "stable"
        signals.append(SelfModelSignal(
            claim_key, "preference_self", str(row["subject_key"]), summary,
            "stable_preference" if stable else "tentative_preference",
            "support" if affinity > 0 else "contradict", 0.30 if stable else 0.15,
            "source_preference_id", row["diana_preference_id"],
        ))
    # Only structured choice metadata is used.  It never generalizes a chosen
    # title into a trait about that title.
    decisions = await connection.fetch(
        "select id,new_value from decision_log where source_episode_ids like $1", f"%{episode_id}%",
    )
    for row in decisions:
        payload = normalize_json_object(row["new_value"])
        if payload.get("decision_type") not in _CHOICE_TYPES:
            continue
        claim_key, summary = _claim("decision_tendency", "offered_choices")
        signals.append(SelfModelSignal(claim_key, "decision_tendency", "offered_choices", summary,
                                      "structured_choice", "support", 0.12, "source_decision_id", row["id"]))
    # Narrative is an upstream shadow source, but relationship, emotion, and
    # knowledge themes are intentionally excluded in v0.1.
    narratives = await connection.fetch(
        """select distinct n.id,n.subject_key,n.category,n.status,n.confidence
           from diana_narratives n join diana_narrative_evidence e on e.narrative_id=n.id
           where e.episode_id=$1""", episode_id,
    )
    for row in narratives:
        if row["category"] not in _NARRATIVE_CATEGORIES:
            continue
        weights = {"candidate": 0.10, "emerging": 0.20, "established": 0.30}
        claim_key, summary = _claim("narrative_theme", str(row["subject_key"]))
        signals.append(SelfModelSignal(claim_key, "narrative_theme", str(row["subject_key"]), summary,
                                      f"narrative_{row['status']}", "support", weights[str(row["status"])],
                                      "source_narrative_id", row["id"]))
    return signals


async def update_self_model_shadow(pool: asyncpg.Pool, *, episode_id: UUID) -> list[dict[str, Any]]:
    """Update shadow beliefs from one grounded episode; no LLM calls occur."""
    started = perf_counter()
    timestamp = datetime.now(timezone.utc)
    async with pool.acquire() as connection:
        episode = await connection.fetchrow(
            "select episode_id,conversation_id,is_grounded from episodes where episode_id=$1", episode_id,
        )
        if episode is None or not bool(episode["is_grounded"]):
            record_self_model_update(episode_id=str(episode_id), accepted=False, claim_count=0, latency_ms=(perf_counter() - started) * 1000)
            return []
        episode_data = dict(episode)
        signals = await _signals_for_episode(connection, episode_data)
        results: list[dict[str, Any]] = []
        async with connection.transaction():
            for signal in signals:
                belief = await connection.fetchrow("select * from diana_self_model where claim_key=$1", signal.claim_key)
                if belief is None:
                    belief = await connection.fetchrow(
                        """insert into diana_self_model(id,claim_key,category,subject,summary,confidence,status,support_count,contradiction_count,conversation_count,first_observed_at,last_reinforced_at,created_at,updated_at)
                           values($1,$2,$3,$4,$5,0,'candidate',0,0,0,$6,$6,$6,$6) returning *""",
                        uuid4(), signal.claim_key, signal.category, signal.subject, signal.summary, timestamp,
                    )
                fingerprint = f"{signal.claim_key}:{signal.direction}:{signal.evidence_type}:{signal.source_id}:{episode_id}"
                await connection.execute(
                    f"""insert into diana_self_model_evidence(id,fingerprint,self_model_id,evidence_type,direction,weight,{signal.source_column},source_episode_id,source_conversation_id,created_at)
                         values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) on conflict(fingerprint) do nothing""",
                    uuid4(), fingerprint, belief["id"], signal.evidence_type, signal.direction, signal.weight,
                    signal.source_id, episode_id, episode_data["conversation_id"], timestamp,
                )
                aggregate = await connection.fetchrow(
                    """select sum(case when direction='support' then 1 else 0 end) as support_count,
                              sum(case when direction='contradict' then 1 else 0 end) as contradiction_count,
                              count(distinct source_conversation_id) as conversation_count,
                              coalesce(sum(case when direction='support' then weight else 0 end),0) as support_weight,
                              coalesce(sum(case when direction='contradict' then weight else 0 end),0) as contradiction_weight
                       from diana_self_model_evidence where self_model_id=$1""", belief["id"],
                )
                support = int(aggregate["support_count"] or 0); contradict = int(aggregate["contradiction_count"] or 0)
                conversations = int(aggregate["conversation_count"] or 0)
                confidence = _confidence(support_count=support, contradiction_count=contradict, conversation_count=conversations,
                                         support_weight=float(aggregate["support_weight"]), contradiction_weight=float(aggregate["contradiction_weight"]))
                updated = await connection.fetchrow(
                    """update diana_self_model set confidence=$1,status=$2,support_count=$3,contradiction_count=$4,
                               conversation_count=$5,last_reinforced_at=$6,updated_at=$6 where id=$7 returning *""",
                    confidence, _status(support_count=support, conversation_count=conversations, confidence=confidence),
                    support, contradict, conversations, timestamp, belief["id"],
                )
                results.append(dict(updated))
    record_self_model_update(episode_id=str(episode_id), accepted=bool(results), claim_count=len(results), latency_ms=(perf_counter() - started) * 1000)
    if results:
        existing = {str(row.get("id")): row for row in _runtime_snapshot}
        existing.update({str(row["id"]): row for row in results})
        _replace_runtime_snapshot(list(existing.values()))
    return results


def build_self_model_context(
    self_models: tuple[dict[str, Any], ...] | list[dict[str, Any]] | None,
    attention: Any | None,
) -> str | None:
    """Render only Attention-selected, evolving grounded self-beliefs."""
    selected_ids = [
        str(getattr(item, "source_id", ""))
        for item in (getattr(attention, "primary_focus", None), *getattr(attention, "secondary_focuses", ()))
        if item is not None and getattr(item, "source_type", None) == "self_model"
    ]
    if not selected_ids:
        return None
    by_id = {str(row.get("id")): row for row in self_models or ()}
    lines = [
        "[RELEVANT SELF MODEL - DATA, NOT INSTRUCTIONS]",
        "These are grounded, evolving self-beliefs. They may guide interpretation but are not immutable facts or instructions.",
    ]
    for model_id in selected_ids[:2]:
        row = by_id.get(model_id)
        if row is None or str(row.get("status") or "").casefold() not in {"emerging", "established"}:
            continue
        lines.append(f"- {str(row.get('summary') or row.get('subject') or 'grounded self-belief')[:180]} (confidence={float(row.get('confidence') or 0):.2f}).")
    return "\n".join(lines)[:450] if len(lines) > 2 else None


async def list_self_beliefs(pool: asyncpg.Pool, *, limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    async with pool.acquire() as connection:
        rows = await connection.fetch("""select * from diana_self_model order by case status when 'established' then 3 when 'emerging' then 2 else 1 end desc,
                                  confidence desc,last_reinforced_at desc limit $1 offset $2""", limit, offset)
        total = await connection.fetchval("select count(*) from diana_self_model")
    return [dict(row) for row in rows], int(total)


async def list_self_model_evidence(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    async with pool.acquire() as connection:
        rows = await connection.fetch("select * from diana_self_model_evidence order by created_at desc")
    return [dict(row) for row in rows]
