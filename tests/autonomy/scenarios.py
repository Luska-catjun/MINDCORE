"""Central stable M4 golden scenario catalog (test contracts only)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math

from app.models.motivation import GoalState, MotivationEvidenceRef, MotivationalSnapshot, NeedState
from app.models.temporal_context import ActivityPoint, TemporalInputs
from app.models.trigger_context import TriggerSnapshot, TriggerType
from app.services.mindcore.temporal_context import compute_temporal_context
from app.services.mindcore.trigger_context import compute_trigger_snapshot
from tests.autonomy.harness import (
    ActionClass,
    AutonomyScenario,
    ReasonCode,
    ScenarioEntry,
    ScenarioExpectation,
    build_autonomy_scenario,
)


FIXED_NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
DEFAULT_TIMEZONE = "Asia/Seoul"


def _need(now: datetime, *, key: str = "curiosity", activation: float = 0.9, persistence: float = 0.5, age: float = 0) -> NeedState:
    occurred = now - timedelta(seconds=age)
    return NeedState(key, 0.4, 0.9, activation, activation * 0.7, persistence, occurred)


def _goal(now: datetime, *, key: str = "goal-a", updated_age: float = 3 * 86400, deadline: float | None = None) -> GoalState:
    created = now - timedelta(days=10)
    return GoalState(
        goal_key=key, status="active", priority=0.9, importance=0.9, urgency=0.7,
        activation=0.8, origin_need="curiosity", created_at=created,
        updated_at=now - timedelta(seconds=updated_age),
        expires_at=now + timedelta(seconds=deadline) if deadline is not None else None,
    )


def _inputs(now: datetime, *, activity: tuple[tuple[str, float, str], ...] = (), ever_user: bool = False, ever_persona: bool = False) -> TemporalInputs:
    points = tuple(
        ActivityPoint(actor, now - timedelta(seconds=age), ref, "turn_context" if actor == "system" else "message_role")
        for actor, age, ref in activity
    )
    return TemporalInputs(points, user_has_ever_spoken=ever_user, persona_has_ever_spoken=ever_persona)


def _motivation(now: datetime, *, needs: tuple[NeedState, ...] = (), goals: tuple[GoalState, ...] = ()) -> MotivationalSnapshot:
    return MotivationalSnapshot(now, needs=needs, goals=goals)


def _entry(
    scenario_id: str,
    description: str,
    *,
    age: float | None = None,
    actor: str = "user",
    needs: tuple[NeedState, ...] = (),
    goals: tuple[GoalState, ...] = (),
    allowed: tuple[ActionClass, ...] = (ActionClass.DO_NOT_ACT, ActionClass.DEFER),
    expected: ActionClass | None = None,
    required: tuple[ReasonCode, ...] = (),
    suppression: tuple[ReasonCode, ...] = (),
    now: datetime = FIXED_NOW,
    temporal_inputs: TemporalInputs | None = None,
    motivation: MotivationalSnapshot | None = None,
    overlays: tuple[TriggerSnapshot, ...] = (),
    persona_id: str | None = None,
    conversation_id: str | None = None,
    tags: tuple[str, ...] = (),
    notes: str = "Expectation is a test contract, not a runtime action decision.",
) -> ScenarioEntry:
    if temporal_inputs is None:
        activity = () if age is None else ((actor, age, f"{scenario_id}-activity"),)
        temporal_inputs = _inputs(now, activity=activity, ever_user=actor == "user" and age is not None, ever_persona=actor == "persona" and age is not None)
    if motivation is None:
        motivation = _motivation(now, needs=needs, goals=goals)
    scenario = build_autonomy_scenario(
        scenario_id=scenario_id,
        description=description,
        now=now,
        timezone_name=DEFAULT_TIMEZONE,
        temporal_inputs=temporal_inputs,
        motivational_snapshot=motivation,
        trigger_overlays=overlays,
        persona_id=persona_id,
        conversation_id=conversation_id,
        tags=tags,
    )
    contract = ScenarioExpectation(
        expected_action_class=expected,
        allowed_action_classes=allowed,
        required_reason_codes=required,
        expected_suppression_reasons=suppression,
        notes=notes,
    )
    return scenario, contract


def _system_overlay(now: datetime, age: float = 30) -> TriggerSnapshot:
    temporal = compute_temporal_context(_inputs(now, activity=(("system", age, "global-system-event"),)), timezone_name=DEFAULT_TIMEZONE, now=now)
    return compute_trigger_snapshot(temporal=temporal, motivation=_motivation(now))


def _scenario_catalog() -> tuple[ScenarioEntry, ...]:
    entries: list[ScenarioEntry] = []
    # A-G: canonical inactivity and motivation cases.
    entries.append(_entry("a_fresh_no_activity", "Fresh Persona with no durable user or Persona activity.", required=(ReasonCode.NO_DURABLE_ACTIVITY,), tags=("fresh",)))
    entries.append(_entry("b_user_just_spoke", "User activity a few seconds ago.", age=5, required=(ReasonCode.RECENT_USER_ACTIVITY,)))
    entries.append(_entry("c_short_idle_3m", "Three minutes since user activity.", age=180, required=(ReasonCode.INSUFFICIENT_IDLE,)))
    entries.append(_entry("d_medium_idle_30m", "Thirty minutes idle without strong durable motivation.", age=1800, required=(ReasonCode.INSUFFICIENT_IDLE,)))
    entries.append(_entry("e_long_idle_5h", "Five hours idle with meaningful idle pressure.", age=18000, allowed=(ActionClass.ACT, ActionClass.DEFER), required=(ReasonCode.LONG_IDLE,)))
    entries.append(_entry("f_very_long_idle_24h", "At least twenty-four hours idle, pressure saturated.", age=86400, allowed=(ActionClass.ACT, ActionClass.DEFER), required=(ReasonCode.LONG_IDLE,)))
    entries.append(_entry("g_strong_need_only", "Strong Need and sufficient idle, without a Goal.", age=18000, needs=(_need(FIXED_NOW),), allowed=(ActionClass.ACT, ActionClass.DEFER), required=(ReasonCode.STRONG_NEED,)))
    # H-N: competing signals and cross-scope composition.
    entries.append(_entry("h_strong_need_recent_user", "Strong Need while the user has just spoken.", age=5, needs=(_need(FIXED_NOW),), required=(ReasonCode.RECENT_USER_ACTIVITY,), suppression=(ReasonCode.RECENT_USER_ACTIVITY,)))
    entries.append(_entry("i_stale_active_goal", "Old active Goal with sufficient idle.", age=18000, goals=(_goal(FIXED_NOW, updated_age=5 * 86400),), allowed=(ActionClass.ACT, ActionClass.DEFER), required=(ReasonCode.STALE_GOAL,)))
    entries.append(_entry("j_goal_deadline_24h", "Active Goal deadline within twenty-four hours.", age=18000, goals=(_goal(FIXED_NOW, deadline=86400),), allowed=(ActionClass.ACT, ActionClass.DEFER), required=(ReasonCode.DEADLINE_PRESSURE,)))
    entries.append(_entry("k_goal_overdue", "Overdue Goal produces pressure facts only; no execution.", age=18000, goals=(_goal(FIXED_NOW, deadline=-3600),), allowed=(ActionClass.DO_NOT_ACT, ActionClass.DEFER), required=(ReasonCode.DEADLINE_PRESSURE,)))
    entries.append(_entry("l_system_event_recent_user", "Fresh system event with recent user activity.", temporal_inputs=_inputs(FIXED_NOW, activity=(("system", 30, "system-l"), ("user", 20, "user-l")), ever_user=True), required=(ReasonCode.RECENT_USER_ACTIVITY, ReasonCode.SYSTEM_EVENT), suppression=(ReasonCode.RECENT_USER_ACTIVITY,)))
    entries.append(_entry("m_system_event_long_idle", "Long-idle conversation plus an independent fresh global system event.", age=18000, overlays=(_system_overlay(FIXED_NOW),), allowed=(ActionClass.ACT, ActionClass.DEFER), required=(ReasonCode.LONG_IDLE, ReasonCode.SYSTEM_EVENT), tags=("global", "conversation-scoped"), notes="Global system activity is composed separately from this conversation's idle context; composition is test-only."))
    entries.append(_entry("n_conflicting_scoped_signals", "Recent user activity, long idle from separate scope, Need, deadline, and system event.", age=18000, needs=(_need(FIXED_NOW),), goals=(_goal(FIXED_NOW, deadline=3600),), overlays=(_system_overlay(FIXED_NOW),), allowed=(ActionClass.DO_NOT_ACT, ActionClass.DEFER, ActionClass.ACT), required=(ReasonCode.CONFLICTING_SIGNALS,), suppression=(ReasonCode.RECENT_USER_ACTIVITY,), tags=("conflict",), notes="The separate-scope user event is 5 seconds old and global recent-user policy suppresses an otherwise eligible proactive decision."))
    # The independent recent-user fact is added as a second deterministic scope.
    recent_scope = _inputs(FIXED_NOW, activity=(("user", 5, "other-scope-recent-user"),), ever_user=True)
    recent_temporal = compute_temporal_context(recent_scope, timezone_name=DEFAULT_TIMEZONE, now=FIXED_NOW)
    recent_overlay = compute_trigger_snapshot(temporal=recent_temporal, motivation=_motivation(FIXED_NOW))
    n_scenario, n_contract = entries[-1]
    n_scenario = build_autonomy_scenario(
        scenario_id=n_scenario.scenario_id, description=n_scenario.description,
        now=FIXED_NOW, timezone_name=DEFAULT_TIMEZONE,
        temporal_inputs=n_scenario.temporal_inputs,
        motivational_snapshot=n_scenario.motivational_snapshot,
        trigger_overlays=(_system_overlay(FIXED_NOW), recent_overlay),
        tags=n_scenario.tags,
    )
    entries[-1] = (n_scenario, n_contract)
    entries.append(_entry("o_equal_strength_ordering", "Equal-strength Need signals retain deterministic type/key ordering.", age=1800, needs=(_need(FIXED_NOW, key="z_need", activation=.8), _need(FIXED_NOW, key="a_need", activation=.8)), required=(ReasonCode.CONFLICTING_SIGNALS,)))
    # P-Z: determinism, thresholds, timezone and trigger edge fixtures.
    entries.append(_entry("p_restart_equivalence", "Rebuilding from identical durable facts produces the same snapshot.", age=900, needs=(_need(FIXED_NOW, age=120),), tags=("restart",)))
    entries.append(_entry("q_same_timestamp_100_repeat", "Same fixed timestamp and facts are repeatable one hundred times.", age=3600, needs=(_need(FIXED_NOW, age=300),), tags=("determinism",)))
    for suffix, seconds in (("below", 119.999), ("at", 120.0), ("above", 120.001)):
        entries.append(_entry(f"r_idle_2m_{suffix}", f"Idle boundary at two minutes: {seconds} seconds.", age=seconds, required=(ReasonCode.INSUFFICIENT_IDLE,)))
    for suffix, seconds in (("below", 899.999), ("at", 900.0), ("above", 900.001)):
        entries.append(_entry(f"s_idle_15m_{suffix}", f"Idle boundary at fifteen minutes: {seconds} seconds.", age=seconds, required=(ReasonCode.INSUFFICIENT_IDLE,)))
    for suffix, seconds in (("below", 7199.999), ("at", 7200.0), ("above", 7200.001)):
        required = (ReasonCode.INSUFFICIENT_IDLE,) if suffix == "below" else (ReasonCode.LONG_IDLE,)
        entries.append(_entry(f"t_idle_2h_{suffix}", f"Idle boundary at two hours: {seconds} seconds.", age=seconds, required=required, notes="The just-below sample remains in M3's `idle` band; LONG_IDLE begins at the exact 7200-second boundary."))
    entries.append(_entry("u_idle_24h_saturation", "Idle pressure reaches exact saturation at twenty-four hours.", age=86400, required=(ReasonCode.LONG_IDLE,)))
    for hour, minute, second, daypart in ((4,59,59,"late_night"),(5,0,0,"morning"),(11,59,59,"morning"),(12,0,0,"afternoon"),(16,59,59,"afternoon"),(17,0,0,"evening"),(20,59,59,"evening"),(21,0,0,"night")):
        local_now = datetime(2026, 9, 23, hour, minute, second, tzinfo=timezone(timedelta(hours=9)))
        entries.append(_entry(f"v_daypart_{hour:02d}{minute:02d}{second:02d}", f"Daypart boundary {hour:02d}:{minute:02d}:{second:02d} ({daypart}).", now=local_now, tags=(daypart,)))
    entries.append(_entry("w_need_persistence_zero", "Need persistence lower extreme.", age=1800, needs=(_need(FIXED_NOW, persistence=0.0, age=86400),)))
    entries.append(_entry("w_need_persistence_one", "Need persistence upper extreme.", age=1800, needs=(_need(FIXED_NOW, persistence=1.0, age=86400),)))
    for label, horizon in (("beyond_7d", 8 * 86400), ("at_7d", 7 * 86400), ("1d", 86400), ("now", 0), ("overdue", -86400)):
        entries.append(_entry(f"x_goal_deadline_{label}", f"Goal deadline horizon case: {label}.", age=18000, goals=(_goal(FIXED_NOW, deadline=horizon),), allowed=(ActionClass.DO_NOT_ACT, ActionClass.DEFER, ActionClass.ACT), required=(() if horizon > 7 * 86400 else (ReasonCode.DEADLINE_PRESSURE,))))
    user_cutoff = 5 * 60 * math.log2(1000)
    system_cutoff = 30 * 60 * math.log2(1000)
    entries.append(_entry("y_user_trigger_at_epsilon", "USER_ACTIVITY just below its active epsilon boundary (10 ms margin).", age=user_cutoff + 0.01, required=()))
    entries.append(_entry("y_user_trigger_above_epsilon", "USER_ACTIVITY just above its active epsilon boundary (10 ms margin).", age=user_cutoff - 0.01, required=(ReasonCode.RECENT_USER_ACTIVITY,)))
    entries.append(_entry("y_system_trigger_at_epsilon", "SYSTEM_EVENT just below its active epsilon boundary (10 ms margin).", temporal_inputs=_inputs(FIXED_NOW, activity=(("system", system_cutoff + 0.01, "system-y-at"),)), required=()))
    entries.append(_entry("y_system_trigger_above_epsilon", "SYSTEM_EVENT just above its active epsilon boundary (10 ms margin).", temporal_inputs=_inputs(FIXED_NOW, activity=(("system", system_cutoff - 0.01, "system-y-above"),)), required=(ReasonCode.SYSTEM_EVENT,)))
    duplicate_need_a = NeedState(
        "duplicate", .4, .9, .8, .56, .5, FIXED_NOW - timedelta(seconds=30),
        evidence_refs=(MotivationEvidenceRef("need_event", "event-a", occurred_at=FIXED_NOW - timedelta(seconds=30)),),
    )
    duplicate_need_b = NeedState(
        "duplicate", .4, .85, .7, .5, .2, FIXED_NOW - timedelta(seconds=60),
        evidence_refs=(MotivationEvidenceRef("need_event", "event-b", occurred_at=FIXED_NOW - timedelta(seconds=60)),),
    )
    entries.append(_entry("z_duplicate_key_coalescing", "Duplicate semantic Need trigger keeps stable coalesced provenance.", age=3600, needs=(duplicate_need_a, duplicate_need_b), required=(ReasonCode.STRONG_NEED,)))
    # Persona A/B and global-vs-conversation isolation are independent fixtures.
    entries.append(_entry("persona_a_isolation", "Persona A owns its long idle and strong Need fixture.", age=18000, needs=(_need(FIXED_NOW),), allowed=(ActionClass.ACT, ActionClass.DEFER), required=(ReasonCode.STRONG_NEED, ReasonCode.LONG_IDLE), persona_id="persona-a", tags=("persona-isolation",)))
    entries.append(_entry("persona_b_isolation", "Persona B owns recent activity and no Need fixture.", age=5, persona_id="persona-b", conversation_id="conversation-b", required=(ReasonCode.RECENT_USER_ACTIVITY,), tags=("persona-isolation",)))
    entries.append(_entry("conversation_global_recent", "Global user activity includes a recent event from another conversation.", temporal_inputs=_inputs(FIXED_NOW, activity=(("user", 5, "other-conversation-user"),), ever_user=True), conversation_id=None, required=(ReasonCode.RECENT_USER_ACTIVITY,), tags=("global-scope",)))
    entries.append(_entry("conversation_current_idle", "Current conversation excludes the other conversation's recent event.", age=18000, conversation_id="conversation-current", required=(ReasonCode.LONG_IDLE,), tags=("conversation-scope",)))
    return tuple(entries)


SCENARIO_CATALOG: tuple[ScenarioEntry, ...] = _scenario_catalog()
SCENARIOS: dict[str, AutonomyScenario] = {scenario.scenario_id: scenario for scenario, _ in SCENARIO_CATALOG}
EXPECTATIONS: dict[str, ScenarioExpectation] = {scenario.scenario_id: expectation for scenario, expectation in SCENARIO_CATALOG}


def get_scenario(scenario_id: str) -> ScenarioEntry:
    for scenario, expectation in SCENARIO_CATALOG:
        if scenario.scenario_id == scenario_id:
            return scenario, expectation
    raise KeyError(scenario_id)
