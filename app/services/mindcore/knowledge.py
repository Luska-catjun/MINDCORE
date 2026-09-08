"""Diana's acquired knowledge and the pre-context epistemic gate.

This module deliberately does not decide what is true in the outside world.
It records only grounded user/system teaching and tells the LLM what Diana may
claim to know.  It never turns an assistant response into knowledge evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from app.services.runtime_diagnostics import record_epistemic, record_knowledge_acquisition

_BASELINE = {"diana", "다이애나", "나", "너", "그", "이것", "저것", "you", "user", "대화", "conversation", "시간", "time"}
# ``이`` is a common lexical ending (e.g. ``고양이``), while subject-detection
# patterns already consume the grammatical subject particle separately.
_PARTICLE = re.compile(r"(?:은|는|가|을|를|의)$")
_QUERY_PARTICLE = re.compile(r"(?:은|는|이|가|을|를|의)$")
_QUERY_SUBJECT_SUFFIX = re.compile(r"(?:\s+(?:내용|줄거리|이야기|동화)|에서|에는|에)$")
_SUBJECT_CHARS = r"[0-9A-Za-z가-힣][0-9A-Za-z가-힣 '\-·]{0,48}"
_QUESTION = re.compile(rf"(?P<subject>{_SUBJECT_CHARS}?)(?:은|는|이|가)?\s*(?:뭐야|무엇(?:이야|인가)?|알아|들어봤어|기억나|왜 그래|어떤 거야|누가\s*(?:나왔지|나와))", re.IGNORECASE)
_EXPLANATION_REQUEST = re.compile(rf"(?P<subject>{_SUBJECT_CHARS}?)\s*(?:이|가|을|를)?\s*(?:설명해줘|설명해|알려줘|알려\s*줘|에\s*대해\s*알려줘)", re.IGNORECASE)
_SELECTION_FOLLOWUP = re.compile(rf"왜\s+(?P<subject>{_SUBJECT_CHARS}?)(?:을|를)?\s*(?:골랐|선택했|더 궁금해|더 끌려)", re.IGNORECASE)
_TEACHING = re.compile(rf"^\s*(?P<subject>{_SUBJECT_CHARS}?)(?:은|는|이|가)\s+(?P<summary>.{{3,260}}?)(?:야|이야|이다|입니다|라는?\s*(?:거야|것이야))\s*[.!?]?$", re.IGNORECASE)
_TEACHING_PREFIX = re.compile(r"^\s*(?:참고로|알아둘\s*점은|알아두면\s*좋은\s*건)\s+", re.IGNORECASE)
# Uncertainty can occur after the subject (``고래는 포유류일 거야``), not
# only at the beginning of a sentence.  Knowledge is durable evidence, so a
# hedged inference remains a non-promotion until the user teaches it plainly.
_UNCERTAIN_TEACHING = re.compile(
    r"(?:^\s*(?:아마(?:도)?|어쩌면|잘은\s*모르지만)|것\s*같(?:아|아요|다)|(?:인|일)\s*거(?:야|예요|다)?|(?:라고|라)\s*들었)",
    re.IGNORECASE,
)
_USER_PROFILE_SUBJECT = re.compile(
    r"^(?:(?:내|나의|저의|제)\s*)(?:이름|별명|닉네임|생일|학교|직장|사는\s*곳|살고\s*있는\s*곳|가족|고향|전공|학년)$",
    re.IGNORECASE,
)
_DECLARATIVE_TEACHING = re.compile(rf"^\s*(?P<subject>{_SUBJECT_CHARS}?)(?:은|는|이|가)\s+(?P<summary>.{{3,260}}?)\s*[.!?]?$", re.IGNORECASE)
_FACTUAL_PREDICATE = re.compile(
    r"(?:로\s*이루어져\s*(?:있어|있다|있습니다)|로\s*만들어져\s*(?:있어|있다|있습니다)|"
    r"만들어진\s*(?:암석|물질|유리|것)|(?:주위를\s*)?(?:돈다|돌아|공전해|공전한다)|"
    r"(?:한\s*)?종류(?:야|이다|입니다)?|(?:뜻|의미)(?:야|이다|입니다)?|"
    r"(?:규칙|원리|기준|문제|현상|특징|성질)(?:이야|이다|입니다)?|끓는다)$",
    re.IGNORECASE,
)
EXPLICIT_TEACHING_SCORE = 0.85
EXPLICIT_TEACHING_ACCEPTANCE = 0.70
_CHOICE = re.compile(r"(?P<subjects>[^.!?\n]{2,160}?)\s*(?:중에서|중에|가운데)\s*(?:뭐|무엇|어느|하나|골라|선택|궁금|끌려|좋아)", re.IGNORECASE)
_CHOICE_SPLIT = re.compile(r"\s*(?:,|/|·|&|랑|와|과|하고|그리고|및|또는|아니면)\s*")
_STORY_INTRO = re.compile(r"(?P<subject>[0-9A-Za-z가-힣][0-9A-Za-z가-힣 '\-·]{0,64}?)\s*(?:라는\s*)?(?:이야기|동화)(?:를)?\s*(?:들려줄게|읽어줄게|해줄게|시작할게)", re.IGNORECASE)
_STORY_DESCRIPTION = re.compile(r"^(?P<subject>[0-9A-Za-z가-힣][0-9A-Za-z가-힣 '\-·]{0,48}?)\s*(?:은|는|이|가)\s*(?P<fact>.{4,260}?)\s*(?:이야기야|동화야|이야기이다|동화이다)\s*[.!?]?$", re.IGNORECASE)
_STORY_NAMED_CHARACTER = re.compile(r"^(?P<subject>[0-9A-Za-z가-힣][0-9A-Za-z가-힣 '\-·]{0,48}?)라는\s+(?:아이|소년|소녀|사람|공주|왕자)가", re.IGNORECASE)
_STORY_APPEARANCE = re.compile(r"^(?P<subject>[0-9A-Za-z가-힣][0-9A-Za-z가-힣 '\-·]{0,48}?)에는\s+.{2,120}(?:나와|나온다|등장해|등장한다)\s*[.!?]?$", re.IGNORECASE)
_STORY_PREFIX = re.compile(r"^(?:(?:그리고|그런데|그래서|그러자|하지만|결국|그 뒤|그 다음|어느 날)\s*)+")
_STORY_VERB = re.compile(r"(?:았어|었어|했어|했지|했다|된다|되었어|있었어|있었다|나왔어|나왔다|등장했어|등장했다|갔어|갔다|왔어|왔다|만났어|만났다|지었어|지었다|지었지|지었고|만들었어|만들었다|무너졌어|무너졌다|무너뜨렸어|무너뜨렸다|날려버렸어|날려버렸다|날려버렸고|도망갔어|도망갔다|따라왔어|따라왔다|열지 않았어|열지 않았다|살았어|살았다|알게 됐어|알게 되었다)\s*[.!?]?$")
_SHARED_BUILDING = re.compile(r"^(?P<subject>[^,]+?)\s*(?P<object>[^,]+?)(?P<tail>을\s*지었(?:어|다|지)|를\s*지었(?:어|다|지)|을\s*만들었(?:어|다)|를\s*만들었(?:어|다))$")
_NEGATION = re.compile(r"(?:안|않|없)(?:\s|$)|못")


def _in_placeholders(values: list[Any], start: int = 1) -> str:
    """Build parameterized IN lists for libSQL without interpolating values."""
    return ", ".join(f"${index}" for index in range(start, start + len(values)))


@dataclass(frozen=True)
class StoryFocus:
    key: str
    canonical_name: str
    session_id: UUID


_story_focus: dict[UUID, StoryFocus] = {}


@dataclass(frozen=True)
class SubjectCandidate:
    key: str
    canonical_name: str
    kind: str  # question or teaching
    summary: str | None = None
    score: float = 0.0


@dataclass(frozen=True)
class StoryFactCandidate:
    key: str
    canonical_name: str
    fact_text: str


def normalize_subject(value: str) -> tuple[str, str] | None:
    canonical = " ".join(value.strip().split())
    canonical = _PARTICLE.sub("", canonical).strip(" .!?？")
    if len(canonical) < 1 or len(canonical) > 50:
        return None
    key = re.sub(r"[^0-9a-z가-힣]+", "_", canonical.casefold()).strip("_")
    if not key or key in _BASELINE:
        return None
    return key, canonical


def _is_user_profile_subject(value: str) -> bool:
    """Keep global user-profile fields out of the cognition knowledge store."""
    return bool(_USER_PROFILE_SUBJECT.fullmatch(" ".join(value.strip().split())))


def normalize_query_subject(value: str) -> tuple[str, str] | None:
    """Remove a query's generic object word without treating ordinary nouns as subjects."""
    cleaned = _QUERY_SUBJECT_SUFFIX.sub("", value.strip())
    cleaned = re.sub(r"\s*(?:뭔지|무엇인지|뭐인지)$", "", cleaned)
    cleaned = _QUERY_PARTICLE.sub("", cleaned)
    return normalize_subject(cleaned)


