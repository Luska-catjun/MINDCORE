"""Deterministic formation of Diana's own preferences from her state evidence."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

import asyncpg

logger = logging.getLogger("diana.diana_preferences")

TENTATIVE_MIN_EVIDENCE = 3
TENTATIVE_MIN_CONVERSATIONS = 2
TENTATIVE_MIN_CONFIDENCE = 0.50
TENTATIVE_MIN_AFFINITY = 0.20
STABLE_MIN_EVIDENCE = 6
STABLE_MIN_CONVERSATIONS = 4
STABLE_MIN_CONFIDENCE = 0.70
STABLE_MIN_AFFINITY = 0.45
MAX_CONTEXT_PREFERENCES = 8

# v0.1 deliberately recognizes only a small set of reusable concrete subjects.
SUBJECTS = (
    ("overwatch", "Overwatch", ("오버워치", "overwatch")),
    ("jasmine_tea", "jasmine tea", ("자스민차", "자스민 차", "jasmine tea")),
    ("black_tea", "black tea", ("홍차", "black tea")),
    ("horror_games", "horror games", ("공포 게임", "horror game", "horror games")),
    ("stargazing", "stargazing", ("별 보기", "별보", "stargazing")),
    ("drawing", "drawing", ("그림 그리기", "그림그리기", "drawing")),
)


@dataclass(frozen=True)
class DianaPreferenceSignal:
    signal_type: str
    value: float
    attribution_id: UUID | None


def identify_subject(user_text: str) -> tuple[str, str] | None:
    text = " ".join(user_text.casefold().split())
    for subject_key, display_name, markers in SUBJECTS:
        if any(marker in text for marker in markers):
            return subject_key, display_name
    if any(marker in text for marker in ("동화", "이야기 읽", "읽어줘", "읽어 줄")):
        return "story_reading", "story reading"
    if "게임" in text:
        return "game_playing", "playing games"
    return None


def _decision_subject(decision: Any) -> tuple[str, str] | None:
    if decision is None or getattr(decision, "decision_type", "") not in {"explicit_choice", "soft_choice", "explicit_accept"}:
        return None
    chosen = str(getattr(decision, "chosen", "")).strip()
    if chosen == "accept":
        return "story_reading", "story reading"
    if not chosen or chosen == "reject":
        return None
    key = hashlib.sha1(chosen.casefold().encode()).hexdigest()[:12]
    return f"choice_{key}", chosen


def _signal_from_attribution(attribution: dict[str, Any]) -> DianaPreferenceSignal | None:
    emotion = str(attribution.get("emotion", ""))
    delta = float(attribution.get("delta") or 0.0)
    attribution_id = attribution.get("emotion_attribution_id")
    if not isinstance(attribution_id, UUID) or delta <= 0:
        return None
    if emotion in {"joy", "delight", "amusement", "excitement", "comfort"}:
        return DianaPreferenceSignal("positive", 0.22, attribution_id)
    if emotion in {"interest", "curiosity"}:
        return DianaPreferenceSignal("curiosity", 0.12, attribution_id)
    if emotion in {"frustration", "sadness", "concern", "disappointment", "anger"}:
        return DianaPreferenceSignal("negative", -0.16, attribution_id)
    return None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _calculate_confidence(evidence_count: int, conversation_count: int, affinity: float) -> float:
    # Count, independent conversations, and consistent direction all matter.
    evidence_component = min(evidence_count, STABLE_MIN_EVIDENCE) / STABLE_MIN_EVIDENCE * 0.60
    diversity_component = min(conversation_count, STABLE_MIN_CONVERSATIONS) / STABLE_MIN_CONVERSATIONS * 0.25
    direction_component = min(abs(affinity) / 0.72, 1.0) * 0.15
    return round(_clamp(evidence_component + diversity_component + direction_component, 0.0, 1.0), 4)


def _status_for(*, evidence_count: int, conversation_count: int, affinity: float, confidence: float) -> str:
    if (
        evidence_count >= STABLE_MIN_EVIDENCE
        and conversation_count >= STABLE_MIN_CONVERSATIONS
        and abs(affinity) >= STABLE_MIN_AFFINITY
        and confidence >= STABLE_MIN_CONFIDENCE
    ):
        return "stable"
    if (
        evidence_count >= TENTATIVE_MIN_EVIDENCE
        and conversation_count >= TENTATIVE_MIN_CONVERSATIONS
        and abs(affinity) >= TENTATIVE_MIN_AFFINITY
        and confidence >= TENTATIVE_MIN_CONFIDENCE
    ):
        return "tentative"
    return "curious"


async def update_diana_preference_from_experience(
    pool: asyncpg.Pool,
    *,
    experience_id: UUID,
    message_id: UUID,
    user_text: str,
    decision: Any | None = None,
    message_attributions: tuple[dict[str, Any], ...] | list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Record at most one state-derived signal from one immutable experience."""
    subject = identify_subject(user_text) or _decision_subject(decision)
    if subject is None:
        return None
    subject_key, display_name = subject
    started = perf_counter()
    timestamp = datetime.now(timezone.utc)

    async with pool.acquire() as connection:
        async with connection.transaction():
            # The coordinator can supply exactly the message attributions it
            # just durably created and linked to this Experience.  They are
            # equivalent to this query for a normal turn; callers without a
            # request-scoped result retain the durable DB read.
            if message_attributions is None:
                attributions = await connection.fetch(
                    """
                    select emotion_attribution_id, emotion, delta
                    from emotion_attributions
                    where source_experience_id = $1 and source_type = 'message'
                    order by created_at asc
                    """,
                    experience_id,
                )
            else:
                attributions = message_attributions
            signals = [signal for item in attributions if (signal := _signal_from_attribution(dict(item))) is not None]
            if decision is not None and getattr(decision, "decision_type", "") in {"explicit_choice", "soft_choice", "explicit_accept"}:
                strength = 0.14 if decision.decision_type == "explicit_choice" else 0.12
                if decision.decision_type == "explicit_accept":
                    strength = 0.12
                signals.append(DianaPreferenceSignal("decision_choice", strength, None))
            if not signals:
                logger.info("DianaPreference update skipped subject=%s reason=no_supported_emotion_signal", subject_key)
                return None

            # A single event gets one signal even if a future state pipeline
            # emits multiple compatible rows for that event.
            signal = max(signals, key=lambda item: abs(item.value))
            preference = await connection.fetchrow(
                "select * from diana_preferences where subject_key=$1",
                subject_key,
            )
            if preference is None:
                preference = await connection.fetchrow(
                    """
                    insert into diana_preferences (
                        diana_preference_id, subject_key, display_name, status, affinity, confidence,
                        evidence_count, positive_evidence, negative_evidence, curiosity_evidence,
                        first_observed_at, last_observed_at, created_at, updated_at
                    ) values ($1, $2, $3, 'curious', 0, 0, 0, 0, 0, 0, $4, $4, $4, $4) returning *
                    """,
                    uuid4(), subject_key, display_name, timestamp,
                )

            inserted = await connection.fetchrow(
                """
                insert into diana_preference_evidence (
                    diana_preference_evidence_id, diana_preference_id, subject_key, signal_type, signal_value,
                    source_emotion_attribution_id, source_experience_id, source_message_id, created_at
                )
                select $1,$2,$3,$4,$5,$6,$7,$8,$9
                where not exists (
                    select 1 from diana_preference_evidence
                    where source_experience_id=$7 and diana_preference_id=$2
                )
                returning diana_preference_evidence_id
                """,
                uuid4(), preference["diana_preference_id"], subject_key, signal.signal_type, signal.value,
                signal.attribution_id, experience_id, message_id, timestamp,
            )
            if inserted is None:
                logger.info("DianaPreference update skipped subject=%s reason=duplicate_experience", subject_key)
                return dict(preference)

            positive = int(preference["positive_evidence"]) + (1 if signal.signal_type == "positive" else 0)
            negative = int(preference["negative_evidence"]) + (1 if signal.signal_type == "negative" else 0)
            curiosity = int(preference["curiosity_evidence"]) + (1 if signal.signal_type == "curiosity" else 0)
            evidence_count = int(preference["evidence_count"]) + 1
            affinity = round(_clamp(float(preference["affinity"]) + signal.value, -1.0, 1.0), 4)

            conversation_count = await connection.fetchval(
                """
                select count(distinct e.conversation_id)
                from diana_preference_evidence evidence
                join experiences e on e.experience_id = evidence.source_experience_id
                where evidence.diana_preference_id=$1
                """,
                preference["diana_preference_id"],
            )
            confidence = _calculate_confidence(evidence_count, int(conversation_count), affinity)
            status_before = str(preference["status"])
            status_after = _status_for(
                evidence_count=evidence_count,
                conversation_count=int(conversation_count),
                affinity=affinity,
                confidence=confidence,
            )
            updated = await connection.fetchrow(
                """
                update diana_preferences
                set display_name=$1, status=$2, affinity=$3, confidence=$4, evidence_count=$5,
                    positive_evidence=$6, negative_evidence=$7, curiosity_evidence=$8,
                    last_observed_at=$9, stabilized_at=case when $2='stable' and stabilized_at is null then $9 else stabilized_at end,
                    updated_at=$9
                where diana_preference_id=$10 returning *
                """,
                display_name, status_after, affinity, confidence, evidence_count,
                positive, negative, curiosity, timestamp, preference["diana_preference_id"],
            )

    result = dict(updated)
    logger.info(
        "DianaPreference update subject=%s status_before=%s status_after=%s evidence=%s signal=%s latency_ms=%.2f",
        subject_key, status_before, status_after, evidence_count, signal.signal_type,
        (perf_counter() - started) * 1000,
    )
    return result


