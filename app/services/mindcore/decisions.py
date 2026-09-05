"""Own deterministic Decision detection, persistence, and Episode provenance."""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import asyncpg


@dataclass(frozen=True)
class DecisionCandidate:
    decision_type: str
    chosen: str
    confidence: float
    reason: str | None = None
    options: tuple[str, ...] = ()


_ACCEPT = ("응, 좋아", "응 좋아", "그래, 좋아", "그렇게 하자", "좋아!", "알겠어", "좋아")
_REJECT = ("싫어", "안 할래", "하지 말자", "그건 안")
_OFFER = ("할까", "할래", "하지 말까", "하지 말자", "해줄까", "해줄래", "읽어줄까", "읽어줄래", "읽어 줄까", "읽어 줄래", "같이", "앞으로", "어때")
_FUTURE_CHOICE = ("무슨 동화", "뭐 듣고 싶", "어떤 이야기", "뭘 읽고 싶")
_UNKNOWN_DETAIL = re.compile(r"(?:누가\s*이기|이기는|졌|지[느는]지|빠르|경주|늑대|할머니|숲|바다|결말|주인공)")
DECISION_TERMINAL_STATUSES = frozenset({"executed", "superseded", "cancelled", "expired"})
_GAME_MARKERS = ("업다운", "스무고개", "게임", "놀이")
_STORY_MARKERS = ("동화", "이야기", "읽", "들려", "듣")
_ACTIVITY_MARKERS = ("산책", "그림 그리기", "책 읽기", "공부", "만들기")
_EXECUTION_START = ("시작", "시작했", "시작할", "하고 있어", "해볼게")
_EXECUTION_COMPLETE = ("끝냈", "완료", "다 했", "한 판 했")
_CANCEL = ("안 할래", "하지 말자", "그건 안", "취소")
logger = logging.getLogger("diana.decisions")

# Candidates retain their confidence so downstream owners can distinguish a
# title-only soft choice from a concrete commitment.  The existing detector
# never emitted a score below .78; making that minimum explicit preserves its
# prior captures while preventing any future low-confidence helper from being
# durably recorded by accident.
DECISION_DURABLE_THRESHOLD = 0.78
SELF_DIRECTED_CHOICE_CONFIDENCE = 0.91


def _normalise_choice(value: str) -> str:
    return " ".join(value.casefold().split()).strip(" .,!?")


def _clean_self_choice(value: str) -> str:
    value = " ".join(value.split()).strip(" .,!?")
    return re.sub(r"(?:은|는|을|를|이|가)$", "", value).strip()


def _is_question_or_future_desire(reply: str) -> bool:
    return (
        "?" in reply
        or any(token in reply for token in ("할까", "어때", "해볼까", "하고 싶", "해보고 싶", "싶어", "언젠가", "나중에"))
    )


