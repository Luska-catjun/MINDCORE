"""Deterministic motivational state.  It deliberately does not drive the LLM."""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import asyncpg

BASELINES = {"curiosity": .45, "understanding": .30, "social_connection": .35, "activity": .30, "helpfulness": .35, "autonomy": .35}
HALF_LIVES = {"curiosity": 18, "understanding": 12, "social_connection": 24, "activity": 18, "helpfulness": 12, "autonomy": 24}
SHORT = {"ok", "okay", "yes", "ㅇㅇ", "ㄱㄱ", "네", "응", "그래", "ㅋㅋ", "ㅎㅎ"}
NEED_EVENT_MAX_ATTEMPTS = 3
# v1 only formed need-generated goals once curiosity/understanding reached .65.
# Self-directed future language is independent evidence, so it has its own
# conservative candidate score instead of pretending it is a user Need signal.
SELF_EXPRESSION_CANDIDATE_THRESHOLD = .65
SELF_EXPRESSION_PROMOTION_THRESHOLD = .85
SELF_EXPRESSION_REINFORCEMENT = .15
SELF_EXPRESSION_PRIORITY_BOOST = .10
_FUTURE_MARKERS = ("다음에는", "다음에", "나중에는", "나중에", "언젠가", "앞으로")
_DESIRE_ACTIONS = (
    "들어보고 싶", "읽어보고 싶", "해보고 싶", "알아보고 싶", "배워보고 싶",
    "공부해보고 싶", "보고 싶", "하고 싶", "해보자", "하자",
)
_TRANSIENT_TARGETS = frozenset({"물", "밥", "잠", "커피", "간식"})
_SUPPORTED_MULTISTEP_GAMES = ("업다운", "스무고개")
_TARGET_PREFIX = re.compile(r"^(?:다음에는?|나중에는?|언젠가|앞으로)\s*")
_TARGET_SUFFIX = re.compile(r"(?:이야기|동화)?(?:도|을|를|은|는|이|가|에|의)?\s*(?:더|한번|한 번)?\s*$")
logger = logging.getLogger("diana.goals")


class _NeedWriteConflict(RuntimeError):
    """The durable Need changed after this transaction read its mutation base."""

@dataclass
class Need:
    key: str; value: float; baseline: float; updated_at: datetime; last_triggered_at: datetime | None = None

@dataclass
class Goal:
 id: str; goal_key: str; summary: str; origin_need: str; priority: float; status: str; progress: float; confidence: float; conversation_id: UUID | None; source_type: str; source_id: str | None; updated_at: datetime; expires_at: datetime | None

@dataclass(frozen=True)
class GoalsNeedsTurnResult:
    """Final, request-scoped motivational state for one completed turn."""
    needs: dict[str, Need]
    created_goals: tuple[Goal, ...]
    relevant_goals: tuple[Goal, ...]

@dataclass(frozen=True)
class SelfExpressionGoalSignal:
    """A deterministic, content-minimal interpretation of Diana's own reply."""
    target: str
    target_key: str
    score: float
    repeat_intent: bool
    action: str

def _now() -> datetime: return datetime.now(timezone.utc)
def _clamp(value: float) -> float: return max(0., min(1., value))


def _decayed_need_value(row: dict[str, Any], *, now: datetime) -> float:
    """Return the effective value of one authoritative durable Need row."""
    updated_at = row["updated_at"]
    elapsed = max(0., (now - updated_at).total_seconds() / 3600)
    baseline = float(row["baseline"])
    value = baseline + (float(row["value"]) - baseline) * .5 ** (
        elapsed / HALF_LIVES[str(row["need_key"])]
    )
    return _clamp(value)

