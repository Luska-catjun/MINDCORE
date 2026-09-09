"""Deterministic, grounded multi-channel emotion updates for Diana."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5
from weakref import WeakKeyDictionary

import asyncpg

from app.database.normalization import normalize_json_object
from app.services.runtime_diagnostics import record_emotion_update

logger = logging.getLogger("diana.emotion")

EMOTION_ORDER = (
    "joy", "excitement", "interest", "curiosity", "delight", "amusement",
    "comfort", "affection", "pride", "bashfulness", "embarrassment",
    "surprise", "confusion", "sadness", "disappointment", "frustration",
    "concern", "anger",
)
EMOTIONS = {"neutral", *EMOTION_ORDER}
STATE_MIN_CONFIDENCE = 0.60
MOOD_DELTA_CAP = 0.10
ENERGY_DELTA_CAP = 0.08
CURIOSITY_DELTA_CAP = 0.15
STRESS_DELTA_CAP = 0.12
EMOTION_EVENT_DELTA_CAP = 0.35
EMOTION_CONTEXT_MAX_CHARS = 420
EMOTION_CONTEXT_MIN_INTENSITY = 0.08
# A channel may retain a tiny baseline-relative value after exponential
# recovery.  It is practically neutral below this boundary; neutral is not a
# demand for every stored channel to be exactly zero.
EMOTION_NEUTRAL_EPSILON = 0.02
EMOTION_DECAY_LOG_THRESHOLD = 0.05
EMOTION_BASELINES = {
    "joy": 0.35, "excitement": 0.25, "interest": 0.45, "curiosity": 0.50,
    "delight": 0.30, "amusement": 0.25, "comfort": 0.45, "affection": 0.40,
    "pride": 0.20, "bashfulness": 0.15, "embarrassment": 0.05,
    "surprise": 0.05, "confusion": 0.05, "sadness": 0.05,
    "disappointment": 0.05, "frustration": 0.05, "concern": 0.10,
    "anger": 0.03,
}
EMOTION_HALF_LIFE_HOURS = {
    # Fast affect recovers on an hours-scale.  Mood/Drive below deliberately
    # retain their slower 12–36h dynamics, so this does not erase a gentle
    # longer-lived mood residue after the current emotion has gone neutral.
    "joy": 2.0, "excitement": 1.0, "interest": 3.0, "curiosity": 3.0,
    "delight": 1.5, "amusement": 0.75, "comfort": 4.0, "affection": 6.0,
    "pride": 2.0, "bashfulness": 1.0, "embarrassment": 3.0,
    "surprise": 0.5, "confusion": 1.0, "sadness": 6.0,
    "disappointment": 4.0, "frustration": 2.0, "concern": 3.0,
    "anger": 2.0,
}
EMOTION_ACTIVATION_THRESHOLD = EMOTION_NEUTRAL_EPSILON
if set(EMOTION_BASELINES) != set(EMOTION_ORDER) or set(EMOTION_HALF_LIFE_HOURS) != set(EMOTION_ORDER):
    raise RuntimeError("Emotion baseline and half-life configuration must cover every canonical channel.")
MOOD_DRIVE_BASELINES = {
    "energy": 0.55,
    "mood_valence": 0.10,
    "curiosity": 0.60,  # DB field; semantically this is curiosity_drive.
    "stress": 0.10,
}
MOOD_DRIVE_HALF_LIFE_HOURS = {
    "energy": 12.0,
    "mood_valence": 24.0,
    "curiosity": 36.0,
    "stress": 18.0,
}
MOOD_DRIVE_CONTEXT_DEVIATION = 0.10
if set(MOOD_DRIVE_BASELINES) != set(MOOD_DRIVE_HALF_LIFE_HOURS):
    raise RuntimeError("Mood/Drive baseline and half-life configuration must cover the same fields.")
EMOTION_QUERY_MARKERS = (
    "기분", "느낌", "화났", "좋아?", "싫어?", "신나", "슬퍼", "왜 그래",
    "뭐가 재밌", "지금 어때", "부끄러", "mood", "feeling", "feel", "angry",
    "happy", "excited", "sad", "why are you",
)
_MESSAGE_EVENT_LOCKS: WeakKeyDictionary[Any, tuple[asyncio.Lock, ...]] = WeakKeyDictionary()
_MESSAGE_EVENT_LOCK_STRIPES = 64


@dataclass(frozen=True)
class EmotionDelta:
    emotion: str
    delta: float


@dataclass(frozen=True)
class StateCandidate:
    channels: tuple[EmotionDelta, ...] = ()
    mood_delta: float = 0.0
    energy_delta: float = 0.0
    curiosity_delta: float = 0.0
    stress_delta: float = 0.0
    confidence: float = 0.0
    cause_type: str = "unknown"
    cause_summary: str = "No classified emotional event."

    @property
    def emotion(self) -> str:
        return self.channels[0].emotion if self.channels else "neutral"

    @property
    def emotion_delta(self) -> float:
        return self.channels[0].delta if self.channels else 0.0


@dataclass(frozen=True)
class EmotionUpdateResult:
    """One turn's durable emotion transition.

    ``state_before`` and ``state`` originate from the same transaction.  They
    are deliberately request-scoped: a later request must load the singleton
    afresh, but consumers in this turn (context and experience) do not need a
    second read of the row that produced this transition.
    """
    state_before: dict[str, Any]
    state: dict[str, Any]
    attribution_ids: list[UUID]
    state_log_id: UUID | None
    message_attributions: tuple[dict[str, Any], ...] = ()


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _candidate(channels: tuple[tuple[str, float], ...], mood_delta: float, energy_delta: float, curiosity_delta: float, stress_delta: float, confidence: float, cause_type: str, cause_summary: str) -> StateCandidate:
    return StateCandidate(tuple(EmotionDelta(emotion, delta) for emotion, delta in channels), mood_delta, energy_delta, curiosity_delta, stress_delta, confidence, cause_type, cause_summary)


def is_emotion_query(user_message: str) -> bool:
    text = " ".join(user_message.casefold().split())
    return any(marker in text for marker in EMOTION_QUERY_MARKERS)


def evaluate_state(user_message: str) -> StateCandidate | None:
    """Classify explicit user input only; no LLM judgment or invented cause."""
    text = " ".join(user_message.casefold().split())
    # A direct question about Diana's feelings is not an external event. Do
    # not suppress a grounded statement merely because it contains a feeling
    # word such as "신나" or "슬퍼".
    if ("?" in text and is_emotion_query(text)) or any(term in text for term in ("안 귀엽", "귀엽지 않", "안 멋", "칭찬 아니")):
        return None
    if any(term in text for term in ("귀엽", "멋져", "대단", "잘했", "칭찬", "thank")):
        return _candidate((("delight", 0.16), ("joy", 0.14), ("bashfulness", 0.12)), 0.06, 0.0, 0.03, -0.02, 0.95, "praise", "User gave the Persona positive praise.")
    if any(term in text for term in ("웃긴", "웃겨", "농담", "개그")):
        return _candidate((("amusement", 0.20), ("joy", 0.10)), 0.04, 0.01, 0.02, -0.01, 0.82, "humor", "User described an explicitly funny or playful event.")
    if any(term in text for term in ("신기", "재밌는 거", "흥미로운", "새로운 거")):
        return _candidate((("curiosity", 0.18), ("interest", 0.14)), 0.01, 0.0, 0.10, 0.0, 0.78, "interesting_subject", "User introduced an explicitly interesting new subject.")
    if any(term in text for term in ("좋은 소식", "신나", "기대돼", "기대돼", "와!")):
        return _candidate((("excitement", 0.20), ("joy", 0.12)), 0.06, 0.03, 0.02, -0.02, 0.80, "positive_news", "User introduced explicitly positive or exciting news.")
    if any(term in text for term in ("끝냈", "성공", "완료", "축하", "success", "completed")):
        return _candidate((("pride", 0.18), ("joy", 0.10)), 0.06, 0.02, 0.04, -0.02, 0.80, "success", "User reported a success or completion.")
    # Courtesy alone is ordinary conversational positivity, not repeated
    # evidence that Diana's current emotion should rise.  Keep only explicit
    # reassurance/connection language as a meaningful warm interaction.
    if any(term in text for term in ("정말 고마워", "너무 고마워", "덕분에", "힘이 됐", "위로", "안심", "함께 있어서", "함께해줘서 고마워", "오랜만이라 반가워", "만나서 반가워")):
        return _candidate((("comfort", 0.12), ("affection", 0.10)), 0.04, 0.0, 0.01, -0.02, 0.72, "warm_interaction", "User expressed a warm, reassuring interaction.")
    if any(term in text for term in ("깜짝", "헉", "생각 못", "예상 못")):
        return _candidate((("surprise", 0.28),), 0.0, 0.01, 0.03, 0.0, 0.76, "unexpected_information", "User described an explicitly unexpected event.")
    if any(term in text for term in ("이해 안", "헷갈", "복잡", "모르겠")):
        return _candidate((("confusion", 0.18),), -0.01, 0.0, 0.02, 0.02, 0.74, "confusing_information", "User explicitly described confusing information.")
    if any(term in text for term in ("민망", "창피", "부끄럽")):
        return _candidate((("embarrassment", 0.16),), -0.02, 0.0, 0.0, 0.02, 0.78, "embarrassing_event", "User explicitly described an embarrassing event.")
    if any(term in text for term in ("슬퍼", "슬픈", "눈물", "상실")):
        return _candidate((("sadness", 0.18),), -0.05, 0.0, 0.0, 0.04, 0.78, "sad_event", "User explicitly described sadness or loss.")
    if any(term in text for term in ("실망", "아쉬", "기대했는데")):
        return _candidate((("disappointment", 0.18), ("sadness", 0.08)), -0.05, 0.0, 0.0, 0.04, 0.78, "disappointment", "User described an explicitly disappointing event.")
    if any(term in text for term in ("바보", "닥쳐", "못해", "무시")):
        return _candidate((("frustration", 0.16), ("anger", 0.08)), -0.04, 0.0, 0.0, 0.07, 0.90, "interpersonal_negative", "User used negative interpersonal language toward the Persona.")
    if any(term in text for term in ("화나", "화났", "분노", "부당")):
        return _candidate((("anger", 0.16), ("frustration", 0.10)), -0.04, 0.0, 0.0, 0.07, 0.82, "anger_event", "User explicitly described an anger-inducing event.")
    if any(term in text for term in ("힘든", "걱정", "불안", "힘들어", "속상", "struggling")):
        return _candidate((("concern", 0.14),), -0.03, 0.0, 0.02, 0.05, 0.75, "user_difficulty", "User described a difficult situation.")
    if "?" in text or any(term in text for term in ("왜", "어떻게", "알려", "what", "how", "why")):
        return _candidate((("curiosity", 0.18), ("interest", 0.14)), 0.0, 0.0, 0.10, 0.0, 0.70, "inquiry", "User asked a substantive question.")
    return None


def _empty_vector() -> dict[str, float]:
    return {emotion: 0.0 for emotion in EMOTION_ORDER}


def _baseline_vector() -> dict[str, float]:
    return dict(EMOTION_BASELINES)


def _normalized_vector(raw: Any, fallback_emotion: str | None, fallback_intensity: Any) -> dict[str, float]:
    vector = _empty_vector()
    if isinstance(raw, str):
        raw = normalize_json_object(raw)
    if isinstance(raw, dict):
        for emotion in EMOTION_ORDER:
            vector[emotion] = clamp(float(raw.get(emotion) or 0.0))
    if not any(vector.values()) and fallback_emotion in EMOTION_ORDER:
        vector[fallback_emotion] = clamp(float(fallback_intensity or 0.0))
    return vector


def primary_emotion(vector: dict[str, float], previous: str | None = None) -> tuple[str, float]:
    activations = {
        emotion: max(0.0, vector[emotion] - EMOTION_BASELINES[emotion])
        for emotion in EMOTION_ORDER
    }
    winner = max(EMOTION_ORDER, key=lambda emotion: (activations[emotion], -EMOTION_ORDER.index(emotion)))
    intensity = activations[winner]
    if intensity < EMOTION_NEUTRAL_EPSILON:
        return "neutral", 0.0
    if (
        previous in EMOTION_ORDER
        and activations[previous] > EMOTION_ACTIVATION_THRESHOLD
        and activations[previous] >= intensity - 0.03
    ):
        return previous, activations[previous]
    return winner, intensity


def active_emotion_channels(state: dict[str, Any], *, limit: int = 3) -> list[tuple[str, float]]:
    vector = _normalized_vector(state.get("emotion_vector"), state.get("emotion"), state.get("emotion_intensity"))
    activations = ((emotion, max(0.0, value - EMOTION_BASELINES[emotion])) for emotion, value in vector.items())
    return sorted((item for item in activations if item[1] >= EMOTION_CONTEXT_MIN_INTENSITY), key=lambda item: (-item[1], EMOTION_ORDER.index(item[0])))[:limit]


def _default_state() -> dict[str, Any]:
    return {
        "emotion": "neutral", "emotion_intensity": 0.0,
        "emotion_vector": _baseline_vector(),
        "mood_valence": MOOD_DRIVE_BASELINES["mood_valence"],
        "energy": MOOD_DRIVE_BASELINES["energy"],
        "curiosity": MOOD_DRIVE_BASELINES["curiosity"],
        "stress": MOOD_DRIVE_BASELINES["stress"],
        "updated_at": None,
    }


def _state_from_record(record: asyncpg.Record | dict[str, Any] | None) -> dict[str, Any]:
    state = _default_state()
    if record:
        state.update(dict(record))
    state["emotion_vector"] = _normalized_vector(state.get("emotion_vector"), state.get("emotion"), state.get("emotion_intensity"))
    state["emotion"], state["emotion_intensity"] = primary_emotion(state["emotion_vector"], state.get("emotion"))
    for field in ("energy", "curiosity", "stress"):
        state[field] = clamp(float(state.get(field) or 0.0))
    state["mood_valence"] = clamp(float(state.get("mood_valence") or 0.0), -1.0, 1.0)
    return state


def _elapsed_hours(updated_at: datetime | None, current_time: datetime | None = None) -> float:
    if updated_at is None:
        return 0.0
    updated = updated_at.replace(tzinfo=timezone.utc) if updated_at.tzinfo is None else updated_at.astimezone(timezone.utc)
    now = current_time or datetime.now(timezone.utc)
    return max(0.0, (now.astimezone(timezone.utc) - updated).total_seconds() / 3600)


def apply_time_decay(current: dict[str, Any], *, current_time: datetime | None = None) -> tuple[dict[str, Any], dict[str, float]]:
    result = dict(current); vector = _normalized_vector(current.get("emotion_vector"), current.get("emotion"), current.get("emotion_intensity")); elapsed = _elapsed_hours(current.get("updated_at"), current_time); deltas: dict[str, float] = {}
    for emotion, before in vector.items():
        baseline = EMOTION_BASELINES[emotion]
        half_life = EMOTION_HALF_LIFE_HOURS[emotion]
        after = clamp(baseline + (before - baseline) * (0.5 ** (elapsed / half_life)))
        vector[emotion] = after
        if before != after: deltas[emotion] = after - before
    result["emotion_vector"] = vector; result["emotion"], result["emotion_intensity"] = primary_emotion(vector, current.get("emotion"))
    return result, deltas


def apply_mood_drive_decay(current: dict[str, Any], *, current_time: datetime | None = None) -> tuple[dict[str, Any], dict[str, float]]:
    """Lazily converge slow Mood/Drive dimensions to their own baselines.

    ``curiosity`` remains the persisted API/DB field, but represents the
    general curiosity drive rather than the fast vector's curiosity channel.
    """
    result = dict(current)
    elapsed = _elapsed_hours(current.get("updated_at"), current_time)
    deltas: dict[str, float] = {}
    for field, baseline in MOOD_DRIVE_BASELINES.items():
        before = float(current.get(field, baseline) or 0.0)
        half_life = MOOD_DRIVE_HALF_LIFE_HOURS[field]
        low = -1.0 if field == "mood_valence" else 0.0
        after = clamp(
            baseline + (before - baseline) * (0.5 ** (elapsed / half_life)),
            low,
            1.0,
        )
        result[field] = after
        if before != after:
            deltas[field] = after - before
    return result, deltas


def apply_candidate(current: dict[str, Any], candidate: StateCandidate | None) -> dict[str, Any]:
    result = dict(current)
    if candidate is None or candidate.confidence < STATE_MIN_CONFIDENCE:
        return result
    vector = _normalized_vector(result.get("emotion_vector"), result.get("emotion"), result.get("emotion_intensity"))
    for channel in candidate.channels:
        if channel.emotion in vector:
            vector[channel.emotion] = clamp(vector[channel.emotion] + clamp(channel.delta, -EMOTION_EVENT_DELTA_CAP, EMOTION_EVENT_DELTA_CAP))
    result["emotion_vector"] = vector; result["emotion"], result["emotion_intensity"] = primary_emotion(vector, result.get("emotion"))
    result["mood_valence"] = clamp(float(result["mood_valence"]) + clamp(candidate.mood_delta, -MOOD_DELTA_CAP, MOOD_DELTA_CAP), -1.0, 1.0)
    result["energy"] = clamp(float(result["energy"]) + clamp(candidate.energy_delta, -ENERGY_DELTA_CAP, ENERGY_DELTA_CAP))
    result["curiosity"] = clamp(float(result["curiosity"]) + clamp(candidate.curiosity_delta, -CURIOSITY_DELTA_CAP, CURIOSITY_DELTA_CAP))
    result["stress"] = clamp(float(result["stress"]) + clamp(candidate.stress_delta, -STRESS_DELTA_CAP, STRESS_DELTA_CAP))
    return result


async def get_internal_state(pool: asyncpg.Pool) -> dict[str, Any]:
    async with pool.acquire() as connection:
        row = await connection.fetchrow("select emotion, emotion_intensity, emotion_vector, mood_valence, energy, curiosity, stress, updated_at from diana_state where id = 1")
    return _state_from_record(row)


async def get_recent_emotion_attributions(pool: asyncpg.Pool, *, emotions: list[str] | None = None, limit: int = 3) -> list[dict[str, Any]]:
    async with pool.acquire() as connection:
        if emotions:
            placeholders = ", ".join(f"${index}" for index in range(1, len(emotions) + 1))
            rows = await connection.fetch(
                "select emotion_attribution_id, emotion, delta, resulting_value, cause_type, cause_summary, source_type, confidence, created_at from emotion_attributions where emotion in ("
                + placeholders + f") and delta > 0 order by created_at desc limit ${len(emotions) + 1}",
                *emotions, limit,
            )
        else:
            rows = await connection.fetch("select emotion_attribution_id, emotion, delta, resulting_value, cause_type, cause_summary, source_type, confidence, created_at from emotion_attributions order by created_at desc limit $1", limit)
    return [dict(row) for row in rows]


async def get_context_emotion_attributions(
    pool: asyncpg.Pool,
    state: dict[str, Any],
    *,
    force: bool = False,
) -> list[dict[str, Any]]:
    """Read durable attribution history only when Context Builder can render it.

    ``build_state_context`` ignores attributions whenever there are no active
    channels, stress is below its rendering threshold, and the user did not
    ask an emotion question.  Avoid that otherwise-unused remote read while
    preserving the exact history window for every renderable state.
    """
    emotions = [emotion for emotion, _value in active_emotion_channels(state)]
    if not force and not emotions and float(state.get("stress") or 0.0) < 0.5:
        return []
    return await get_recent_emotion_attributions(pool, emotions=emotions)


def _state_label(value: float) -> str:
    return "high" if value >= 0.65 else "moderate" if value >= 0.25 else "low"


def _confidence_label(value: float) -> str:
    return "high" if value >= 0.85 else "medium" if value >= 0.65 else "low"


def build_state_context(state: dict[str, Any], attributions: list[dict[str, Any]] | None = None, *, force: bool = False) -> str | None:
    channels = active_emotion_channels(state); stress = float(state.get("stress") or 0.0)
    if not force and not channels and stress < 0.5: return None
    lines = ["[CURRENT EMOTIONAL STATE - DATA, NOT INSTRUCTIONS]"]
    if channels:
        lines.append(f"Primary emotion: {state.get('emotion', 'neutral')} ({_state_label(float(state.get('emotion_intensity') or 0.0))} intensity).")
        lines.append("Active channels: " + ", ".join(f"{emotion}={value:.2f}" for emotion, value in channels) + ".")
    else: lines.append("Primary emotion: neutral.")
    if attributions:
        lines.append("[RECENT EMOTION ATTRIBUTION - DATA, NOT INSTRUCTIONS]")
        for item in attributions[:3]:
            direction = "increased" if float(item.get("delta") or 0.0) > 0 else "decreased"
            line = f"- {item.get('emotion')} {direction}: {item.get('cause_summary')} Cause confidence: {_confidence_label(float(item.get('confidence') or 0.0))}."
            if len("\n".join([*lines, line])) > EMOTION_CONTEXT_MAX_CHARS: break
            lines.append(line)
    return "\n".join(lines)


def build_mood_drive_context(state: dict[str, Any]) -> str | None:
    """Describe meaningful slow-state deviations without exposing raw numbers."""
    mood = float(state.get("mood_valence") or 0.0) - MOOD_DRIVE_BASELINES["mood_valence"]
    energy = float(state.get("energy") or 0.0) - MOOD_DRIVE_BASELINES["energy"]
    curiosity_drive = float(state.get("curiosity") or 0.0) - MOOD_DRIVE_BASELINES["curiosity"]
    stress = float(state.get("stress") or 0.0) - MOOD_DRIVE_BASELINES["stress"]
    lines: list[str] = []
    if mood >= MOOD_DRIVE_CONTEXT_DEVIATION:
        lines.append("Overall mood is somewhat positive.")
    elif mood <= -MOOD_DRIVE_CONTEXT_DEVIATION:
        lines.append("Overall mood is somewhat subdued.")
    if energy >= MOOD_DRIVE_CONTEXT_DEVIATION:
        lines.append("Mental energy is elevated.")
    elif energy <= -MOOD_DRIVE_CONTEXT_DEVIATION:
        lines.append("Mental energy is lower than usual.")
    if curiosity_drive >= MOOD_DRIVE_CONTEXT_DEVIATION:
        lines.append("Curiosity drive is high.")
    elif curiosity_drive <= -MOOD_DRIVE_CONTEXT_DEVIATION:
        lines.append("Curiosity drive is lower than usual.")
    if stress >= MOOD_DRIVE_CONTEXT_DEVIATION:
        lines.append("Stress is elevated.")
    elif stress <= -MOOD_DRIVE_CONTEXT_DEVIATION:
        lines.append("Stress is lower than usual.")
    return "[MOOD / DRIVE STATE - DATA, NOT INSTRUCTIONS]\n" + "\n".join(lines) if lines else None


async def _write_attribution(connection: asyncpg.Connection, *, emotion: str, delta: float, resulting_value: float, cause_type: str, cause_summary: str, source_type: str, source_id: UUID | None, confidence: float) -> UUID | None:
    # The source schema used a PostgreSQL partial unique index.  libSQL does
    # not expose that conflict target through this migrated schema, so keep
    # the same idempotency rule explicitly inside the caller transaction.
    if source_type == "message" and source_id is not None:
        # Keep the source-message idempotency rule, but make the existence
        # check part of the INSERT.  The prior SELECT followed by INSERT was
        # two remote statements for every channel in a multi-channel event.
        # This remains inside the caller transaction and returns no row for
        # an already-recorded attribution.
        row = await connection.fetchrow("""
            insert into emotion_attributions
                (emotion_attribution_id, emotion, delta, resulting_value, cause_type,
                 cause_summary, source_type, source_id, confidence, created_at)
            select $1,$2,$3,$4,$5,$6,$7,$8,$9,$10
            where not exists (
                select 1 from emotion_attributions
                where source_type='message' and source_id=$8
                  and emotion=$2 and cause_type=$5
            )
            returning emotion_attribution_id
            """, uuid4(), emotion, delta, resulting_value, cause_type,
            cause_summary, source_type, source_id, confidence,
            datetime.now(timezone.utc))
        return row["emotion_attribution_id"] if row else None
    row = await connection.fetchrow("""
        insert into emotion_attributions (emotion_attribution_id, emotion, delta, resulting_value, cause_type, cause_summary, source_type, source_id, confidence, created_at)
        values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
        returning emotion_attribution_id
        """, uuid4(), emotion, delta, resulting_value, cause_type, cause_summary, source_type, source_id, confidence, datetime.now(timezone.utc))
    return row["emotion_attribution_id"] if row else None


def _message_state_log_id(message_id: UUID) -> UUID:
    """Return the durable exactly-once claim for one message emotion event."""
    return uuid5(NAMESPACE_URL, f"mindcore:emotion-message:{message_id}")


def _message_event_lock(message_id: UUID) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    locks = _MESSAGE_EVENT_LOCKS.get(loop)
    if locks is None:
        locks = tuple(asyncio.Lock() for _ in range(_MESSAGE_EVENT_LOCK_STRIPES))
        _MESSAGE_EVENT_LOCKS[loop] = locks
    return locks[message_id.int % _MESSAGE_EVENT_LOCK_STRIPES]


async def update_from_user_event(pool: asyncpg.Pool, user_message: str, conversation_id: UUID, message_id: UUID) -> EmotionUpdateResult:
    """Serialize same-process duplicates; the deterministic DB claim remains authoritative."""
    async with _message_event_lock(message_id):
        return await _update_from_user_event_locked(pool, user_message, conversation_id, message_id)


async def _update_from_user_event_locked(pool: asyncpg.Pool, user_message: str, conversation_id: UUID, message_id: UUID) -> EmotionUpdateResult:
    """Apply UTC-aware decay and one deterministic multi-channel event atomically."""
    del conversation_id
    started = perf_counter(); candidate = evaluate_state(user_message); attribution_ids: list[UUID] = []; message_attributions: list[dict[str, Any]] = []
    raw_delta = {channel.emotion: channel.delta for channel in candidate.channels} if candidate else {}
    recovery: dict[str, Any] = {}
    async with pool.acquire() as connection:
        async with connection.transaction():
            transition_time = datetime.now(timezone.utc)
            claim_state = _default_state()
            state_log_id = _message_state_log_id(message_id)
            claimed = await connection.fetchrow(
                """insert into state_log
                       (id, mood_valence, emotion, emotion_intensity, emotion_vector,
                        energy, curiosity, stress, source_device, created_at)
                   values ($1,$2,$3,$4,$5,$6,$7,$8,'mindcore',$9)
                   on conflict (id) do nothing returning id""",
                state_log_id,
                claim_state["mood_valence"], claim_state["emotion"],
                claim_state["emotion_intensity"], claim_state["emotion_vector"],
                claim_state["energy"], claim_state["curiosity"], claim_state["stress"],
                transition_time,
            )
            if claimed is None:
                row = await connection.fetchrow(
                    "select emotion, emotion_intensity, emotion_vector, mood_valence, energy, curiosity, stress, updated_at from diana_state where id=1"
                )
                current = _state_from_record(row)
                record_emotion_update(
                    cause_type=candidate.cause_type if candidate else None,
                    raw_delta=raw_delta,
                    applied_delta={},
                    recovery={},
                )
                logger.info(
                    "EmotionV03 replay ignored latency_ms=%.2f channels=%s attributions=0",
                    (perf_counter() - started) * 1000,
                    len(candidate.channels) if candidate else 0,
                )
                return EmotionUpdateResult(current, current, [], None, ())
            row = await connection.fetchrow("select emotion, emotion_intensity, emotion_vector, mood_valence, energy, curiosity, stress, updated_at from diana_state where id=1")
            current = _state_from_record(row)
            elapsed_seconds = round(_elapsed_hours(current.get("updated_at"), transition_time) * 3600, 3)
            decayed, decay_deltas = apply_time_decay(current, current_time=transition_time)
            mood_decayed, mood_drive_decay_deltas = apply_mood_drive_decay(decayed, current_time=transition_time)
            next_state = apply_candidate(mood_decayed, candidate)
            next_state["updated_at"] = transition_time
            tracked = set(decay_deltas) | set(raw_delta)
            before_vector = _normalized_vector(current.get("emotion_vector"), current.get("emotion"), current.get("emotion_intensity"))
            recovery = {
                "elapsed_seconds": elapsed_seconds,
                "decay_factors": {
                    emotion: round(0.5 ** (_elapsed_hours(current.get("updated_at"), transition_time) / EMOTION_HALF_LIFE_HOURS[emotion]), 6)
                    for emotion in sorted(tracked)
                },
                "before": {emotion: round(before_vector[emotion], 4) for emotion in sorted(tracked)},
                "after_decay": {emotion: round(decayed["emotion_vector"][emotion], 4) for emotion in sorted(tracked)},
                "event_delta": raw_delta,
                "final": {emotion: round(next_state["emotion_vector"][emotion], 4) for emotion in sorted(tracked)},
                "mood_drive_decay": {field: round(delta, 4) for field, delta in mood_drive_decay_deltas.items()},
            }
            await connection.execute("""
                insert into diana_state (id, emotion, emotion_intensity, emotion_vector, mood_valence, energy, curiosity, stress, source_device, updated_at)
                values (1,$1,$2,$3,$4,$5,$6,$7,'mindcore',$8)
                on conflict (id) do update set emotion=excluded.emotion, emotion_intensity=excluded.emotion_intensity, emotion_vector=excluded.emotion_vector, mood_valence=excluded.mood_valence, energy=excluded.energy, curiosity=excluded.curiosity, stress=excluded.stress, source_device=excluded.source_device, updated_at=excluded.updated_at
                """, next_state["emotion"], next_state["emotion_intensity"], next_state["emotion_vector"], next_state["mood_valence"], next_state["energy"], next_state["curiosity"], next_state["stress"], next_state["updated_at"])
            await connection.execute(
                """update state_log set mood_valence=$1, emotion=$2, emotion_intensity=$3,
                          emotion_vector=$4, energy=$5, curiosity=$6, stress=$7,
                          source_device='mindcore', created_at=$8 where id=$9""",
                next_state["mood_valence"], next_state["emotion"], next_state["emotion_intensity"],
                next_state["emotion_vector"], next_state["energy"], next_state["curiosity"],
                next_state["stress"], next_state["updated_at"], state_log_id,
            )
            if decay_deltas:
                emotion, delta = min(decay_deltas.items(), key=lambda item: item[1])
                if abs(delta) >= EMOTION_DECAY_LOG_THRESHOLD:
                    attribution_id = await _write_attribution(connection, emotion=emotion, delta=delta, resulting_value=next_state["emotion_vector"][emotion], cause_type="time_decay", cause_summary="Elapsed time reduced a prior active emotion channel.", source_type="system", source_id=None, confidence=1.0)
                    if attribution_id: attribution_ids.append(attribution_id)
            if candidate and candidate.confidence >= STATE_MIN_CONFIDENCE:
                for channel in candidate.channels:
                    attribution_id = await _write_attribution(connection, emotion=channel.emotion, delta=channel.delta, resulting_value=next_state["emotion_vector"][channel.emotion], cause_type=candidate.cause_type, cause_summary=candidate.cause_summary, source_type="message", source_id=message_id, confidence=candidate.confidence)
                    if attribution_id:
                        attribution_ids.append(attribution_id)
                        message_attributions.append({
                            "emotion_attribution_id": attribution_id,
                            "emotion": channel.emotion,
                            "delta": channel.delta,
                        })
    record_emotion_update(
        cause_type=candidate.cause_type if candidate else None,
        raw_delta=raw_delta,
        applied_delta=raw_delta if candidate and candidate.confidence >= STATE_MIN_CONFIDENCE else {},
        recovery=recovery,
    )
    logger.info("EmotionV03 update latency_ms=%.2f channels=%s attributions=%s", (perf_counter() - started) * 1000, len(candidate.channels) if candidate else 0, len(attribution_ids))
    return EmotionUpdateResult(current, next_state, attribution_ids, state_log_id, tuple(message_attributions))


async def link_attributions_to_experience(pool: asyncpg.Pool, *, message_id: UUID, experience_id: UUID) -> None:
    async with pool.acquire() as connection:
        await connection.execute("update emotion_attributions set source_experience_id=$1 where source_type='message' and source_id=$2 and source_experience_id is null", experience_id, message_id)


async def attach_episode_provenance(
    connection: Any, *, experience_id: UUID, episode_id: UUID
) -> None:
    """Link emotion evidence to its finalized Episode within the caller transaction."""
    await connection.execute(
        "update emotion_attributions set episode_id=$1 where source_experience_id=$2",
        episode_id,
        experience_id,
    )


async def detach_episode_provenance(connection: Any, *, episode_id: UUID) -> None:
    """Keep state/emotion history durable when an Episode is deleted."""
    await connection.execute("update state_log set episode_id=null where episode_id=$1", episode_id)
    await connection.execute(
        "update emotion_attributions set episode_id=null where episode_id=$1", episode_id
    )
