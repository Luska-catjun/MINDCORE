"""Read persisted episodes for explicit, time-bounded recall questions."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import asyncpg

from app.services.mindcore.temporal import TemporalRange

logger = logging.getLogger("diana.episode_recall")

MAX_EPISODE_RECALL_CANDIDATES = 100
MAX_EPISODE_RECALL_RESULTS = 24


@dataclass(frozen=True)
class EpisodeRecallResult:
    status: str
    time_range: TemporalRange
    episodes: list[dict[str, Any]]
    raw_messages: list[dict[str, Any]]
    failure_reason: str | None = None


def _select_episodes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(rows) <= MAX_EPISODE_RECALL_RESULTS:
        return rows

    # Keep the requested time range authoritative. When it is unusually busy,
    # importance and strength select the subset, then chronological order makes
    # the recalled story readable to the model.
    selected = sorted(
        rows,
        key=lambda row: (
            float(row.get("importance") or 0.0),
            float(row.get("memory_strength") or 0.0),
            row["created_at"],
        ),
        reverse=True,
    )[:MAX_EPISODE_RECALL_RESULTS]
    return sorted(selected, key=lambda row: row["created_at"])


async def retrieve_episodes_for_range(
    pool: asyncpg.Pool,
    time_range: TemporalRange,
) -> EpisodeRecallResult:
    """Return a non-fatal result so an unavailable reader is not confused with no records."""
    logger.info("[RECALL] query started")
    try:
        async with pool.acquire() as connection:
            records = await connection.fetch(
                """
                select episode_id, conversation_id, sequence, summary, importance,
                    emotional_impact, personal_relevance, relationship_impact,
                    novelty, confidence, recall_frequency, memory_strength, created_at,
                    started_at, episode_type, topic_key, provenance, is_grounded
                from episodes
                where created_at >= $1 and created_at < $2
                order by created_at asc
                limit $3
                """,
                time_range.start,
                time_range.end,
                MAX_EPISODE_RECALL_CANDIDATES,
            )
            raw_records = await connection.fetch(
                """select id, conversation_id, role, content, created_at from messages
                   where created_at >= $1 and created_at < $2 and role='user'
                   order by created_at asc limit $3""",
                time_range.start, time_range.end, MAX_EPISODE_RECALL_CANDIDATES,
            )
        episodes = _select_episodes([dict(record) for record in records])
        raw_messages = [dict(record) for record in raw_records if any(marker in str(record["content"]).casefold() for marker in ("중", "골라", "선택"))][:12]
        logger.info("[RECALL] status=success episodes_found=%s", len(episodes))
        logger.info("[RECALL] episode_ids=%s", [str(item["episode_id"]) for item in episodes])
        return EpisodeRecallResult("success", time_range, episodes, raw_messages)
    except Exception as exc:
        logger.warning(
            "[RECALL] status=failed reason=%s error_type=%s",
            str(exc),
            type(exc).__name__,
        )
        return EpisodeRecallResult("failed", time_range, [], [], type(exc).__name__)