def _self_directed_choice(diana_text: str) -> DecisionCandidate | None:
    """Recognise a bounded, assistant-owned commitment without an LLM call.

    This intentionally does *not* turn a question, positive evaluation, or
    open-ended future desire into a Decision.  The target must be attached to
    a concrete game/story/activity commitment in Diana's own saved reply.
    """
    reply = " ".join(diana_text.casefold().split())
    if _is_question_or_future_desire(reply):
        return None
    if any(token in reply for token in ("재밌", "좋아", "싫어")) and not any(
        token in reply for token in ("하자", "할래", "할게", "고를래", "정할게", "정하자", "해보자")
    ):
        return None

    commitment = r"(?:할래|하자|할게|고를래|정할래|정할게|정하자|해보자)"
    # Keep story titles whole (including title-internal '와') and take the
    # selection after the explicit story frame, rather than guessing a title
    # from arbitrary assistant prose.
    match = re.search(
        rf"(?:다음\s*(?:이야기|동화)(?:는|은)?\s*)(?P<choice>.{{1,80}}?)(?:으로|로)\s*{commitment}",
        reply,
    )
    if match:
        return DecisionCandidate("explicit_choice", _clean_self_choice(match.group("choice")), SELF_DIRECTED_CHOICE_CONFIDENCE)

    # Named games are deliberately bounded; the generic phrase "먼저 X 하자"
    # is not enough to create a durable Decision.
    match = re.search(rf"(?P<choice>업다운|스무고개)(?:부터|으로|로)?\s*{commitment}", reply)
    if match:
        return DecisionCandidate("explicit_choice", _clean_self_choice(match.group("choice")), SELF_DIRECTED_CHOICE_CONFIDENCE)

    # A direct replacement is a concrete same-domain supersession when the
    # replacement itself is a supported game.
    match = re.search(rf"(?:업다운|스무고개)\s*대신\s*(?P<choice>업다운|스무고개)(?:부터|으로|로)?\s*{commitment}", reply)
    if match:
        return DecisionCandidate("explicit_choice", _clean_self_choice(match.group("choice")), SELF_DIRECTED_CHOICE_CONFIDENCE)

    # Activity support remains intentionally small: it captures an activity
    # with follow-through value, not conversational ordering such as "학교
    # 얘기부터 듣자".
    for activity in _ACTIVITY_MARKERS:
        if re.search(rf"{re.escape(activity)}(?:을|를|부터|으로|로)?\s*{commitment}", reply):
            return DecisionCandidate("explicit_choice", activity, SELF_DIRECTED_CHOICE_CONFIDENCE)
    return None


def _choices_from_prompt(user_text: str) -> list[str]:
    before_middle = re.split(r"\s*(?:중|가운데)\s*", user_text, maxsplit=1)[0]
    before_middle = before_middle.rsplit(".", 1)[-1].strip()
    # A comma/solidus list is unambiguous. Do not then split title-internal
    # conjunctions such as "토끼와 거북이" into invented separate options.
    values = re.split(r"\s*(?:,|/|·)\s*", before_middle)
    if len(values) == 1:
        values = re.split(r"\s*(?:또는|or)\s*", before_middle)
    values = [value.strip(" .?!\"'“”") for value in values]
    # One-character options (A/B/C, 1/2/3) are legitimate only because this
    # function is called after a choice-request context has already matched.
    return [value for value in values if 1 <= len(value) <= 80][:6]


def extract_choice_options(user_text: str) -> list[str]:
    """Return only explicit user-provided choice lists, never model guesses."""
    text = " ".join(user_text.casefold().split())
    return _choices_from_prompt(user_text) if "중" in text or "골라" in text or "선택" in text else []


def _explicit_reason(reply: str) -> str | None:
    text = " ".join(reply.split())
    if "눈에 띄" in text:
        return "이름이 눈에 띄어서"
    if "궁금" in text:
        return "더 궁금해서"
    if "끌려" in text:
        return "더 끌려서"
    return None


def detect_decision(user_text: str, diana_text: str, *, prior_options: list[str] | None = None) -> DecisionCandidate | None:
    """Capture a grounded offered choice or Diana's explicit self-commitment."""
    prompt = " ".join(user_text.casefold().split())
    reply = " ".join(diana_text.casefold().split())
    choices = _choices_from_prompt(user_text) if "중" in prompt or "골라" in prompt or "선택" in prompt else []
    for choice in choices:
        if choice.casefold() in reply:
            if any(token in reply for token in ("할래", "고를래", "먼저 보고 싶", "선택할래")):
                return DecisionCandidate("explicit_choice", choice, 0.92, _explicit_reason(diana_text), tuple(choices))
            if any(token in reply for token in ("궁금", "끌려", "눈에 띄")):
                return DecisionCandidate("soft_choice", choice, 0.78, _explicit_reason(diana_text), tuple(choices))
    if any(marker in prompt for marker in _FUTURE_CHOICE):
        for choice in prior_options or []:
            if choice.casefold() in reply and any(token in reply for token in ("듣고 싶", "읽고 싶", "할래", "궁금", "끌려")):
                return DecisionCandidate("future_choice", choice, 0.78, _explicit_reason(diana_text), ())
    # A later offer may refer to an already user-provided option list.  Reuse
    # that grounded list; never infer an option from the model's vocabulary.
    if "?" in user_text and any(marker in prompt for marker in _OFFER):
        for choice in prior_options or []:
            if choice.casefold() in reply and any(token in reply for token in ("할래", "먼저", "골라", "선택")):
                return DecisionCandidate("explicit_choice", choice, 0.90, _explicit_reason(diana_text), tuple(prior_options))
    if "?" in user_text and any(marker in prompt for marker in _OFFER):
        if any(marker in reply for marker in _REJECT):
            return DecisionCandidate("explicit_reject", "reject", 0.88, None, ())
        if any(marker in reply for marker in _ACCEPT):
            return DecisionCandidate("explicit_accept", "accept", 0.88, None, ())
    return _self_directed_choice(diana_text)