async def _get_need_snapshot_with_conn(c: Any, *, now: datetime | None = None) -> dict[str, Need]:
    """Read the lazily decayed Need snapshot using an already acquired connection."""
    current = now or _now()
    rows = await c.fetch("select need_key,value,baseline,updated_at,last_triggered_at from diana_needs")
    loaded = {str(row['need_key']): row for row in rows}
    missing = [key for key in BASELINES if key not in loaded]
    if missing:
        # Repair only an incomplete durable invariant.  A normal turn never
        # enters this transaction or issues canonical no-op writes.
        async with c.transaction():
            for key in missing:
                row = await c.fetchrow("""insert into diana_needs(need_key,value,baseline,updated_at,metadata)
                    values($1,$2,$2,$3,'{}') on conflict(need_key) do nothing
                    returning need_key,value,baseline,updated_at,last_triggered_at""", key, BASELINES[key], current)
                if row is None:
                    # Another request repaired this row first.  Read only the
                    # contested key so the returned snapshot remains durable
                    # source-of-truth data without a full-table reload.
                    row = await c.fetchrow("select need_key,value,baseline,updated_at,last_triggered_at from diana_needs where need_key=$1", key)
                loaded[key] = row
    result = {}
    for row in loaded.values():
        value = _decayed_need_value(row, now=current)
        result[str(row['need_key'])] = Need(str(row['need_key']), value, float(row['baseline']), current, row['last_triggered_at'])
    return result

async def get_need_snapshot(pool: asyncpg.Pool, *, now: datetime | None = None) -> dict[str, Need]:
    async with pool.acquire() as c:
        return await _get_need_snapshot_with_conn(c, now=now)

async def _apply_with_conn(c: Any, needs: dict[str, Need], key: str, delta: float, reason: str, source_type: str, source_id: str | None, conversation_id: UUID | None) -> None:
    """Persist one idempotent Need signal through an already acquired connection."""
    need = needs[key]
    fingerprint = f"{key}:{source_type}:{source_id or ''}:{reason}"
    for attempt in range(NEED_EVENT_MAX_ATTEMPTS):
        try:
            async with c.transaction():
                # The INSERT remains the idempotence authority and is also the
                # transaction's first write.  On SQLite/libSQL this obtains the
                # single-writer reservation before we read the mutation base.
                # A competing writer fails with BUSY/locked and retries from a
                # new transaction, so it cannot reuse a stale caller snapshot.
                now = max(_now(), need.updated_at)
                provisional_after = _clamp(need.value + delta)
                event = await c.fetchrow("""insert into diana_need_events(id,need_key,delta,before_value,after_value,reason,source_type,source_id,conversation_id,fingerprint,created_at)
                    values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                    on conflict(fingerprint) do nothing
                    returning id""", uuid4(), key, delta, need.value, provisional_after, reason, source_type, source_id, conversation_id, fingerprint, now)
                if event is None:
                    return
                current = await c.fetchrow(
                    "select need_key,value,baseline,updated_at,last_triggered_at from diana_needs where need_key=$1",
                    key,
                )
                if current is None:
                    raise RuntimeError(f"Missing canonical Need row for {key}.")
                # Preserve the established lazy-decay boundary represented by
                # the caller snapshot.  If another event committed after that
                # snapshot, its newer durable timestamp wins and is never
                # decayed backwards or overwritten by the stale timestamp.
                calculation_time = max(need.updated_at, current["updated_at"])
                now = max(now, calculation_time)
                before = _decayed_need_value(current, now=calculation_time)
                after = _clamp(before + delta)
                updated = await c.fetchrow(
                    """update diana_needs set value=$1,updated_at=$2,last_triggered_at=$2
                       where need_key=$3 and value=$4 and baseline=$5 and updated_at=$6
                       returning need_key""",
                    after, now, key, current["value"], current["baseline"], current["updated_at"],
                )
                if updated is None:
                    raise _NeedWriteConflict("Need state changed during signal application.")
                await c.execute(
                    "update diana_need_events set before_value=$1,after_value=$2 where id=$3",
                    before, after, event["id"],
                )
            need.value, need.updated_at, need.last_triggered_at = after, now, now
            return
        except (_NeedWriteConflict, ValueError) as exc:
            retryable = isinstance(exc, _NeedWriteConflict) or "database is locked" in str(exc).casefold()
            if not retryable or attempt + 1 == NEED_EVENT_MAX_ATTEMPTS:
                raise
            await asyncio.sleep(0.01 * (attempt + 1))

