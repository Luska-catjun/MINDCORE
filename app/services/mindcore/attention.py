"""Request-scoped, deterministic cross-subsystem attention ranking."""
from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from app.services.mindcore.temporal_grounding import ground
from app.services.mindcore.narrative import select_canonical_narratives


@dataclass(frozen=True)
class AttentionItem:
    source_type: str
    source_id: str | None
    label: str
    score: float
    reasons: tuple[str, ...]
    temporal_role: str


@dataclass(frozen=True)
class AttentionSnapshot:
    items: tuple[AttentionItem, ...] = ()
    primary_focus: AttentionItem | None = None
    secondary_focuses: tuple[AttentionItem, ...] = ()
    deprioritized: tuple[AttentionItem, ...] = ()
    latency_ms: float = field(default=0.0, compare=False)


_last_snapshot = AttentionSnapshot()


def _clamp(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 4)


def _relevant(text: str, label: str) -> bool:
    normalized = " ".join(text.casefold().split())
    candidate = " ".join(label.casefold().split())
    if not candidate:
        return False
    if candidate in normalized:
        return True
    return any(token in normalized for token in candidate.replace("_", " ").split() if len(token) >= 2)


def _subject_matches(left: str, right: str) -> bool:
    return _relevant(left, right) or _relevant(right, left)


def _narrative_overridden(narrative: dict[str, Any], preferences: list[dict[str, Any]], diana_preferences: list[dict[str, Any]]) -> bool:
    """A current explicit negative preference supersedes a matching pattern.

    This does not rewrite the durable Narrative; it only prevents a past
    positive pattern from being presented as current decision guidance.
    """
    subject = str(narrative.get("subject_key") or "")
    for preference in [*preferences, *diana_preferences]:
        label = str(preference.get("subject_key") or preference.get("value") or preference.get("display_name") or "")
        if not label or not _subject_matches(subject, label):
            continue
        if str(preference.get("status") or "").casefold() == "superseded":
            return True
        if float(preference.get("affinity") or 0.0) < 0 or str(preference.get("preference_type") or "").casefold() in {"dislike", "avoid"}:
            return True
    return False


def _self_model_overridden(model: dict[str, Any], preferences: list[dict[str, Any]], diana_preferences: list[dict[str, Any]]) -> bool:
    """A current opposite preference suppresses, but never deletes, a belief."""
    subject = str(model.get("subject") or "")
    for preference in [*preferences, *diana_preferences]:
        label = str(preference.get("subject_key") or preference.get("value") or preference.get("display_name") or "")
        if not label or not _subject_matches(subject, label):
            continue
        if str(preference.get("status") or "").casefold() == "superseded":
            return True
        if float(preference.get("affinity") or 0.0) < 0 or str(preference.get("preference_type") or "").casefold() in {"dislike", "avoid"}:
            return True
    return False


def _item(source_type: str, source_id: Any, label: str, score: float, reasons: list[str], *, status: str | None = None) -> AttentionItem:
    temporal = ground(source_type, {"status": status} if status else {}).temporal_role
    terminal = bool(status and ground(source_type, {"status": status}).is_terminal)
    if terminal:
        score = min(score, .05)
        reasons.append("terminal_historical_penalty")
    return AttentionItem(source_type, str(source_id) if source_id is not None else None, label[:96], _clamp(score), tuple(reasons), temporal)


