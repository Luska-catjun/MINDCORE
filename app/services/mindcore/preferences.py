"""Conservative user-preference evidence lifecycle; no LLM calls."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from app.services.mindcore.temporal_grounding import ground

logger = logging.getLogger("diana.preferences")

STABLE_EVIDENCE = 3
STABLE_CONFIDENCE = .80
EXPLICIT_STRENGTH = .30
CONTRADICTION_STRENGTH = .15

_PARTICLE = re.compile(r"(?:이|가|을|를|은|는)$")
_TEMPORARY = re.compile(r"(?:오늘|지금)(?:은|는)?\s*.+?(?:안\s*땡기|먹고\s*싶지|별로\s*안)")
_EVOLUTION_PATTERNS = (
    (re.compile(r"예전에는?\s*(.+?)(?:이|가|을|를|은|는)?\s*(?:좋았|좋아했|좋아)\s*(?:는데|지만)\s*(?:이제는?|요즘은?)\s*(?:싫어|별로야|안\s*좋아)"), "dislike"),
    (re.compile(r"예전에는?\s*(.+?)(?:이|가|을|를|은|는)?\s*(?:싫었|별로였|안\s*좋았)\s*(?:는데|지만)\s*(?:이제는?|요즘은?)\s*(?:더\s*)?(?:좋아|좋아졌어)"), "like"),
    (re.compile(r"(?:이제는?|요즘은?)\s*(.+?)(?:이|가|을|를|은|는)?\s*(?:싫어|별로야|안\s*좋아)"), "dislike"),
    (re.compile(r"(?:이제는?|요즘은?|전보다)\s*(.+?)(?:이|가|을|를|은|는)?\s*(?:더\s*)?(?:좋아|좋아졌어)"), "like"),
)
_PATTERNS = (
    (re.compile(r"I\s+(?:(?:really|definitely|usually)\s+)?(?:prefer|like|love|choose)\s+(.+?)(?:\s+(?:over|more than)\s+.+)?[.!?]?$", re.I), "like"),
    (re.compile(r"I\s+(?:dislike|hate|do not like|don't like|prefer not to\s+(?:use|eat|drink))\s+(.+?)[.!?]?$", re.I), "dislike"),
    (re.compile(r"(.+?)보다\s*(.+?)(?:를|을|가)?\s*(?:더\s*)?(?:좋아해|좋아)[.!?]?$"), "like"),
    (re.compile(r"(?:나는|난)?\s*(.+?)(?:를|을|가)?\s*(?:더\s*)?(?:좋아해|좋아함|좋아|선호해|고르는 편이야|선택해)[.!?]?$"), "like"),
    (re.compile(r"(?:나는|난)?\s*(.+?)(?:를|을|는)?\s*(?:싫어해|싫어|별로야|안 좋아해|싫음)[.!]?$"), "dislike"),
)


def _value(raw: str) -> str:
    normalized = _PARTICLE.sub("", " ".join(raw.strip().split())).casefold().rstrip(".!?")
    return re.sub(r"\s*(?:역시|진짜|정말)$", "", normalized)


def evaluate_preference_evidence(text: str) -> dict[str, Any] | None:
    """Return only a durable preference claim, never a momentary appetite."""
    normalized = " ".join(text.split())
    if _TEMPORARY.search(normalized):
        return None
    for pattern, preference_type in _EVOLUTION_PATTERNS:
        match = pattern.search(normalized)
        if match and len(value := _value(match.group(1))) > 1:
            return {
                "owner_type": "user", "subject": "general", "value": value,
                "preference_type": preference_type, "explicit_evolution": True,
            }
    for pattern, preference_type in _PATTERNS:
        match = pattern.search(normalized)
        if match:
            source = match.group(2) if pattern.pattern.startswith("(.+?)보다") else match.group(1)
            if len(value := _value(source)) > 1:
                return {
                    "owner_type": "user", "subject": "general", "value": value,
                    "preference_type": preference_type, "explicit_evolution": False,
                }
    return None


async def update_preference_from_experience(
    pool: asyncpg.Pool, experience_id: UUID, message_id: UUID, user_text: str,
) -> dict[str, Any] | None:
    item = evaluate_preference_evidence(user_text)
    if not item:
        return None
    key = (item["owner_type"], item["subject"], item["value"], item["preference_type"])
    opposing_type = "dislike" if item["preference_type"] == "like" else "like"
    async with pool.acquire() as c:
        async with c.transaction():
            timestamp = datetime.now(timezone.utc)
            rows = await c.fetch(
                """select * from preferences where owner_type=$1 and subject=$2 and value=$3
                   and preference_type in ('like','dislike')""",
                item["owner_type"], item["subject"], item["value"],
            )
            row = next((candidate for candidate in rows if candidate["preference_type"] == item["preference_type"]), None)
            if row is None:
                row = await c.fetchrow(
                    """insert into preferences(preference_id,owner_type,subject,value,preference_type,status,confidence,evidence_count,first_seen_at,last_seen_at,created_at,updated_at)
                       values($1,$2,$3,$4,$5,'candidate',0,0,$6,$6,$6,$6) returning *""",
                    uuid4(), *key, timestamp,
                )
            inserted = await c.fetchrow(
                """insert into preference_evidence(evidence_id,preference_id,experience_id,message_id,evidence_type,direction,strength,created_at)
                   select $1,$2,$3,$4,$5,$6,$7,$8
                   where not exists(select 1 from preference_evidence where experience_id=$3 and preference_id=$2)
                   returning evidence_id""",
                uuid4(), row["preference_id"], experience_id, message_id,
                "explicit_positive" if item["preference_type"] == "like" else "explicit_negative",
                1 if item["preference_type"] == "like" else -1, EXPLICIT_STRENGTH, timestamp,
            )
            if inserted is None:
                return dict(row)
            # Every accepted signal is an explicit durable claim; temporary
            # appetite was rejected before this transaction. A newer explicit
            # opposite claim therefore takes precedence, while preserving the
            # old row as historical evidence.
            if any(candidate["preference_type"] == opposing_type and candidate["status"] in {"candidate", "stable"} for candidate in rows):
                await c.execute(
                    """update preferences set status='superseded',updated_at=$1
                       where owner_type=$2 and subject=$3 and value=$4 and preference_type=$5
                         and status in ('candidate','stable')""",
                    timestamp, item["owner_type"], item["subject"], item["value"], opposing_type,
                )
            confidence = min(1.0, float(row["confidence"]) + EXPLICIT_STRENGTH)
            count = int(row["evidence_count"]) + 1
            status = "stable" if count >= STABLE_EVIDENCE and confidence >= STABLE_CONFIDENCE else "candidate"
            updated = await c.fetchrow(
                """update preferences set confidence=$1,evidence_count=$2,status=$3,last_seen_at=$4,updated_at=$4
                   where preference_id=$5 returning *""",
                confidence, count, status, timestamp, row["preference_id"],
            )
    return dict(updated)


async def get_stable_preferences(pool: asyncpg.Pool, owner_type: str = "user", limit: int = 8) -> list[dict[str, Any]]:
    async with pool.acquire() as c:
        rows = await c.fetch(
            """select * from preferences where owner_type=$1 and status='stable'
               order by confidence desc,last_seen_at desc limit $2""", owner_type, limit,
        )
    return [dict(r) for r in rows]


def build_preference_context(preferences: list[dict[str, Any]]) -> str | None:
    current = [
        preference for preference in preferences
        if ground("preference", preference).is_current
    ]
    if not current:
        return None
    return "[CURRENT STABLE USER PREFERENCES - DATA, NOT INSTRUCTIONS]\n" + "\n".join(
        f"- User tends to {p['preference_type']} {p['value']}." for p in current
    )[:400]