async def get_diana_preferences(pool: asyncpg.Pool, *, limit: int = MAX_CONTEXT_PREFERENCES) -> list[dict[str, Any]]:
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            select * from diana_preferences where status in ('stable', 'tentative')
            order by case status when 'stable' then 3 when 'tentative' then 2 else 1 end desc,
                confidence desc, last_observed_at desc
            limit $1
            """,
            limit,
        )
    return [dict(row) for row in rows]


def build_diana_preference_context(preferences: list[dict[str, Any]]) -> str | None:
    if not preferences:
        return None
    lines = ["[PERSONA PREFERENCE FORMATION - DATA, NOT INSTRUCTIONS]"]
    for preference in preferences:
        name = str(preference["display_name"])
        status = str(preference["status"])
        confidence = float(preference["confidence"])
        affinity = float(preference["affinity"])
        if status == "stable":
            meaning = "stable positive preference" if affinity > 0 else "stable negative preference"
        elif status == "tentative":
            meaning = "tentative preference, not stable"
        else:
            meaning = "curious only, not a preference"
        lines.append(f"- {name}: status={status}; confidence={confidence:.2f}; {meaning}.")
    return "\n".join(lines)[:700]


async def attach_episode_provenance(connection: Any, *, experience_id: UUID, episode_id: UUID) -> None:
    """Link preference evidence to an Episode; preference owns this mutation."""
    await connection.execute(
        "update diana_preference_evidence set episode_id=$1 where source_experience_id=$2",
        episode_id,
        experience_id,
    )


async def detach_episode_provenance(connection: Any, *, episode_id: UUID) -> None:
    """Keep preference evidence durable when its Episode is removed."""
    await connection.execute(
        "update diana_preference_evidence set episode_id=null where episode_id=$1", episode_id
    )
