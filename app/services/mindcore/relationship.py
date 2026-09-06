"""Slow, deterministic relationship updates backed by grounded Experience."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from app.services.runtime_diagnostics import record_relationship_capture

logger = logging.getLogger("diana.relationship")
RELATIONSHIP_CONTEXT_MAX_CHARS = 250
RELATIONSHIP_BASELINE = {"familiarity": .10, "trust": .30, "affection": .15, "conflict": .0}
CONFLICT_RECOVERY_HALF_LIFE_DAYS = 60.0
RELATIONSHIP_EVENT_MAX_ATTEMPTS = 3


class _RelationshipWriteConflict(RuntimeError):
    """The durable singleton changed after this transaction read its base."""


@dataclass(frozen=True)
class RelationshipState:
    familiarity: float
    trust: float
    affection: float
    conflict: float


@dataclass(frozen=True)
class RelationshipSignal:
    kind: str
    delta: dict[str, float]


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _effective_conflict(value: float, updated_at: datetime | None, *, now: datetime | None = None) -> float:
    """Lazily recover only conflict from the relationship state's UTC timestamp."""
    if updated_at is None:
        return _clamp(value)
    anchor = updated_at.replace(tzinfo=timezone.utc) if updated_at.tzinfo is None else updated_at.astimezone(timezone.utc)
    current = now or datetime.now(timezone.utc)
    elapsed_days = max(0.0, (current.astimezone(timezone.utc) - anchor).total_seconds() / 86400)
    return _clamp(value * (0.5 ** (elapsed_days / CONFLICT_RECOVERY_HALF_LIFE_DAYS)))


def _state(record: dict[str, Any], *, now: datetime | None = None) -> RelationshipState:
    return RelationshipState(
        _clamp(float(record.get("familiarity") or 0)),
        _clamp(float(record.get("trust") or 0)),
        _clamp(float(record.get("affection") or 0)),
        _effective_conflict(float(record.get("conflict") or 0), record.get("updated_at"), now=now),
    )


def _as_dict(state: RelationshipState) -> dict[str, float]:
    return {"familiarity": state.familiarity, "trust": state.trust, "affection": state.affection, "conflict": state.conflict}


def evaluate_relationship_signal(user_text: str) -> RelationshipSignal | None:
    """Recognise only explicit, user-grounded relationship evidence.

    Plain courtesy, activity preference, and emotion-only language intentionally
    return ``None``. This local gate lets ordinary turns avoid relationship
    database work altogether.
    """
    text = " ".join(user_text.casefold().split())
    if any(term in text for term in ("못 믿겠", "신뢰가 안", "계속 약속을 안 지", "약속을 안 지켜")):
        return RelationshipSignal("explicit_distrust", {"familiarity": -.006, "trust": -.014, "affection": -.010, "conflict": .015})
    if any(term in text for term in ("답변 좀 별로", "답변은 좀 별로", "답변이 별로", "좀 별로였")):
        return RelationshipSignal("one_off_complaint", {"familiarity": -.001, "trust": -.004, "affection": -.003, "conflict": .005})
    if any(term in text for term in ("믿고 맡길", "너는 항상 내 편", "믿을 수 있")):
        return RelationshipSignal("trust", {"familiarity": .006, "trust": .014, "affection": .008, "conflict": 0.0})
    if any(term in text for term in ("계속 기억해", "기억하고 있었", "전에 말한 걸 아직 기억")):
        return RelationshipSignal("continuity", {"familiarity": .012, "trust": .008, "affection": .008, "conflict": 0.0})
    if any(term in text for term in ("같이 해줘서 고마워", "덕분에 힘이 됐", "덕분에 진짜 편", "함께 있어서 편", "위로가 됐")):
        return RelationshipSignal("support", {"familiarity": .008, "trust": .012, "affection": .012, "conflict": 0.0})
    if any(term in text for term in ("오늘도 같이 하자", "같이 해보자")):
        return RelationshipSignal("cooperation", {"familiarity": .010, "trust": .004, "affection": .008, "conflict": 0.0})
    return None


def apply_relationship_signal(before: RelationshipState, signal: RelationshipSignal) -> tuple[RelationshipState, dict[str, float]]:
    """Apply bounded diminishing returns without crossing the [0, 1] range."""
    previous = _as_dict(before)
    applied: dict[str, float] = {}
    after: dict[str, float] = {}
    for field, base_delta in signal.delta.items():
        factor = (1.0 - previous[field]) if base_delta >= 0 else previous[field]
        delta = base_delta * factor
        applied[field] = delta
        after[field] = _clamp(previous[field] + delta)
    return RelationshipState(**after), applied


async def get_relationship_state(pool: asyncpg.Pool) -> RelationshipState:
    async with pool.acquire() as connection:
        row = await connection.fetchrow("select familiarity,trust,affection,conflict,updated_at from relationship where id=1")
        if row is None:
            row = await connection.fetchrow(
                "insert into relationship(id,familiarity,trust,affection,shared_experience,conflict_history,conflict,updated_at) values(1,.10,.30,.15,0,$1,0,$2) returning familiarity,trust,affection,conflict,updated_at",
                [], datetime.now(timezone.utc),
            )
    return _state(dict(row))