async def _apply(pool: asyncpg.Pool, needs: dict[str, Need], key: str, delta: float, reason: str, source_type: str, source_id: str | None, conversation_id: UUID | None) -> None:
    async with pool.acquire() as c:
        await _apply_with_conn(c, needs, key, delta, reason, source_type, source_id, conversation_id)

def _target(text: str, working_memory: Any | None) -> str | None:
    focus = getattr(working_memory, 'current_focus', None)
    if focus: return str(focus)
    return None


def _normalize_goal_target(value: str) -> tuple[str, str] | None:
    """Normalize an explicit action target without treating arbitrary nouns as goals."""
    canonical = " ".join(value.strip().split()).strip(" .,!?")
    canonical = re.sub(r"\s+(?:진짜|정말|너무|더|한번|한 번)\s*", " ", canonical).strip()
    canonical = re.sub(r"(?:에\s*대해서|에\s*관해)$", "", canonical).strip()
    canonical = _TARGET_SUFFIX.sub("", canonical).strip()
    canonical = re.sub(r"^(?:너랑|너와|나랑|나와)\s+", "", canonical).strip()
    if len(canonical) < 1 or canonical.casefold() in _TRANSIENT_TARGETS:
        return None
    key = re.sub(r"[^0-9a-z가-힣]+", "_", canonical.casefold()).strip("_")
    return (key, canonical) if key else None


def classify_self_expression_goal(text: str, *, working_memory: Any | None = None) -> SelfExpressionGoalSignal | None:
    """Classify a durable *future desire*, not positive sentiment or a decision.

    The classifier deliberately combines four independently meaningful facets:
    first-person desire, a future/continuation cue, an actionable verb, and a
    concrete target.  This stays deterministic and avoids a per-turn LLM call,
    while being more robust than treating one keyword or regex match as a goal.
    """
    normalized = " ".join(text.casefold().split())
    if not normalized:
        return None
    action = next((item for item in sorted(_DESIRE_ACTIONS, key=len, reverse=True) if item in normalized), None)
    if action is None:
        return None
    action_index = normalized.find(action)
    before_action = normalized[:action_index].split(".")[-1].split("!")[-1].strip()
    before_action = _TARGET_PREFIX.sub("", before_action)
    target = _normalize_goal_target(before_action)
    if target is None:
        focus = _target("", working_memory)
        target = _normalize_goal_target(focus) if focus else None
    if target is None:
        return None
    target_key, canonical = target
    has_future_marker = any(marker in normalized for marker in _FUTURE_MARKERS)
    has_desire = "싶" in action or action in {"해보자", "하자"}
    # A desire verb is inherently future-facing, but an explicit temporal cue
    # makes the evidence stronger.  Repetition is not a new goal instance.
    score = .20 + .25 + (.30 if has_future_marker else .15) + (.15 if has_desire else 0.0)
    repeat_intent = any(marker in normalized for marker in ("또 ", "다시 ", "한번 더", "한 번 더"))
    if repeat_intent:
        score = min(1.0, score + .10)
    if score < SELF_EXPRESSION_CANDIDATE_THRESHOLD:
        return None
    return SelfExpressionGoalSignal(canonical, target_key, round(score, 2), repeat_intent, action)


def _known_story_conflict(signal: SelfExpressionGoalSignal, epistemic_items: list[dict[str, Any]] | None) -> bool:
    """Known story blocks a first-time desire; explicit repetition remains valid."""
    if signal.repeat_intent:
        return False
    return any(
        str(item.get("subject_key")) == signal.target_key
        and item.get("knowledge_type") == "story"
        and str(item.get("status")) != "unknown"
        for item in (epistemic_items or [])
    )


def _progress_plan_for_target(target: str) -> dict[str, Any] | None:
    """Declare milestones only for an explicitly structured, supported goal.

    Ordinary curiosity and one-shot story goals intentionally remain binary.
    """
    normalized = target.casefold()
    if "규칙" not in normalized or not any(marker in normalized for marker in ("게임", * _SUPPORTED_MULTISTEP_GAMES)):
        return None
    subject = next((game for game in _SUPPORTED_MULTISTEP_GAMES if game in normalized), None)
    if subject is None:
        return None
    return {
        "subject_key": subject,
        "milestones": {"rules_learned": .35, "activity_started": .40, "activity_completed": .25},
    }


