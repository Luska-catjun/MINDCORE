"""Own Episode promotion, merge eligibility, finalization, and deletion.

Episodes are finalized autobiographical records.  Only ``story_reading`` may
merge into the same topic/session for a short window.  Durable child records
remain owned by their services; this module delegates provenance attachment and
detachment to those owners.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import TYPE_CHECKING, Iterable
from uuid import UUID, uuid4

import asyncpg

from app.database.normalization import normalize_utc_datetime
from app.services import memory_service
from app.services.mindcore import diana_preferences, internal_state, knowledge, relationship
from app.services.mindcore.diana_preferences import identify_subject
from app.services.runtime_diagnostics import record_episode_promotion

if TYPE_CHECKING:
    from app.services.mindcore.decisions import DecisionCandidate

logger = logging.getLogger("diana.episodes")
STORY_MERGE_WINDOW = timedelta(minutes=30)

_TRIVIAL_TURNS = {
    "ㅇㅇ", "ㅇ", "응", "네", "넵", "ok", "okay", "ㅋㅋ", "ㅋㅋㅋ", "ㅎㅎ", "ㅎㅎㅎ",
}
_GREETING_OR_FILLER = (
    "안녕", "좋은 아침", "좋은 저녁", "잘 자", "그다음", "계속 말해줘", "계속 얘기해",
    "그래", "맞아", "알겠어", "고마워",
)
_EMOTIONAL_MARKERS = ("좋아", "행복", "기뻐", "신나", "힘들", "슬퍼", "짜증", "걱정", "불안", "무서")
_RELATIONSHIP_MARKERS = ("고마워", "미안", "좋아해", "싫어", "친구", "약속")
_PERSONAL_MARKERS = ("다이애나", "너는", "너 ", "좋아하는", "기억", "나이", "어떤 애", "나는", "난 ", "내가", "오늘", "어제")
_STORY_MARKERS = ("동화", "이야기", "옛날", "읽어줄", "읽어 줄", "주인공")
_STORY_TITLE = re.compile(r"(?P<title>[0-9a-z가-힣][0-9a-z가-힣\s]{1,48}?)(?:\s*(?:동화|이야기))", re.IGNORECASE)


@dataclass(frozen=True)
class EpisodeCandidate:
    content: str
    importance: float
    emotional_impact: float
    personal_relevance: float
    relationship_impact: float
    novelty: float
    confidence: float
    memory_strength: float
    decay: float
    promotion_score: float
    promotion_reasons: tuple[str, ...]


def _contains_any(text: str, markers: Iterable[str]) -> bool:
    return any(marker in text for marker in markers)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, round(value, 4)))


def story_topic_key(user_text: str) -> str | None:
    """Use only a user-provided story title; never infer pretrained identity."""
    match = _STORY_TITLE.search(" ".join(user_text.split()))
    if match is None:
        return None
    title = " ".join(match.group("title").casefold().split())
    # Remove only generic request framing, keeping the title's own words.
    title = re.sub(r"^(?:그|이|저|어떤|무슨)\s+", "", title).strip()
    return f"story:{title.replace(' ', '_')}" if title else None


def _as_utc(value: datetime | str | None) -> datetime | None:
    value = normalize_utc_datetime(value)
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def is_merge_eligible(
    *,
    prior: dict | None,
    conversation_id: UUID,
    episode_type: str,
    topic_key: str | None,
    user_message_id: UUID | None,
    assistant_message_id: UUID | None,
    experience_id: UUID | None,
    timestamp: datetime,
) -> bool:
    """Return whether one finalized story Episode may extend a prior session.

    The contract is deliberately narrow: same conversation/type/normalized
    topic, complete message/Experience provenance on both sides, and at most
    thirty minutes of elapsed UTC time.
    """
    if (
        prior is None
        or episode_type != "story_reading"
        or user_message_id is None
        or assistant_message_id is None
        or experience_id is None
        or prior.get("conversation_id") != conversation_id
        or prior.get("episode_type") != "story_reading"
        or prior.get("topic_key") != topic_key
        or prior.get("user_message_id") is None
        or prior.get("assistant_message_id") is None
        or prior.get("experience_id") is None
    ):
        return False
    ended_at = _as_utc(prior.get("ended_at"))
    return ended_at is not None and timedelta(0) <= timestamp - ended_at <= STORY_MERGE_WINDOW


def build_episode_candidate(
    user_text: str,
    diana_text: str,
    *,
    decision: "DecisionCandidate | None" = None,
    memory_id: UUID | None = None,
) -> EpisodeCandidate | None:
    """Promote only a meaningful completed turn to an episode, without an LLM call."""
    normalized = " ".join(user_text.casefold().split()).strip(".!? ")
    if not normalized or normalized in _TRIVIAL_TURNS or normalized in _GREETING_OR_FILLER:
        record_episode_promotion(status="rejected", score=0.0, reasons=["trivial_or_filler"])
        return None

    reasons: list[str] = []
    score = 0.0
    story_turn = _contains_any(normalized, _STORY_MARKERS)
    emotional_impact = 0.35 if _contains_any(normalized, _EMOTIONAL_MARKERS) else 0.05
    relationship_impact = 0.30 if _contains_any(normalized, _RELATIONSHIP_MARKERS) else 0.0
    personal_relevance = 0.45 if _contains_any(normalized, _PERSONAL_MARKERS) else 0.0
    if decision is not None:
        score += 0.40
        reasons.append("decision")
    if story_turn and (len(normalized) >= 24 or "읽어" in normalized):
        score += 0.35
        reasons.append("story_content")
    if personal_relevance and len(normalized) >= 20:
        score += 0.30
        reasons.append("personal_context")
    if emotional_impact >= 0.35 or relationship_impact >= 0.30:
        score += 0.25
        reasons.append("emotion_or_relationship")
    if memory_id is not None:
        score += 0.25
        reasons.append("memory_created")
    if len(normalized) >= 160:
        score += 0.20
        reasons.append("detailed_turn")
    if len(normalized) < 12 and not reasons:
        score -= 0.20
    score = _clamp(score)
    if score < 0.35:
        record_episode_promotion(status="rejected", score=score, reasons=reasons or ["below_meaningful_threshold"])
        return None

    importance = _clamp(0.18 + score * 0.52)
    novelty = _clamp(0.16 + (0.24 if len(normalized) >= 24 else 0.08) + (0.12 if story_turn else 0.0))
    memory_strength = _clamp(0.12 + importance * 0.55 + emotional_impact * 0.12 + personal_relevance * 0.10)

    return EpisodeCandidate(
        content=f"User: {user_text.strip()}\nDiana: {diana_text.strip()}",
        importance=importance,
        emotional_impact=emotional_impact,
        personal_relevance=personal_relevance,
        relationship_impact=relationship_impact,
        novelty=novelty,
        confidence=_clamp(0.50 + score * 0.42),
        memory_strength=memory_strength,
        decay=0.0,
        promotion_score=score,
        promotion_reasons=tuple(reasons),
    )


def classify_episode_provenance(user_text: str) -> tuple[str, str, bool]:
    text = " ".join(user_text.casefold().split())
    hypothetical_markers = ("만약", "나중에", "라면", "상상", "뭐 하고 싶", "어떻게 할 것 같")
    if any(marker in text for marker in hypothetical_markers) or ("하면" in text and "싶" in text):
        return "hypothetical", "hypothetical", False
    if "귀엽" in text or "칭찬" in text:
        return "praise", "grounded_event", True
    if "?" in text or any(marker in text for marker in ("왜", "어떻게", "뭐야")):
        return "question", "grounded_event", True
    return "conversation", "grounded_event", True


async def finalize_episode_linkage(
    pool: asyncpg.Pool,
    *,
    conversation_id: UUID,
    user_message_id: UUID,
    assistant_message_id: UUID,
    experience_id: UUID | None,
    sequence: int,
    source_device: str | None,
    user_text: str,
    diana_text: str,
    memory_id: UUID | None = None,
    decision: "DecisionCandidate | None" = None,
) -> dict | None:
    """Persist an Episode after best-effort downstream work.

    ``experience_id`` is intentionally optional: Experience write failure must
    not prevent Episode finalization.
    """
    candidate = build_episode_candidate(user_text, diana_text, decision=decision, memory_id=memory_id)
    if candidate is None:
        return None
    episode_type, provenance, is_grounded = classify_episode_provenance(user_text)
    if _contains_any(" ".join(user_text.casefold().split()), _STORY_MARKERS):
        episode_type = "story_reading"
    subject = identify_subject(user_text)
    topic_key = story_topic_key(user_text) if episode_type == "story_reading" else (subject[0] if subject else None)
    started = perf_counter()
    timestamp = datetime.now(timezone.utc)
    async with pool.acquire() as connection:
        async with connection.transaction():
            if episode_type != "story_reading":
                # ``user_message_id`` has a partial unique index in the production
                # schema.  Let that constraint be the duplicate authority instead
                # of adding a remote pre-read on every ordinary Episode.  Targetless
                # SQLite UPSERT is intentional: it also covers the partial index.
                row = await connection.fetchrow(
                    """
                    insert into episodes (
                        episode_id, conversation_id, user_message_id, assistant_message_id, experience_id,
                        sequence, summary, source_device, importance, emotional_impact,
                        personal_relevance, relationship_impact, novelty, confidence,
                        recall_frequency, memory_strength, decay, created_at, started_at, ended_at,
                        episode_type, topic_key, provenance, is_grounded, updated_at
                    ) values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,0,$15,$16,$17,$17,$17,$18,$19,$20,$21,$17)
                    on conflict do update set
                        assistant_message_id=excluded.assistant_message_id,
                        experience_id=excluded.experience_id,
                        ended_at=excluded.ended_at,
                        updated_at=excluded.updated_at
                    returning episode_id
                    """,
                    uuid4(), conversation_id, user_message_id, assistant_message_id, experience_id, sequence,
                    candidate.content, source_device or "unknown", candidate.importance,
                    candidate.emotional_impact, candidate.personal_relevance, candidate.relationship_impact,
                    candidate.novelty, candidate.confidence, candidate.memory_strength, candidate.decay,
                    timestamp, episode_type, topic_key, provenance, is_grounded,
                )
            else:
                existing = await connection.fetchrow(
                    "select episode_id from episodes where user_message_id=$1 limit 1", user_message_id,
                )
                if existing is not None:
                    row = await connection.fetchrow(
                        """update episodes set assistant_message_id=$1, experience_id=$2,
                           ended_at=$3, updated_at=$3 where episode_id=$4 returning episode_id""",
                        assistant_message_id, experience_id, timestamp, existing["episode_id"],
                    )
                else:
                    # Keep contiguous narration in one session instead of creating a row per line.
                    prior_row = await connection.fetchrow(
                        """select episode_id, conversation_id, episode_type, topic_key,
                              user_message_id, assistant_message_id, experience_id,
                              summary, ended_at from episodes
                       where conversation_id=$1 and episode_type='story_reading'
                         and (topic_key=$2 or (topic_key is null and $2 is null))
                         and user_message_id is not null
                         and assistant_message_id is not null
                         and experience_id is not null
                       order by ended_at desc limit 1""",
                        conversation_id, topic_key,
                    )
                    prior = dict(prior_row) if prior_row is not None else None
                    if is_merge_eligible(
                        prior=prior,
                        conversation_id=conversation_id,
                        episode_type=episode_type,
                        topic_key=topic_key,
                        user_message_id=user_message_id,
                        assistant_message_id=assistant_message_id,
                        experience_id=experience_id,
                        timestamp=timestamp,
                    ):
                        summary = f"{prior['summary']}\n{candidate.content}"[-8000:]
                        row = await connection.fetchrow(
                            """update episodes set user_message_id=$1, assistant_message_id=$2, experience_id=$3,
                           summary=$4, ended_at=$5, updated_at=$5 where episode_id=$6 returning episode_id""",
                            user_message_id, assistant_message_id, experience_id, summary, timestamp, prior["episode_id"],
                        )
                    else:
                        row = await connection.fetchrow(
                            """
                        insert into episodes (
                            episode_id, conversation_id, user_message_id, assistant_message_id, experience_id,
                            sequence, summary, source_device, importance, emotional_impact,
                            personal_relevance, relationship_impact, novelty, confidence,
                            recall_frequency, memory_strength, decay, created_at, started_at, ended_at,
                            episode_type, topic_key, provenance, is_grounded, updated_at
                        ) values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,0,$15,$16,$17,$17,$17,$18,$19,$20,$21,$17)
                        returning episode_id
                        """,
                            uuid4(), conversation_id, user_message_id, assistant_message_id, experience_id, sequence,
                            candidate.content, source_device or "unknown", candidate.importance,
                            candidate.emotional_impact, candidate.personal_relevance, candidate.relationship_impact,
                            candidate.novelty, candidate.confidence, candidate.memory_strength, candidate.decay,
                            timestamp, episode_type, topic_key, provenance, is_grounded,
                        )
            episode_id = row["episode_id"]
            if experience_id is not None:
                await internal_state.attach_episode_provenance(
                    connection, experience_id=experience_id, episode_id=episode_id
                )
                await relationship.attach_episode_provenance(
                    connection, experience_id=experience_id, episode_id=episode_id
                )
                await diana_preferences.attach_episode_provenance(
                    connection, experience_id=experience_id, episode_id=episode_id
                )
            if memory_id is not None:
                await memory_service.attach_episode_provenance(
                    connection, memory_id=memory_id, episode_id=episode_id
                )
    record_episode_promotion(status="promoted", score=candidate.promotion_score, reasons=list(candidate.promotion_reasons))
    logger.info("Episode finalized id=%s type=%s provenance=%s score=%.2f latency_ms=%.2f", episode_id, episode_type, provenance, candidate.promotion_score, (perf_counter() - started) * 1000)
    return {"episode_id": episode_id, "episode_type": episode_type, "provenance": provenance, "topic_key": topic_key, "promotion_score": candidate.promotion_score}


async def delete_episode(pool: asyncpg.Pool, *, episode_id: UUID) -> None:
    """Delete one administrator-selected episode while retaining durable records.

    The associated records are evidence/provenance, not children owned by an
    episode. Their episode link is deliberately nulled before the row removal.
    """
    async with pool.acquire() as connection:
        async with connection.transaction():
            exists = await connection.fetchval("select 1 from episodes where episode_id=$1", episode_id)
            if not exists:
                raise KeyError(str(episode_id))
            await knowledge.detach_episode_provenance(connection, episode_id=episode_id)
            await internal_state.detach_episode_provenance(connection, episode_id=episode_id)
            await memory_service.detach_episode_provenance(connection, episode_id=episode_id)
            await diana_preferences.detach_episode_provenance(connection, episode_id=episode_id)
            await relationship.detach_episode_provenance(connection, episode_id=episode_id)
            await connection.execute("delete from episodes where episode_id=$1", episode_id)