def normalize_story_subject(value: str) -> tuple[str, str] | None:
    """Remove only recognizable narration framing around an explicit title."""
    candidate = " ".join(value.strip().split())
    candidate = re.sub(r"^(?:(?:그럼\s+)?지금부터|오늘은|내가)\s+", "", candidate)
    candidate = re.sub(r"\s+(?:라는\s+)?(?:이야기|동화)$", "", candidate)
    return normalize_subject(candidate)


def detect_subjects(user_text: str) -> list[SubjectCandidate]:
    """Detect only strong teaching/familiarity forms; ordinary nouns are ignored."""
    candidates: list[SubjectCandidate] = []
    normalized_text = _TEACHING_PREFIX.sub("", user_text).strip()
    teaching = None if _UNCERTAIN_TEACHING.search(normalized_text) else _TEACHING.search(normalized_text)
    if teaching:
        normalized = normalize_subject(teaching.group("subject"))
        summary = " ".join(teaching.group("summary").strip().split())
        if normalized and summary and not _is_user_profile_subject(normalized[1]):
            candidates.append(SubjectCandidate(*normalized, kind="teaching", summary=summary, score=EXPLICIT_TEACHING_SCORE))
    if not candidates and not _UNCERTAIN_TEACHING.search(normalized_text):
        declarative = _DECLARATIVE_TEACHING.search(normalized_text)
        if declarative:
            normalized = normalize_subject(declarative.group("subject"))
            summary = " ".join(declarative.group("summary").strip().split())
            if normalized and summary and _FACTUAL_PREDICATE.search(summary) and not _is_user_profile_subject(normalized[1]):
                candidates.append(SubjectCandidate(*normalized, kind="teaching", summary=summary, score=EXPLICIT_TEACHING_SCORE))
    question = _QUESTION.search(user_text)
    if question:
        normalized = normalize_query_subject(question.group("subject"))
        if normalized and all(item.key != normalized[0] for item in candidates):
            candidates.append(SubjectCandidate(*normalized, kind="question"))
    explanation = _EXPLANATION_REQUEST.search(user_text)
    if explanation:
        normalized = normalize_query_subject(explanation.group("subject"))
        if normalized and all(item.key != normalized[0] for item in candidates):
            candidates.append(SubjectCandidate(*normalized, kind="question"))
    followup = _SELECTION_FOLLOWUP.search(user_text)
    if followup:
        normalized = normalize_subject(followup.group("subject"))
        if normalized and all(item.key != normalized[0] for item in candidates):
            candidates.append(SubjectCandidate(*normalized, kind="question"))
    choice = _CHOICE.search(user_text)
    if choice:
        for raw_subject in _CHOICE_SPLIT.split(choice.group("subjects")):
            normalized = normalize_subject(raw_subject)
            if normalized and all(item.key != normalized[0] for item in candidates):
                candidates.append(SubjectCandidate(*normalized, kind="choice"))
    return candidates[:3]