async def capture_self_expression_goal(
    pool: asyncpg.Pool,
    conversation_id: UUID,
    assistant_text: str,
    assistant_message_id: UUID,
    *,
    working_memory: Any | None = None,
    epistemic_items: list[dict[str, Any]] | None = None,
) -> Goal | None:
    """Persist one self-originated goal candidate after a saved assistant reply.

    No database work is performed for ordinary replies.  Explicit desires make
    one targeted goal lookup so duplicate reinforcement and terminal-history
    preservation are decided from durable state rather than process memory.
    """
    signal = classify_self_expression_goal(assistant_text, working_memory=working_memory)
    if signal is None:
        return None
    if _known_story_conflict(signal, epistemic_items):
        logger.info("GOAL_FORMATION source=self_expression score=%.2f threshold=%.2f created=false reason=already_satisfied", signal.score, SELF_EXPRESSION_CANDIDATE_THRESHOLD)
        return None
    base_key = f"curiosity:{signal.target_key}"
    now = _now()
    async with pool.acquire() as c:
        # The normal chat path reuses the epistemic lookup made before the LLM.
        # If the reply names an earlier-turn subject, perform this one narrow
        # durable check rather than trusting a process-local conversation cache.
        if not signal.repeat_intent and not any(str(item.get("subject_key")) == signal.target_key for item in (epistemic_items or [])):
            knowledge = await c.fetchrow(
                "select knowledge_type,status from diana_knowledge where subject_key=$1", signal.target_key,
            )
            if knowledge is not None and knowledge["knowledge_type"] == "story" and knowledge["status"] != "unknown":
                logger.info("GOAL_FORMATION source=self_expression score=%.2f threshold=%.2f created=false reason=already_satisfied", signal.score, SELF_EXPRESSION_CANDIDATE_THRESHOLD)
                return None
        rows = [dict(row) for row in await c.fetch("select * from diana_goals where goal_key=$1", base_key)]
        current = next((row for row in rows if row["status"] in ("candidate", "active")), None)
        if current is not None:
            priority = _clamp(max(float(current["priority"]), signal.score) + SELF_EXPRESSION_PRIORITY_BOOST)
            confidence = _clamp(max(float(current["confidence"]), signal.score) + SELF_EXPRESSION_REINFORCEMENT)
            await c.execute(
                "update diana_goals set priority=$1,confidence=$2,updated_at=$3 where id=$4",
                priority, confidence, now, current["id"],
            )
            current.update(priority=priority, confidence=confidence, updated_at=now)
            logger.info("GOAL_FORMATION source=self_expression score=%.2f threshold=%.2f created=false reason=reinforced", signal.score, SELF_EXPRESSION_CANDIDATE_THRESHOLD)
            return _goal_from_row(current)
        if rows and not signal.repeat_intent:
            logger.info("GOAL_FORMATION source=self_expression score=%.2f threshold=%.2f created=false reason=terminal_history", signal.score, SELF_EXPRESSION_CANDIDATE_THRESHOLD)
            return None
        goal_key = base_key if not rows else f"{base_key}:renewed:{assistant_message_id}"
        active_counts = await c.fetchrow(
            "select count(*) as total, sum(case when conversation_id=$1 then 1 else 0 end) as conversation_total from diana_goals where status='active'",
            conversation_id,
        )
        total_active = int(active_counts["total"] or 0)
        conversation_active = int(active_counts["conversation_total"] or 0)
        status = "active" if (signal.score >= SELF_EXPRESSION_PROMOTION_THRESHOLD
                                and total_active < 3 and conversation_active < 2) else "candidate"
        identifier = uuid4()
        summary = f"Explore {signal.target}"
        metadata = {"formation_score": signal.score, "formation_threshold": SELF_EXPRESSION_CANDIDATE_THRESHOLD,
                    "promotion_threshold": SELF_EXPRESSION_PROMOTION_THRESHOLD, "reason": "explicit_future_desire",
                    "action": signal.action}
        if progress_plan := _progress_plan_for_target(signal.target):
            metadata["progress_plan"] = progress_plan
        await c.execute("""insert into diana_goals(id,goal_key,goal_type,summary,origin_need,priority,status,progress,confidence,conversation_id,source_type,source_id,created_at,updated_at,expires_at,metadata)
            values($1,$2,'short_term',$3,'curiosity',$4,$5,0,$4,$6,'self_expression',$7,$8,$8,$9,$10)""",
            identifier, goal_key, summary, signal.score, status, conversation_id, assistant_message_id,
            now, now + timedelta(hours=24), metadata,
        )
    goal = Goal(str(identifier), goal_key, summary, "curiosity", signal.score, status, 0, signal.score,
                conversation_id, "self_expression", str(assistant_message_id), now, now + timedelta(hours=24))
    logger.info("GOAL_FORMATION source=self_expression score=%.2f threshold=%.2f promotion_threshold=%.2f created=true status=%s", signal.score, SELF_EXPRESSION_CANDIDATE_THRESHOLD, SELF_EXPRESSION_PROMOTION_THRESHOLD, status)
    return goal