def decision_rejection_reason(user_text: str, diana_text: str) -> str:
    """Return safe, content-free funnel metadata for a non-candidate reply."""
    reply = " ".join(diana_text.casefold().split())
    if "?" in reply or any(token in reply for token in ("할까", "어때", "해볼까")):
        return "question_only"
    if any(token in reply for token in ("하고 싶", "해보고 싶", "싶어", "언젠가", "나중에")):
        return "goal_only"
    if any(token in reply for token in ("재밌", "좋아", "싫어", "끌려", "궁금")):
        return "preference_or_curiosity_only"
    if any(token in user_text.casefold() for token in ("해.", "하자", "해줘", "시작해")):
        return "user_command_without_self_commitment"
    return "no_explicit_commitment"


def is_durable_decision(candidate: DecisionCandidate) -> bool:
    return candidate.confidence >= DECISION_DURABLE_THRESHOLD


def decision_domain(candidate: DecisionCandidate, *, user_text: str = "", diana_text: str = "") -> str | None:
    """Return only a narrow, deterministic domain safe for supersession."""
    text = " ".join((user_text + " " + diana_text + " " + candidate.chosen + " " + " ".join(candidate.options)).casefold().split())
    if any(marker in text for marker in _GAME_MARKERS):
        return "game"
    if any(marker in text for marker in _STORY_MARKERS):
        return "story"
    if any(marker in text for marker in _ACTIVITY_MARKERS):
        return "activity"
    return None


async def sanitize_decision_reason(pool: asyncpg.Pool, candidate: DecisionCandidate, diana_text: str) -> DecisionCandidate:
    """Never persist a title-only choice reason that contains unlearned story detail."""
    if candidate.reason is None or candidate.chosen in {"accept", "reject"}:
        return candidate
    key = re.sub(r"[^0-9a-z가-힣]+", "_", candidate.chosen.casefold()).strip("_")
    async with pool.acquire() as connection:
        known = await connection.fetchval("select 1 from diana_knowledge where subject_key=$1 limit 1", key)
    if not known and _UNKNOWN_DETAIL.search(diana_text):
        return DecisionCandidate(candidate.decision_type, candidate.chosen, candidate.confidence, None, candidate.options)
    return candidate