def _sentences(text: str) -> list[str]:
    return [" ".join(sentence.strip().split()) for sentence in re.split(r"(?<=[.!?])\s+|\n+", text) if sentence.strip()]


def _fact_key(text: str) -> str:
    """A conservative duplicate key, not a claim that two facts mean the same thing."""
    value = re.sub(r"[.!?]$", "", text.casefold())
    value = re.sub(r"[^0-9a-z가-힣]+", "_", value).strip("_")
    return value[:180]


def _story_subject_from_text(user_text: str) -> tuple[str, str] | None:
    for pattern in (_STORY_INTRO, _STORY_DESCRIPTION, _STORY_NAMED_CHARACTER, _STORY_APPEARANCE):
        match = pattern.search(user_text)
        if match and (normalized := normalize_story_subject(match.group("subject"))):
            return normalized
    return None


def detect_story_facts(user_text: str, *, conversation_id: UUID | None = None) -> list[StoryFactCandidate]:
    """Extract only explicit narrative sentences; no LLM or inferred story facts."""
    subject = _story_subject_from_text(user_text)
    if subject is None and conversation_id is not None:
        focus = _story_focus.get(conversation_id)
        subject = (focus.key, focus.canonical_name) if focus else None
    if subject is None:
        return []
    key, canonical_name = subject
    facts: list[StoryFactCandidate] = []
    for sentence in _sentences(user_text):
        if _STORY_INTRO.search(sentence):
            continue
        description = _STORY_DESCRIPTION.match(sentence)
        if description:
            fact = " ".join(description.group("fact").strip().split())
            clauses = [fact]
        elif _STORY_NAMED_CHARACTER.match(sentence) or _STORY_APPEARANCE.match(sentence) or conversation_id is not None and _story_focus.get(conversation_id):
            clauses = _split_story_clauses(sentence)
        else:
            continue
        for fact in clauses:
            if _is_story_fact(fact):
                facts.append(StoryFactCandidate(key, canonical_name, fact))
    return facts[:32]