async def satisfy_story_goals(
    pool: asyncpg.Pool,
    conversation_id: UUID,
    learned_subject_keys: list[str],
) -> int:
    """Close unresolved story-learning goals when grounded knowledge is acquired."""
    keys = list(dict.fromkeys(f"curiosity:{key}" for key in learned_subject_keys if key))
    if not keys:
        return 0
    placeholders = ", ".join(f"${index}" for index in range(1, len(keys) + 1))
    async with pool.acquire() as c:
        rows = await c.fetch(
            "select id from diana_goals where conversation_id=$" + str(len(keys) + 1)
            + " and status in ('candidate','active') and goal_key in (" + placeholders + ")",
            *keys, conversation_id,
        )
        if not rows:
            return 0
        now = _now()
        for row in rows:
            await c.execute("update diana_goals set status='satisfied',progress=1.0,updated_at=$1 where id=$2", now, row["id"])
    return len(rows)


async def _apply_goal_progress_evidence_with_conn(
    c: Any,
    *,
    conversation_id: UUID,
    subject_key: str,
    milestone: str,
    source_type: str,
    source_id: str,
) -> int:
    """Apply one idempotent structured milestone without changing motivation."""
    rows = await c.fetch(
        "select * from diana_goals where conversation_id=$1 and status in ('candidate','active')",
        conversation_id,
    )
    updated = 0
    for source_row in rows:
        row = dict(source_row)
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        plan = metadata.get("progress_plan") if isinstance(metadata.get("progress_plan"), dict) else None
        if plan is None or str(plan.get("subject_key")) != subject_key:
            continue
        milestones = plan.get("milestones") if isinstance(plan.get("milestones"), dict) else {}
        contribution = float(milestones.get(milestone, 0.0) or 0.0)
        if contribution <= 0:
            continue
        fingerprint = f"{source_type}:{source_id}:{milestone}"
        evidence = metadata.get("progress_evidence") if isinstance(metadata.get("progress_evidence"), list) else []
        if any(isinstance(item, dict) and item.get("fingerprint") == fingerprint for item in evidence):
            continue
        now = _now()
        progress = _clamp(float(row["progress"]) + contribution)
        terminal = milestone == "activity_completed" or progress >= 1.0
        if terminal:
            progress = 1.0
        updated_metadata = {
            **metadata,
            "progress_evidence": [*evidence[-15:], {"fingerprint": fingerprint, "milestone": milestone,
                                                       "source_type": source_type, "source_id": source_id,
                                                       "contribution": contribution}],
        }
        await c.execute(
            "update diana_goals set progress=$1,status=$2,updated_at=$3,metadata=$4 where id=$5",
            progress, "satisfied" if terminal else row["status"], now, updated_metadata, row["id"],
        )
        updated += 1
    return updated