async def record_decision(
    pool: asyncpg.Pool,
    *,
    candidate: DecisionCandidate,
    episode_id: UUID | None,
    conversation_id: UUID | None = None,
    user_message_id: UUID | None = None,
    assistant_message_id: UUID | None = None,
    user_text: str = "",
    diana_text: str = "",
) -> dict:
    """Persist a new active Decision and atomically supersede its same-domain predecessor."""
    timestamp = datetime.now(timezone.utc)
    domain = decision_domain(candidate, user_text=user_text, diana_text=diana_text)
    async with pool.acquire() as connection:
        async with connection.transaction():
            if conversation_id is not None:
                active_rows = await connection.fetch(
                    "select * from decision_log where conversation_id=$1 and decision_domain is $2 and status='active'",
                    conversation_id, domain,
                )
                chosen = _normalise_choice(candidate.chosen)
                for active_row in active_rows:
                    if _normalise_choice(str(_payload(dict(active_row)).get("chosen") or "")) == chosen:
                        result = dict(active_row)
                        result["acquisition_result"] = "duplicate"
                        return result
            if conversation_id is not None and domain is not None:
                await connection.execute(
                    """update decision_log set status='superseded',resolved_at=$1,updated_at=$1
                       where conversation_id=$2 and decision_domain=$3 and status='active'""",
                    timestamp, conversation_id, domain,
                )
            row = await connection.fetchrow(
                """insert into decision_log(id, target, old_value, new_value, reason, source_episode_ids, created_at,
                                               conversation_id, decision_domain, status, updated_at, resolved_at)
                   values($1,$2,$3,$4,$5,$6,$7,$8,$9,'active',$7,null) returning *""",
                uuid4(), "diana_decision", None,
                {"decision_type": candidate.decision_type, "chosen": candidate.chosen, "options": list(candidate.options) or None,
                 "confidence": candidate.confidence, "conversation_id": str(conversation_id) if conversation_id else None,
                 "user_message_id": str(user_message_id) if user_message_id else None, "assistant_message_id": str(assistant_message_id) if assistant_message_id else None},
                candidate.reason, [str(episode_id)] if episode_id else [], timestamp,
                conversation_id, domain,
            )
    result = dict(row)
    result["acquisition_result"] = "created"
    return result


async def link_decision_episode(pool: asyncpg.Pool, *, decision_id: UUID, episode_id: UUID) -> None:
    """Attach Episode provenance without exposing ``decision_log`` JSON to callers."""
    async with pool.acquire() as connection:
        await connection.execute("update decision_log set source_episode_ids=$1,updated_at=$2 where id=$3", [str(episode_id)], datetime.now(timezone.utc), decision_id)


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("new_value")
    return value if isinstance(value, dict) else {}


async def cancel_active_decision_from_reply(pool: asyncpg.Pool, conversation_id: UUID, diana_text: str) -> int:
    """Cancel only an explicitly named, currently active Decision."""
    reply = " ".join(diana_text.casefold().split())
    if not any(marker in reply for marker in _CANCEL):
        return 0
    async with pool.acquire() as connection:
        async with connection.transaction():
            rows = await connection.fetch("select id,new_value from decision_log where conversation_id=$1 and status='active'", conversation_id)
            matching = [row for row in rows if (chosen := str(_payload(row).get("chosen") or "").casefold()) and chosen in reply]
            if not matching:
                return 0
            now = datetime.now(timezone.utc)
            for row in matching:
                await connection.execute("update decision_log set status='cancelled',resolved_at=$1,updated_at=$1 where id=$2", now, row["id"])
    return len(matching)


async def apply_grounded_decision_execution(
    pool: asyncpg.Pool,
    *,
    conversation_id: UUID,
    user_text: str,
    episode_id: UUID | None = None,
) -> int:
    """Mark a named active choice executed only from a grounded start/completion event.

    Goal progress is handled by the same grounded event in the Goal owner;
    a Decision never completes an unrelated Goal by itself.
    """
    text = " ".join(user_text.casefold().split())
    milestone = "activity_completed" if any(marker in text for marker in _EXECUTION_COMPLETE) else "activity_started" if any(marker in text for marker in _EXECUTION_START) else None
    if milestone is None:
        return 0
    async with pool.acquire() as connection:
        async with connection.transaction():
            rows = await connection.fetch("select id,new_value,source_episode_ids from decision_log where conversation_id=$1 and status='active'", conversation_id)
            matching = [row for row in rows if (chosen := str(_payload(row).get("chosen") or "").casefold()) and chosen in text]
            if not matching:
                return 0
            now = datetime.now(timezone.utc)
            for row in matching:
                episode_ids = row.get("source_episode_ids") if isinstance(row.get("source_episode_ids"), list) else []
                if episode_id is not None and str(episode_id) not in episode_ids:
                    episode_ids = [*episode_ids, str(episode_id)]
                await connection.execute(
                    "update decision_log set status='executed',resolved_at=$1,updated_at=$1,source_episode_ids=$2 where id=$3",
                    now, episode_ids, row["id"],
                )
    return len(matching)