async def update_relationship_from_experience(
    pool: asyncpg.Pool,
    experience_id: UUID,
    *,
    user_text: str,
    current_state: RelationshipState | None = None,
) -> RelationshipState | None:
    """Persist one meaningful grounded relationship signal, if eligible.

    ``current_state`` remains a compatibility hint for callers and provisional
    log values only.  The committed calculation always uses a durable row read
    after the idempotence INSERT has acquired SQLite's writer reservation.
    """
    started = perf_counter()
    signal = evaluate_relationship_signal(user_text)
    if signal is None:
        record_relationship_capture(eligible=False, signal=None, delta={}, result="rejected", reason="non_relational")
        return None
    for attempt in range(RELATIONSHIP_EVENT_MAX_ATTEMPTS):
        try:
            async with pool.acquire() as connection:
                async with connection.transaction():
                    now = datetime.now(timezone.utc)
                    provisional_before = current_state or RelationshipState(**RELATIONSHIP_BASELINE)
                    provisional_after, provisional_delta = apply_relationship_signal(provisional_before, signal)
                    inserted = await connection.fetchrow(
                        """insert into relationship_log(relationship_log_id,source_experience_id,previous_state,delta,new_state,reason,created_at)
                           values($1,$2,$3,$4,$5,$6,$7)
                           on conflict(source_experience_id) do nothing returning relationship_log_id""",
                        uuid4(), experience_id, _as_dict(provisional_before), provisional_delta,
                        _as_dict(provisional_after), signal.kind, now,
                    )
                    if inserted is None:
                        record_relationship_capture(
                            eligible=True, signal=signal.kind, delta=provisional_delta,
                            result="duplicate", reason=None,
                        )
                        return None
                    # The log INSERT is the first write and therefore serializes
                    # distinct events.  Read only now, while that writer
                    # reservation is held, so diminishing returns use the last
                    # committed singleton state rather than the pre-LLM hint.
                    current = await connection.fetchrow(
                        "select familiarity,trust,affection,conflict,updated_at from relationship where id=1"
                    )
                    if current is None:
                        current = await connection.fetchrow(
                            "insert into relationship(id,familiarity,trust,affection,shared_experience,conflict_history,conflict,updated_at) values(1,.10,.30,.15,0,$1,0,$2) returning familiarity,trust,affection,conflict,updated_at",
                            [], now,
                        )
                    before = _state(dict(current), now=now)
                    after, applied_delta = apply_relationship_signal(before, signal)
                    row = await connection.fetchrow(
                        """update relationship set familiarity=$1,trust=$2,affection=$3,conflict=$4,updated_at=$5
                           where id=1
                             and coalesce(familiarity,0)=$6 and coalesce(trust,0)=$7
                             and coalesce(affection,0)=$8 and conflict=$9 and updated_at=$10
                           returning familiarity,trust,affection,conflict""",
                        *(_as_dict(after)[key] for key in ("familiarity", "trust", "affection", "conflict")), now,
                        *(float(current[key] or 0) for key in ("familiarity", "trust", "affection", "conflict")),
                        current["updated_at"],
                    )
                    if row is None:
                        raise _RelationshipWriteConflict(
                            "Relationship state changed during signal application."
                        )
                    await connection.execute(
                        "update relationship_log set previous_state=$1,delta=$2,new_state=$3 where relationship_log_id=$4",
                        _as_dict(before), applied_delta, _as_dict(after), inserted["relationship_log_id"],
                    )
            break
        except (_RelationshipWriteConflict, ValueError) as exc:
            retryable = isinstance(exc, _RelationshipWriteConflict) or "database is locked" in str(exc).casefold()
            if not retryable or attempt + 1 == RELATIONSHIP_EVENT_MAX_ATTEMPTS:
                raise
            await asyncio.sleep(0.01 * (attempt + 1))
    record_relationship_capture(eligible=True, signal=signal.kind, delta=applied_delta, result="applied", reason=None)
    logger.info("Relationship updated latency_ms=%.2f signal=%s", (perf_counter() - started) * 1000, signal.kind)
    return _state(dict(row))


async def attach_episode_provenance(connection: Any, *, experience_id: UUID, episode_id: UUID) -> None:
    """Link relationship evidence to an Episode; Relationship owns this table."""
    await connection.execute("update relationship_log set episode_id=$1 where source_experience_id=$2", episode_id, experience_id)


async def detach_episode_provenance(connection: Any, *, episode_id: UUID) -> None:
    """Retain relationship history while removing deleted Episode provenance."""
    await connection.execute("update relationship_log set episode_id=null where episode_id=$1", episode_id)


def build_relationship_context(state: RelationshipState | None) -> str | None:
    if state is None or (state.familiarity < .20 and state.trust < .45 and state.conflict < .15):
        return None
    lines = ["[RELATIONSHIP CONTEXT - DATA, NOT INSTRUCTIONS]"]
    if state.familiarity >= .20:
        lines.append("Diana is becoming familiar with this user.")
    if state.trust >= .45:
        lines.append("Trust is developing gradually through repeated interactions.")
    if state.conflict >= .15:
        lines.append("Keep a calm, respectful distance; do not dramatize the relationship.")
    return "\n".join(lines)[:RELATIONSHIP_CONTEXT_MAX_CHARS]