async def apply_goal_progress_evidence(
    pool: asyncpg.Pool,
    *,
    conversation_id: UUID,
    subject_key: str,
    milestone: str,
    source_type: str,
    source_id: str,
) -> int:
    """Public transaction boundary for grounded multi-step Goal progress."""
    async with pool.acquire() as c:
        async with c.transaction():
            return await _apply_goal_progress_evidence_with_conn(
                c, conversation_id=conversation_id, subject_key=subject_key, milestone=milestone,
                source_type=source_type, source_id=source_id,
            )


def infer_grounded_progress_event(user_text: str) -> tuple[str, str] | None:
    """Recognize only explicit milestones for the small declared game plan."""
    text = " ".join(user_text.casefold().split())
    subject = next((game for game in _SUPPORTED_MULTISTEP_GAMES if game in text), None)
    if subject is None:
        return None
    if any(marker in text for marker in ("끝냈", "완료", "다 했", "한 판 했")):
        return subject, "activity_completed"
    if "규칙" in text and any(marker in text for marker in ("알았", "배웠", "이해했", "설명")):
        return subject, "rules_learned"
    if any(marker in text for marker in ("시작", "시작했", "시작할", "하고 있어")):
        return subject, "activity_started"
    return None


async def apply_grounded_goal_progress_from_event(
    pool: asyncpg.Pool,
    *,
    conversation_id: UUID,
    user_text: str,
    source_id: str,
) -> int:
    """Use a concrete user event, never time, Need, or repeated desire, as progress."""
    event = infer_grounded_progress_event(user_text)
    if event is None:
        return 0
    subject_key, milestone = event
    return await apply_goal_progress_evidence(
        pool, conversation_id=conversation_id, subject_key=subject_key, milestone=milestone,
        source_type="grounded_event", source_id=source_id,
    )

async def update_goals(pool: asyncpg.Pool, conversation_id: UUID, text: str, source_id: UUID | None = None, *, working_memory: Any | None = None, epistemic_unknown: bool = False) -> GoalsNeedsTurnResult:
    """Apply grounded signals, then form short-lived candidate goals only."""
    normalized = ' '.join(text.casefold().split()); source = str(source_id) if source_id else None
    # Keep the snapshot and all same-turn signals on one connection.  Each
    # signal keeps its own atomic Need/event transaction while avoiding remote
    # acquire churn between the deterministic stages of a single turn.
    async with pool.acquire() as c:
        needs = await _get_need_snapshot_with_conn(c)
        if normalized not in SHORT and len(normalized) >= 4:
            if any(token in normalized for token in ('도와줘','어떻게 하지','설명해줘','코드 좀 봐','help me','fix','debug')):
                await _apply_with_conn(c, needs, 'helpfulness', .03, 'explicit_help_request', 'message', source, conversation_id)
            if any(token in normalized for token in ('뭐 하고 싶어','뭘 할까','골라')):
                await _apply_with_conn(c, needs, 'activity', .015, 'activity_choice_offered', 'message', source, conversation_id)
            if any(token in normalized for token in ('같이','대화','얘기')):
                await _apply_with_conn(c, needs, 'social_connection', .015, 'warm_interaction', 'message', source, conversation_id)
            if epistemic_unknown and _target(text, working_memory):
                await _apply_with_conn(c, needs, 'curiosity', .03, 'unknown_active_topic', 'message', source, conversation_id)
            if any(item.slot_type == 'open_loop' for item in getattr(working_memory, 'items', [])):
                await _apply_with_conn(c, needs, 'understanding', .025, 'open_loop', 'working_memory', source, conversation_id)
        # The remaining lifecycle and formation work uses one fresh, turn-local
        # goal set.  It replaces overlapping active/candidate/count reads, but
        # keeps each durable mutation at its existing statement boundary.
        active_goals, goal_rows = await _refresh_goal_lifecycle_with_conn(c, conversation_id, working_memory)
        created_goals = await _form_goals_with_conn(
            c, conversation_id, needs, _target(text, working_memory), source,
            active_goals=active_goals, known_goal_rows=goal_rows,
        )
    relevant_goals = tuple(sorted(
        (goal for goal in active_goals if goal.conversation_id == conversation_id and goal.status == 'active'),
        key=lambda goal: goal.priority,
        reverse=True,
    )[:2])
    return GoalsNeedsTurnResult(needs, tuple(created_goals), relevant_goals)

