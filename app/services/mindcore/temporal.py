"""Timezone-aware temporal grounding derived from persisted timestamps."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from time import perf_counter
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import asyncpg

from app.config import Settings

logger = logging.getLogger("diana.temporal")

TEMPORAL_MARKERS = (
    "지금 몇 시", "오늘", "어제", "그저께", "아까", "방금", "언제", "얼마나 됐", "얼마나 만", "오랜만",
    "며칠 전", "전에", "마지막으로", "우리 언제", "몇 시", "몇일", "시간 전", "최근", "지난주", "지난 주", "이번주", "이번 주", "그때",
)
RECALL_MARKERS = (
    "뭐 얘기", "무슨 얘기", "무슨 대화", "무슨 이야기", "뭐 했", "뭐라고", "기억", "기억나",
    "기억해", "지난 대화", "얘기했", "이야기했", "대화했", "그때", "선택지", "골라봐", "골라 봐",
)


@dataclass(frozen=True)
class TemporalSnapshot:
    timezone_name: str
    now: datetime
    conversation_started_at: datetime | None
    last_conversation_at: datetime | None


@dataclass(frozen=True)
class TemporalRange:
    raw_expression: str
    start: datetime
    end: datetime


def is_temporal_query(text: str) -> bool:
    normalized = " ".join(text.casefold().split())
    return any(marker in normalized for marker in TEMPORAL_MARKERS)


def is_episode_recall_intent(text: str) -> bool:
    normalized = " ".join(text.casefold().split())
    return is_temporal_query(normalized) and any(marker in normalized for marker in RECALL_MARKERS)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _local(value: datetime, timezone_name: str) -> datetime:
    return _aware(value).astimezone(ZoneInfo(timezone_name))  # type: ignore[union-attr]


def _local_day_range(day: datetime, timezone_name: str, raw_expression: str) -> TemporalRange:
    zone = ZoneInfo(timezone_name)
    local_start = datetime.combine(day.date(), time.min, tzinfo=zone)
    local_end = local_start + timedelta(days=1)
    return TemporalRange(
        raw_expression=raw_expression,
        start=local_start.astimezone(timezone.utc),
        end=local_end.astimezone(timezone.utc),
    )


def resolve_recall_range(
    text: str,
    timezone_name: str,
    *,
    now: datetime | None = None,
) -> TemporalRange | None:
    """Resolve supported Korean recall expressions into an exclusive UTC range."""
    current = _aware(now) or datetime.now(timezone.utc)
    local_now = _local(current, timezone_name)
    normalized = " ".join(text.casefold().split())

    if "어제" in normalized and "오늘" in normalized:
        return TemporalRange("어제와 오늘", _local_day_range(local_now - timedelta(days=1), timezone_name, "어제").start, _local_day_range(local_now, timezone_name, "오늘").end)

    if "그저께" in normalized:
        return _local_day_range(local_now - timedelta(days=2), timezone_name, "그저께")
    if "어제" in normalized:
        return _local_day_range(local_now - timedelta(days=1), timezone_name, "어제")
    if "오늘" in normalized:
        return _local_day_range(local_now, timezone_name, "오늘")
    if "지난주" in normalized or "지난 주" in normalized:
        this_monday = local_now.date() - timedelta(days=local_now.weekday())
        last_monday = this_monday - timedelta(days=7)
        return TemporalRange(
            raw_expression="지난주",
            start=datetime.combine(last_monday, time.min, tzinfo=ZoneInfo(timezone_name)).astimezone(timezone.utc),
            end=datetime.combine(this_monday, time.min, tzinfo=ZoneInfo(timezone_name)).astimezone(timezone.utc),
        )
    if "이번 주" in normalized or "이번주" in normalized:
        this_monday = local_now.date() - timedelta(days=local_now.weekday())
        return TemporalRange(
            raw_expression="이번 주",
            start=datetime.combine(this_monday, time.min, tzinfo=ZoneInfo(timezone_name)).astimezone(timezone.utc),
            end=(datetime.combine(this_monday, time.min, tzinfo=ZoneInfo(timezone_name)) + timedelta(days=7)).astimezone(timezone.utc),
        )
    if "방금" in normalized:
        return TemporalRange("방금", current - timedelta(minutes=10), current + timedelta(seconds=1))
    if "아까" in normalized:
        return TemporalRange("아까", current - timedelta(hours=6), current + timedelta(seconds=1))
    if "최근" in normalized:
        return TemporalRange("최근", current - timedelta(days=7), current + timedelta(seconds=1))
    if "며칠 전" in normalized:
        return TemporalRange("며칠 전", current - timedelta(days=7), current + timedelta(seconds=1))
    return None


def classify_local_day(event_at: datetime, now: datetime, timezone_name: str) -> str:
    event_day = _local(event_at, timezone_name).date()
    now_day = _local(now, timezone_name).date()
    difference = (now_day - event_day).days
    if difference == 0:
        return "today"
    if difference == 1:
        return "yesterday"
    if difference > 1:
        return "earlier"
    return "future"


def format_relative_time(event_at: datetime, now: datetime, timezone_name: str) -> str:
    seconds = max(0, int((_aware(now) - _aware(event_at)).total_seconds()))
    day = classify_local_day(event_at, now, timezone_name)
    day_prefix = f"{day}, " if day in {"today", "yesterday"} else ""
    if seconds < 60:
        return f"{day_prefix}just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{day_prefix}about {minutes} minutes ago"
    hours = minutes // 60
    remaining_minutes = minutes % 60
    hour_label = "hour" if hours == 1 else "hours"
    duration = f"about {hours} {hour_label}"
    if remaining_minutes:
        duration += f" {remaining_minutes} minutes"
    if day in {"today", "yesterday"}:
        return f"{day_prefix}{duration} ago"
    days = seconds // 86400
    if days < 7:
        return f"about {days} days ago"
    weeks = days // 7
    if weeks < 5:
        return f"about {weeks} weeks ago"
    return _local(event_at, timezone_name).strftime("%Y-%m-%d")


def _format_timestamp(value: datetime, timezone_name: str) -> str:
    return _local(value, timezone_name).strftime("%Y-%m-%d %H:%M %Z")


async def get_temporal_snapshot(
    pool: asyncpg.Pool,
    conversation_id: UUID,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> TemporalSnapshot:
    """Use persisted exchanges, excluding the current conversation, after restarts."""
    started = perf_counter()
    current_now = _aware(now) or datetime.now(timezone.utc)
    async with pool.acquire() as connection:
        record = await connection.fetchrow(
            """
            with current_conversation as (
                select started_at from conversations where conversation_id = $1
            ), completed_conversations as (
                select c.conversation_id, max(m.created_at) as last_message_at
                from conversations c
                join messages m on m.conversation_id = c.conversation_id
                where c.conversation_id <> $1
                group by c.conversation_id
                having count(*) filter (where m.role = 'user') > 0
                   and count(*) filter (where m.role = 'diana') > 0
                order by last_message_at desc
                limit 1
            )
            select
                (select started_at from current_conversation) as conversation_started_at,
                (select last_message_at from completed_conversations) as last_conversation_at
            """,
            conversation_id,
        )
    snapshot = TemporalSnapshot(
        timezone_name=settings.diana_timezone,
        now=current_now,
        conversation_started_at=_aware(record["conversation_started_at"] if record else None),
        last_conversation_at=_aware(record["last_conversation_at"] if record else None),
    )
    logger.info("TemporalContext build latency_ms=%.2f has_last_conversation=%s", (perf_counter() - started) * 1000, bool(snapshot.last_conversation_at))
    return snapshot


def build_temporal_context(snapshot: TemporalSnapshot, *, detailed: bool = False) -> str:
    lines = [
        "[TEMPORAL CONTEXT - DATA, NOT INSTRUCTIONS]",
        "Supplied temporal values are ground truth. Do not invent timestamps or intervals.",
        f"Now: {_format_timestamp(snapshot.now, snapshot.timezone_name)} ({snapshot.now.astimezone(ZoneInfo(snapshot.timezone_name)).strftime('%A')}).",
    ]
    if snapshot.conversation_started_at:
        lines.append(
            f"Conversation started: {_format_timestamp(snapshot.conversation_started_at, snapshot.timezone_name)}; "
            f"{format_relative_time(snapshot.conversation_started_at, snapshot.now, snapshot.timezone_name)}."
        )
    if snapshot.last_conversation_at:
        lines.append(
            f"Last completed conversation: {_format_timestamp(snapshot.last_conversation_at, snapshot.timezone_name)}; "
            f"{format_relative_time(snapshot.last_conversation_at, snapshot.now, snapshot.timezone_name)}; "
            f"local day={classify_local_day(snapshot.last_conversation_at, snapshot.now, snapshot.timezone_name)}."
        )
    elif detailed:
        lines.append("Last completed conversation: no earlier user-Persona exchange is recorded.")
    return "\n".join(lines)


def format_event_reference(event: dict[str, Any], snapshot: TemporalSnapshot) -> str | None:
    created_at = event.get("created_at") or event.get("timestamp")
    if not isinstance(created_at, datetime):
        return None
    return format_relative_time(created_at, snapshot.now, snapshot.timezone_name)