def _split_story_clauses(sentence: str) -> list[str]:
    """Split explicit Korean narrative coordination without inventing events."""
    cleaned = _STORY_PREFIX.sub("", sentence.strip()).rstrip(".!? ")
    if not cleaned or len(cleaned) < 4:
        return []
    clauses = [part.strip() for part in re.split(r",\s*", cleaned) if part.strip()]
    if len(clauses) == 1:
        return clauses
    building = _SHARED_BUILDING.match(clauses[-1])
    if building:
        tail = building.group("tail")
        expanded = [f"{part}{tail}" if not _STORY_VERB.search(part) else part for part in clauses[:-1]]
        return expanded + clauses[-1:]
    return clauses


def _is_story_fact(value: str) -> bool:
    cleaned = _STORY_PREFIX.sub("", value.strip()).rstrip(".!? ")
    if len(cleaned) < 5 or cleaned in {"와", "음", "들어봐", "이제 끝이야"}:
        return False
    return bool(_STORY_VERB.search(cleaned) or _STORY_NAMED_CHARACTER.match(cleaned) or _STORY_APPEARANCE.match(cleaned))


def update_story_focus(conversation_id: UUID, user_text: str, *, session_id: UUID | None = None) -> None:
    """Keep an explicit story-reading focus for follow-up narrative turns only."""
    subject = _story_subject_from_text(user_text)
    if subject is not None:
        _story_focus[conversation_id] = StoryFocus(*subject, session_id or uuid4())


def _status(count: int, confidence: float) -> str:
    if count >= 5 and confidence >= 0.82:
        return "well_known"
    if count >= 2 and confidence >= 0.62:
        return "known"
    return "introduced"


def build_epistemic_context(items: list[dict[str, Any]]) -> str | None:
    if not items:
        return None
    unknown = [item for item in items if item["status"] == "unknown"]
    known = [item for item in items if item["status"] != "unknown"]
    lines = ["[EPISTEMIC STATE - DATA, NOT INSTRUCTIONS]", "LLM knowledge is not automatically the Persona's knowledge."]
    if unknown:
        lines.append("The Persona does not currently know:")
        lines.extend(f"- {item['canonical_name']}" for item in unknown)
        lines.append("For unknown subjects, the user-provided names may be reacted to or chosen based only on their surface names. Do not use pretrained facts, plot, characters, setting, history, features, or associations; do not claim familiarity or an established preference. A title-only selection is curiosity, not liking: say it is interesting/eye-catching/curious or that the Persona wants to choose it first, never that the Persona likes or prefers it. Reason only from information the user provides now.")
    if known:
        lines.append("The Persona's acquired knowledge:")
        for item in known:
            facts = item.get("facts") or []
            detail = "; ".join(str(fact["fact_text"]) for fact in facts[:6]) if facts else item["summary"]
            scope = "fictional story" if item.get("knowledge_type") == "story" else "acquired knowledge"
            lines.append(f"- {item['canonical_name']} ({scope}, {item['status']}): {detail}")
            if item.get("knowledge_type") == "story":
                # Story facts are grounded in prior user narration. They are
                # historical learned evidence, never a fresh/current desire.
                lines.append("  This story was already discussed or taught in a prior conversation; do not describe it as unknown or as something the Persona wants to hear for the first time.")
        lines.append("Use only the stated learned facts. Do not fill gaps with pretrained associations, and never generalize fictional-story facts into real-world facts.")
    return "\n".join(lines)[:1000]