def _goal_from_row(row: dict[str, Any]) -> Goal:
    return Goal(str(row['id']),str(row['goal_key']),str(row['summary']),str(row['origin_need']),float(row['priority']),str(row['status']),float(row['progress']),float(row['confidence']),UUID(str(row['conversation_id'])) if row['conversation_id'] else None,str(row['source_type']),str(row['source_id']) if row['source_id'] else None,row['updated_at'],row['expires_at'])

async def refresh_goal_lifecycle(pool: asyncpg.Pool, conversation_id: UUID | None, working_memory: Any | None = None, *, now: datetime | None = None) -> list[Goal]:
    """Lazy, grounded lifecycle transitions; never inspects LLM prose."""
    async with pool.acquire() as c:
        active_goals, _goal_rows = await _refresh_goal_lifecycle_with_conn(c, conversation_id, working_memory, now=now)
    return active_goals

async def _refresh_goal_lifecycle_with_conn(c: Any, conversation_id: UUID | None, working_memory: Any | None = None, *, now: datetime | None = None) -> tuple[list[Goal], dict[str, dict[str, Any]]]:
    """Apply lifecycle transitions from one request-scoped active/candidate set."""
    current = now or _now()
    has_open_loop = any(item.slot_type == 'open_loop' for item in getattr(working_memory, 'items', []))
    rows = [dict(row) for row in await c.fetch(
        "select * from diana_goals where status in ('candidate','active')"
    )]
    goal_rows = {str(row['id']): row for row in rows}
    active_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    for row in rows:
        if row['expires_at'] is not None and row['expires_at'] <= current:
            await c.execute("update diana_goals set status='expired',updated_at=$1 where id=$2", current, row['id'])
            row['status'], row['updated_at'] = 'expired', current
            continue
        if conversation_id is not None and not has_open_loop and row['conversation_id'] == conversation_id and row['origin_need'] == 'understanding' and row['goal_type'] == 'open_loop_clarification':
            await c.execute("update diana_goals set status='satisfied',progress=1.0,updated_at=$1 where id=$2", current, row['id'])
            row['status'], row['progress'], row['updated_at'] = 'satisfied', 1.0, current
            continue
        if row['status'] == 'active':
            active_rows.append(row)
        else:
            candidate_rows.append(row)
    active_goals = [_goal_from_row(row) for row in sorted(active_rows, key=lambda row: row['priority'], reverse=True)]
    slots = max(0, 3-len(active_goals))
    if slots:
        active_by_conversation: dict[UUID, int] = {}
        for goal in active_goals:
            if goal.conversation_id is not None:
                active_by_conversation[goal.conversation_id] = active_by_conversation.get(goal.conversation_id, 0) + 1
        for candidate in sorted(candidate_rows, key=lambda row: (row['priority'], row['updated_at']), reverse=True):
            if slots <= 0:
                break
            # v2 candidates from an assistant's own future desire are allowed
            # to accumulate evidence before taking an active slot.  Existing
            # v1 need-generated candidates retain their established behavior.
            if (candidate['source_type'] == 'self_expression'
                    and float(candidate['confidence']) < SELF_EXPRESSION_PROMOTION_THRESHOLD):
                continue
            candidate_conversation = candidate['conversation_id']
            # SQL's ``conversation_id=$1`` yields zero for NULL; retain that
            # legacy behavior rather than giving global candidates a new limit.
            count = active_by_conversation.get(candidate_conversation, 0) if candidate_conversation is not None else 0
            if count >= 2:
                continue
            await c.execute("update diana_goals set status='active',updated_at=$1 where id=$2", current, candidate['id'])
            candidate['status'], candidate['updated_at'] = 'active', current
            goal = _goal_from_row(candidate)
            active_goals.append(goal)
            if candidate_conversation is not None:
                active_by_conversation[candidate_conversation] = count + 1
            slots -= 1
    return active_goals, goal_rows