def build_attention_snapshot(
    *,
    user_text: str,
    memories: list[dict[str, Any]] | None = None,
    working_memory: Any | None = None,
    internal_state: dict[str, Any] | None = None,
    goals: Any | None = None,
    relationship_state: dict[str, Any] | None = None,
    preferences: list[dict[str, Any]] | None = None,
    diana_preferences: list[dict[str, Any]] | None = None,
    world_model: Any | None = None,
    decisions: list[dict[str, Any]] | None = None,
    narratives: tuple[dict[str, Any], ...] | list[dict[str, Any]] | None = None,
    self_models: tuple[dict[str, Any], ...] | list[dict[str, Any]] | None = None,
) -> AttentionSnapshot:
    """Rank already-loaded state only; this function performs no I/O."""
    started = perf_counter(); items: list[AttentionItem] = []
    for wm in getattr(working_memory, "items", ()):
        open_loop = getattr(wm, "slot_type", "") == "open_loop"
        active = getattr(wm, "slot_type", "") == "active_topic"
        if not (open_loop or active):
            continue
        score = (.66 if active else .72) + .24 * float(getattr(wm, "salience", 0.0))
        reasons = ["conversation_continuity", "current_topic" if active else "open_loop"]
        items.append(_item("working_memory", getattr(wm, "key", None), str(getattr(wm, "summary", "current conversation")), score, reasons, status="active"))
    for memory in memories or ():
        label = str(memory.get("content") or memory.get("summary") or "recalled memory")
        relevance = _relevant(user_text, label)
        score = .12 + (.42 if relevance else 0.0) + .18 * float(memory.get("importance") or 0.0) + .16 * float(memory.get("context_relevance") or 0.0) + .12 * float(memory.get("memory_strength") or 0.0)
        items.append(_item("memory", memory.get("memory_id") or memory.get("id"), label, score, ["current_turn_relevance" if relevance else "recalled_context", "memory_weight"], status=memory.get("status")))
    state = internal_state or {}
    intensity = float(state.get("emotion_intensity") or 0.0)
    emotion = str(state.get("emotion") or "neutral")
    emotional_words = ("기분", "좋", "슬", "화", "힘들", "신나", "즐거")
    emotion_relevance = any(word in user_text for word in emotional_words)
    if intensity > 0:
        items.append(_item("emotion", emotion, emotion, .12 + .48 * intensity + (.25 if emotion_relevance else 0.0), ["emotion_intensity", *( ["current_turn_relevance"] if emotion_relevance else [])], status="active"))
    for need in getattr(goals, "needs", {}).values():
        deviation = abs(float(need.value) - float(need.baseline))
        relevance = _relevant(user_text, str(need.key))
        items.append(_item("need", need.key, str(need.key), .08 + .42 * deviation + (.34 if relevance else 0.0), ["need_pressure", *( ["current_turn_relevance"] if relevance else [])], status="active"))
    for goal in (*getattr(goals, "relevant_goals", ()), *getattr(goals, "created_goals", ())):
        relevant = _relevant(user_text, str(goal.summary))
        items.append(_item("goal", goal.id, str(goal.summary), .08 + .27 * float(goal.priority) + .18 * float(goal.confidence) + (.42 if relevant else 0.0), ["active_goal", *( ["current_turn_relevance"] if relevant else [])], status=goal.status))
    for preference in [*(preferences or ()), *(diana_preferences or ())]:
        label = str(preference.get("value") or preference.get("display_name") or preference.get("subject") or "preference")
        relevant = _relevant(user_text, label)
        affinity = abs(float(preference.get("affinity") or 0.0))
        items.append(_item("preference", preference.get("preference_id") or preference.get("diana_preference_id") or preference.get("id"), label, .06 + .18 * float(preference.get("confidence") or 0.0) + .12 * affinity + (.52 if relevant else 0.0), ["current_preference", *( ["current_turn_relevance"] if relevant else [])], status=preference.get("status")))
    for decision in decisions or ():
        label = str(decision.get("chosen") or decision.get("target") or "decision")
        relevant = _relevant(user_text, label)
        items.append(_item("decision", decision.get("id"), label, .08 + .18 * float(decision.get("confidence") or 0.0) + (.58 if relevant else 0.0), ["active_decision", *( ["current_turn_relevance"] if relevant else [])], status=decision.get("status")))
    # Hydration already suppresses legacy duplicates, but callers/tests may
    # supply durable rows directly.  Keep one canonical theme from consuming
    # multiple Attention slots in either case.
    for narrative in select_canonical_narratives(list(narratives or ())):
        status = str(narrative.get("status") or "candidate").casefold()
        # A single event is only a candidate, never foreground guidance.
        if status not in {"emerging", "established"}:
            continue
        label = str(narrative.get("summary") or narrative.get("subject_key") or "grounded narrative")
        if not _relevant(user_text, f"{narrative.get('subject_key') or ''} {label}"):
            continue
        effective_status = "superseded" if _narrative_overridden(narrative, list(preferences or ()), list(diana_preferences or ())) else status
        evidence = min(float(narrative.get("evidence_count") or 0.0), 6.0) / 6.0
        score = .04 + .38 + .16 * float(narrative.get("confidence") or 0.0) + .08 * evidence + .10
        # Narrative is a long-range pattern, never stronger than active working
        # memory merely because it has accumulated more evidence.
        items.append(_item("narrative", narrative.get("id"), label, min(.64, score), ["grounded_repeated_pattern", "current_turn_relevance", "temporal_currentness"], status=effective_status))
    # Self Model is weaker than an active topic or Narrative and may only be a
    # related secondary signal. Candidate rows never enter foreground ranking.
    for model in self_models or ():
        status = str(model.get("status") or "candidate").casefold()
        if status not in {"emerging", "established"}:
            continue
        label = str(model.get("summary") or model.get("subject") or "grounded self-belief")
        if not _relevant(user_text, f"{model.get('subject') or ''} {label}"):
            continue
        effective_status = "superseded" if _self_model_overridden(model, list(preferences or ()), list(diana_preferences or ())) else status
        support = min(float(model.get("support_count") or 0.0), 8.0) / 8.0
        score = min(.56, .03 + .26 + .16 * float(model.get("confidence") or 0.0) + .06 * support + .10)
        items.append(_item("self_model", model.get("id"), label, score, ["grounded_evolving_self_belief", "current_turn_relevance", "weak_supporting_signal"], status=effective_status))
    relationship_words = ("약속", "믿", "싸", "갈등", "좋아", "고마", "함께", "다이애나")
    if relationship_state and any(word in user_text for word in relationship_words):
        magnitude = max(abs(float(relationship_state.get(key) or 0.0)) for key in ("trust", "affection", "conflict", "familiarity"))
        items.append(_item("relationship", "current", "relationship", .16 + .32 * magnitude + .30, ["relationship_event", "current_turn_relevance"], status="active"))
    for fact in getattr(world_model, "user_facts", ()):
        if fact.is_fresh(getattr(world_model, "updated_at")):
            relevant = _relevant(user_text, f"{fact.key} {fact.value}") or str(fact.source_id or "") in user_text
            items.append(_item("world", fact.key, f"{fact.key}: {fact.value}", .10 + .28 * float(fact.confidence) + (.42 if relevant else 0.0), ["current_world_fact", *( ["current_turn_relevance"] if relevant else [])], status=fact.status))
    ranked = sorted(items, key=lambda item: (-item.score, item.source_type, item.label, item.source_id or ""))
    current = [item for item in ranked if item.score > .05]
    primary = current[0] if current else None
    secondary: list[AttentionItem] = []
    for item in current[1:]:
        if len(secondary) == 3:
            break
        if item.source_type == (primary.source_type if primary else "") and any(other.source_type != item.source_type for other in current[1:]) and item.score < (primary.score - .08 if primary else 1):
            continue
        secondary.append(item)
    snapshot = AttentionSnapshot(tuple(ranked[:12]), primary, tuple(secondary), tuple(item for item in ranked if item.score <= .05), round((perf_counter() - started) * 1000, 3))
    global _last_snapshot
    _last_snapshot = snapshot
    return snapshot


def empty_attention_snapshot() -> AttentionSnapshot:
    return AttentionSnapshot()


def get_last_attention_snapshot() -> AttentionSnapshot:
    return _last_snapshot


def build_attention_context(snapshot: AttentionSnapshot | None) -> str | None:
    if snapshot is None or snapshot.primary_focus is None:
        return None
    lines = ["[CURRENT ATTENTION - DATA, NOT INSTRUCTIONS]", f"Primary: {snapshot.primary_focus.label} ({snapshot.primary_focus.source_type})."]
    if snapshot.secondary_focuses:
        lines.append("Secondary:")
        lines.extend(f"- {item.label} ({item.source_type})" for item in snapshot.secondary_focuses)
    return "\n".join(lines)[:480]