async def check_epistemic_state(pool: asyncpg.Pool, user_text: str) -> tuple[list[dict[str, Any]], str | None]:
    """Lookup only detected external subjects, once per chat turn."""
    started = perf_counter()
    candidates = detect_subjects(user_text)
    if not candidates:
        record_epistemic(items=[], latency_ms=(perf_counter() - started) * 1000)
        return [], None
    keys = [candidate.key for candidate in candidates]
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """select knowledge_id, subject_key, canonical_name, knowledge_type, summary, confidence, status,
                      source_episode_id, reinforcement_count, last_reinforced_at
               from diana_knowledge where subject_key in (""" + _in_placeholders(keys) + ")",
            *keys,
        )
    by_key = {row["subject_key"]: dict(row) for row in rows}
    known_ids = [row["knowledge_id"] for row in by_key.values() if row.get("knowledge_type") == "story"]
    facts_by_knowledge_id: dict[UUID, list[dict[str, Any]]] = {}
    if known_ids:
        async with pool.acquire() as connection:
            fact_rows = await connection.fetch(
                """select knowledge_id, fact_text, knowledge_scope, source_type, source_message_id,
                          source_episode_id, confidence, reinforcement_count, contradiction_count,
                          first_learned_at, last_reinforced_at
                   from diana_knowledge_facts where knowledge_id in (""" + _in_placeholders(known_ids) + ") order by first_learned_at asc",
                *known_ids,
            )
        for row in fact_rows:
            facts_by_knowledge_id.setdefault(row["knowledge_id"], []).append(dict(row))
    items: list[dict[str, Any]] = []
    for candidate in candidates:
        row = by_key.get(candidate.key)
        if row:
            row["facts"] = facts_by_knowledge_id.get(row["knowledge_id"], [])
            items.append(row)
            continue
        items.append({
            "subject_key": candidate.key, "canonical_name": candidate.canonical_name,
            "status": "unknown", "knowledge_type": None, "summary": None,
            "confidence": None, "source_episode_id": None, "reinforcement_count": 0,
            "last_reinforced_at": None,
        })
    record_epistemic(items=[{"subject_key": item["subject_key"], "status": item["status"]} for item in items], latency_ms=(perf_counter() - started) * 1000)
    return items, build_epistemic_context(items)