async def _form_goals(pool: asyncpg.Pool, conversation_id: UUID, needs: dict[str, Need], target: str | None, source_id: str | None, *, active_goals: list[Goal] | None = None) -> list[Goal]:
    if not target:
        return []
    async with pool.acquire() as c:
        return await _form_goals_with_conn(c, conversation_id, needs, target, source_id, active_goals=active_goals)

async def _form_goals_with_conn(c: Any, conversation_id: UUID, needs: dict[str, Need], target: str | None, source_id: str | None, *, active_goals: list[Goal] | None = None, known_goal_rows: dict[str, dict[str, Any]] | None = None) -> list[Goal]:
    if not target:
        return []
    eligible = [key for key in ('curiosity', 'understanding') if needs[key].value >= .65]
    base_keys = [f'{key}:{target.casefold()}' for key in eligible]
    rows_by_key: dict[str, list[dict[str, Any]]] = {}
    if known_goal_rows is not None:
        for row in known_goal_rows.values():
            if row['goal_key'] in base_keys:
                rows_by_key.setdefault(str(row['goal_key']), []).append(row)
    lookup_keys = [key for key in base_keys if key not in rows_by_key]
    if lookup_keys:
        rows = await c.fetch(
            "select * from diana_goals where goal_key in (" + ", ".join(f'${index}' for index in range(1, len(lookup_keys) + 1)) + ")",
            *lookup_keys,
        )
        for row in rows:
            rows_by_key.setdefault(str(row['goal_key']), []).append(dict(row))
    created=[]
    for key in eligible:
        need=needs[key]
        base_key=f'{key}:{target.casefold()}'
        goal_key=base_key
        same_key_rows = rows_by_key.get(base_key, [])
        row=next((item for item in same_key_rows if item['status'] in ('candidate','active')), None)
        priority=_clamp(need.value*.8)
        if row:
            now = _now()
            await c.execute("update diana_goals set priority=$1,updated_at=$2 where id=$3",priority,now,row['id'])
            row['priority'], row['updated_at'] = priority, now
            if active_goals is not None:
                for goal in active_goals:
                    if goal.id == str(row['id']): goal.priority, goal.updated_at = priority, now; break
            continue
        terminal=bool(same_key_rows)
        if terminal:
            # Preserve terminal history and give the renewed grounded source
            # a distinct lifecycle instance without reviving it.
            goal_key=f'{base_key}:renewed:{source_id or uuid4()}'
        active=len(active_goals) if active_goals is not None else await c.fetchval("select count(*) from diana_goals where status='active'")
        status='active' if active < 3 else 'candidate'; now=_now(); identifier=uuid4()
        summary=f"Clarify {target}" if key=='understanding' else f"Learn more about {target}"
        await c.execute("""insert into diana_goals(id,goal_key,goal_type,summary,origin_need,priority,status,progress,confidence,conversation_id,source_type,source_id,created_at,updated_at,expires_at,metadata)
            values($1,$2,'short_term',$3,$4,$5,$6,0,.8,$7,'message',$8,$9,$9,$10,'{}')""",identifier,goal_key,summary,key,priority,status,conversation_id,source_id,now,now+timedelta(hours=24))
        goal=Goal(str(identifier),goal_key,summary,key,priority,status,0,.8,conversation_id,'message',source_id,now,now+timedelta(hours=24))
        created.append(goal)
        rows_by_key.setdefault(goal_key, []).append({"id": identifier, "goal_key": goal_key, "status": status, "priority": priority, "updated_at": now})
        if status == 'active' and active_goals is not None: active_goals.append(goal)
    return created

async def get_active_goals(pool: asyncpg.Pool) -> list[Goal]: return await get_relevant_goals(pool, None, None)
async def get_relevant_goals(pool: asyncpg.Pool, conversation_id: UUID | None, working_memory_snapshot: Any | None) -> list[Goal]:
    query="select * from diana_goals where status='active'" + (" and conversation_id=$1" if conversation_id else "") + " order by priority desc limit 2"
    async with pool.acquire() as c: rows=await c.fetch(query, conversation_id) if conversation_id else await c.fetch(query)
    return [_goal_from_row(row) for row in rows]