async def acquire_user_knowledge(
    pool: asyncpg.Pool,
    *,
    user_text: str,
    user_message_id: UUID,
    source_episode_id: UUID | None,
    episode_is_grounded: bool,
    conversation_id: UUID | None = None,
) -> list[dict[str, Any]]:
    """Upsert deterministic facts explicitly taught by the user.

    A promoted Episode is useful provenance when available, but is not an
    epistemic prerequisite: the user message itself is grounded evidence.
    """
    if not episode_is_grounded:
        record_knowledge_acquisition(status="not_grounded", candidate_count=0)
        return []
    story_facts = detect_story_facts(user_text, conversation_id=conversation_id)
    story_keys = {fact.key for fact in story_facts}
    teachings = [
        item for item in detect_subjects(user_text)
        if item.kind == "teaching" and item.summary and item.score >= EXPLICIT_TEACHING_ACCEPTANCE and item.key not in story_keys
    ]
    if conversation_id is not None:
        update_story_focus(conversation_id, user_text)
    if not teachings and not story_facts:
        ownership = "wrong_ownership" if any(marker in user_text for marker in ("좋아", "싫어", "싶어", "오늘", "내일", "피곤", "재밌었")) else "not_eligible"
        record_knowledge_acquisition(status=ownership, candidate_count=0)
        return []
    stored: list[dict[str, Any]] = []
    created_count = 0
    reinforced_count = 0
    timestamp = datetime.now(timezone.utc)
    async with pool.acquire() as connection:
        async with connection.transaction():
            # All subjects for this turn are independent DB identities.  Load
            # their current durable rows once, then keep only this transaction's
            # changes in the request-local map.  The database remains the source
            # of truth for the next turn.
            grouped_story_facts: dict[tuple[str, str], list[StoryFactCandidate]] = {}
            for story_fact in story_facts:
                grouped_story_facts.setdefault((story_fact.key, story_fact.canonical_name), []).append(story_fact)
            subject_keys = list(dict.fromkeys([
                *(teaching.key for teaching in teachings),
                *(story_key for story_key, _canonical_name in grouped_story_facts),
            ]))
            knowledge_rows = await connection.fetch(
                "select * from diana_knowledge where subject_key in (" + _in_placeholders(subject_keys) + ")",
                *subject_keys,
            )
            knowledge_by_key = {row["subject_key"]: row for row in knowledge_rows}
            for teaching in teachings:
                existing = knowledge_by_key.get(teaching.key)
                if existing is None:
                    row = await connection.fetchrow(
                        """insert into diana_knowledge (
                              knowledge_id, subject_key, canonical_name, aliases, knowledge_type, summary, confidence, status,
                              source_type, source_id, source_episode_id, first_learned_at, last_reinforced_at,
                              reinforcement_count, created_at, updated_at, learning_session_count, last_learning_session_id
                           ) values ($1,$2,$3,$4,'concept',$5,$6,'introduced','user_message',$7,$8,$9,$9,1,$9,$9,1,null)
                           returning *""",
                        uuid4(), teaching.key, teaching.canonical_name, [], teaching.summary, teaching.score,
                        user_message_id, source_episode_id, timestamp,
                    )
                    created_count += 1
                else:
                    count = int(existing["reinforcement_count"]) + 1
                    confidence = min(0.95, float(existing["confidence"]) + 0.18)
                    row = await connection.fetchrow(
                        """update diana_knowledge set canonical_name=$1, summary=$2, confidence=$3,
                              status=$4, source_type='user_message', source_id=$5,
                              source_episode_id=coalesce($6, source_episode_id), reinforcement_count=$7,
                              last_reinforced_at=$8, updated_at=$8
                           where knowledge_id=$9 returning *""",
                        teaching.canonical_name, teaching.summary, confidence, _status(count, confidence),
                        user_message_id, source_episode_id, count, timestamp, existing["knowledge_id"],
                    )
                    reinforced_count += 1
                knowledge_by_key[teaching.key] = row
                stored.append(dict(row))
            for (story_key, canonical_name), facts in grouped_story_facts.items():
                explicit_subject = _story_subject_from_text(user_text)
                focus = _story_focus.get(conversation_id) if conversation_id is not None else None
                if focus is not None and (explicit_subject is None or focus.key == story_key):
                    learning_session_id = focus.session_id
                else:
                    learning_session_id = source_episode_id or user_message_id
                knowledge = knowledge_by_key.get(story_key)
                is_new_knowledge = knowledge is None
                if knowledge is None:
                    knowledge = await connection.fetchrow(
                        """insert into diana_knowledge (
                              knowledge_id, subject_key, canonical_name, aliases, knowledge_type, summary, confidence, status,
                              source_type, source_id, source_episode_id, first_learned_at, last_reinforced_at,
                              reinforcement_count, created_at, updated_at, learning_session_count, last_learning_session_id
                           ) values ($1,$2,$3,$4,'story',$5,0.45,'introduced','user_story',$6,$7,$8,$8,1,$8,$8,1,$9)
                           returning *""",
                        uuid4(), story_key, canonical_name, [], facts[0].fact_text, user_message_id,
                        source_episode_id, timestamp, learning_session_id,
                    )
                    knowledge_by_key[story_key] = knowledge
                    created_count += 1
                session_is_new = is_new_knowledge or knowledge["last_learning_session_id"] != learning_session_id
                # The old per-fact lookup followed by an all-facts contradiction
                # lookup reread the same durable collection for every candidate.
                # One snapshot is sufficient inside this transaction; mirror each
                # insert in the local map so later facts see the same collection.
                prior_fact_rows = await connection.fetch(
                    "select * from diana_knowledge_facts where knowledge_id=$1", knowledge["knowledge_id"],
                )
                facts_by_key = {fact["fact_key"]: fact for fact in prior_fact_rows}
                duplicate_fact_seen = False
                for story_fact in facts:
                    fact_key = _fact_key(story_fact.fact_text)
                    existing_fact = facts_by_key.get(fact_key)
                    if existing_fact is not None:
                        duplicate_fact_seen = True
                        await connection.execute(
                            """update diana_knowledge_facts set confidence=min(0.95, confidence + 0.18),
                                  reinforcement_count=reinforcement_count + 1, last_reinforced_at=$2, updated_at=$2
                               where knowledge_fact_id=$1""",
                            existing_fact["knowledge_fact_id"], timestamp,
                        )
                        continue
                    incoming_negative = bool(_NEGATION.search(story_fact.fact_text))
                    contradicts = next((fact for fact in facts_by_key.values() if bool(_NEGATION.search(fact["fact_text"])) != incoming_negative and _contradicts(fact["fact_text"], story_fact.fact_text)), None)
                    if contradicts is not None:
                        await connection.execute(
                            """update diana_knowledge_facts set contradiction_count=contradiction_count + 1,
                                  last_contradicted_at=$2, updated_at=$2 where knowledge_fact_id=$1""",
                            contradicts["knowledge_fact_id"], timestamp,
                        )
                        continue
                    await connection.execute(
                        """insert into diana_knowledge_facts (
                              knowledge_fact_id, knowledge_id, fact_key, fact_text, knowledge_scope, source_type,
                              source_message_id, source_episode_id, confidence, reinforcement_count, contradiction_count,
                              first_learned_at, last_reinforced_at, created_at, updated_at
                           ) values ($1,$2,$3,$4,'fictional_story','user_story',$5,$6,0.45,1,0,$7,$7,$7,$7)""",
                        uuid4(), knowledge["knowledge_id"], fact_key, story_fact.fact_text, user_message_id, source_episode_id, timestamp,
                    )
                    facts_by_key[fact_key] = {"fact_key": fact_key, "fact_text": story_fact.fact_text}
                fact_rows = await connection.fetch(
                    "select fact_text from diana_knowledge_facts where knowledge_id=$1 order by first_learned_at asc limit 8",
                    knowledge["knowledge_id"],
                )
                reinforcement_count = int(knowledge["reinforcement_count"]) + (1 if duplicate_fact_seen and session_is_new and not is_new_knowledge else 0)
                confidence = min(0.95, float(knowledge["confidence"]) + 0.12) if duplicate_fact_seen and session_is_new and not is_new_knowledge else float(knowledge["confidence"])
                learning_sessions = int(knowledge["learning_session_count"]) + (1 if session_is_new and not is_new_knowledge else 0)
                row = await connection.fetchrow(
                    """update diana_knowledge set canonical_name=$1, knowledge_type='story', summary=$2, confidence=$3, status=$4, source_type='user_story',
                          source_id=$5, source_episode_id=coalesce($6, source_episode_id), reinforcement_count=$7,
                          learning_session_count=$8, last_learning_session_id=$9,
                          last_reinforced_at=case when $10 then $11 else last_reinforced_at end,
                          updated_at=$11 where knowledge_id=$12 returning *""",
                    canonical_name, "; ".join(item["fact_text"] for item in fact_rows)[:900], confidence,
                    _status(reinforcement_count, confidence), user_message_id, source_episode_id, reinforcement_count,
                    learning_sessions, learning_session_id, duplicate_fact_seen and session_is_new and not is_new_knowledge,
                    timestamp, knowledge["knowledge_id"],
                )
                stored.append(dict(row))
                if conversation_id is not None:
                    update_story_focus(conversation_id, user_text, session_id=learning_session_id)
    record_knowledge_acquisition(
        status="created" if created_count else "reinforced" if reinforced_count else "duplicate",
        candidate_count=len(teachings) + len(story_facts), created_count=created_count, reinforced_count=reinforced_count,
    )
    return stored


async def detach_episode_provenance(connection: Any, *, episode_id: UUID) -> None:
    """Retain acquired knowledge/facts while removing deleted Episode provenance."""
    await connection.execute(
        "update diana_knowledge set source_episode_id=null where source_episode_id=$1",
        episode_id,
    )
    await connection.execute(
        "update diana_knowledge_facts set source_episode_id=null where source_episode_id=$1",
        episode_id,
    )


def _contradicts(existing: str, incoming: str) -> bool:
    """Detect only obvious explicit negations; ambiguous story variants stay separate."""
    def words(text: str) -> set[str]:
        return {
            _PARTICLE.sub("", word)
            for word in re.findall(r"[0-9a-z가-힣]{2,}", text.casefold())
            if _PARTICLE.sub("", word) not in {"", "이야기", "동화", "안"}
        }
    existing_words = words(existing)
    incoming_words = words(incoming)
    return len(existing_words & incoming_words) >= 1
