"""Test-only scenario assembly over M2/M3 read-only projections.

This module intentionally defines no autonomy policy or runtime decision path.
It only assembles fixed-time inputs and serializes their derived facts.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import json
from typing import Mapping, Protocol, TypeAlias

from app.models.motivation import MotivationalSnapshot
from app.models.temporal_context import TemporalContext, TemporalInputs, require_aware
from app.models.trigger_context import TriggerSignal, TriggerSnapshot, TriggerType
from app.services.mindcore.temporal_context import compute_temporal_context
from app.services.mindcore.trigger_context import compute_trigger_snapshot


class ActionClass(StrEnum):
    """M5 contract vocabulary only; never used by production runtime."""

    ACT = "ACT"
    DO_NOT_ACT = "DO_NOT_ACT"
    DEFER = "DEFER"


class ReasonCode(StrEnum):
    """Stable M5 test vocabulary. M4 does not calculate these reasons."""

    RECENT_USER_ACTIVITY = "RECENT_USER_ACTIVITY"
    INSUFFICIENT_IDLE = "INSUFFICIENT_IDLE"
    LONG_IDLE = "LONG_IDLE"
    STRONG_NEED = "STRONG_NEED"
    STALE_GOAL = "STALE_GOAL"
    DEADLINE_PRESSURE = "DEADLINE_PRESSURE"
    SYSTEM_EVENT = "SYSTEM_EVENT"
    NO_DURABLE_ACTIVITY = "NO_DURABLE_ACTIVITY"
    CONFLICTING_SIGNALS = "CONFLICTING_SIGNALS"
    NO_ACTIONABLE_MOTIVATION = "NO_ACTIONABLE_MOTIVATION"


@dataclass(frozen=True, slots=True)
class ScenarioExpectation:
    """Human-authored test contract, separate from scenario/runtime inputs."""

    expected_action_class: ActionClass | None = None
    allowed_action_classes: tuple[ActionClass, ...] = ()
    required_reason_codes: tuple[ReasonCode, ...] = ()
    forbidden_reason_codes: tuple[ReasonCode, ...] = ()
    expected_suppression_reasons: tuple[ReasonCode, ...] = ()
    notes: str = ""

    def __post_init__(self) -> None:
        if self.expected_action_class is not None:
            object.__setattr__(self, "expected_action_class", ActionClass(self.expected_action_class))
        for field in (
            "allowed_action_classes", "required_reason_codes",
            "forbidden_reason_codes", "expected_suppression_reasons",
        ):
            enum_type = ActionClass if field == "allowed_action_classes" else ReasonCode
            values = tuple(enum_type(value) for value in getattr(self, field))
            object.__setattr__(self, field, tuple(sorted(set(values), key=str)))
        if self.expected_action_class is not None and self.allowed_action_classes and (
            self.expected_action_class not in self.allowed_action_classes
        ):
            raise ValueError("scenario_expectation_expected_action_not_allowed")


@dataclass(frozen=True, slots=True)
class DerivedScenarioInputs:
    """Read-only M3 outputs; deliberately contains no action result."""

    temporal: TemporalContext
    motivation: MotivationalSnapshot
    trigger: TriggerSnapshot


@dataclass(frozen=True, slots=True)
class AutonomyScenario:
    """Immutable fixed-time facts used by a future autonomy test/engine."""

    scenario_id: str
    description: str
    now: datetime
    timezone: str
    temporal_inputs: TemporalInputs
    motivational_snapshot: MotivationalSnapshot
    trigger_snapshot: TriggerSnapshot
    trigger_overlays: tuple[TriggerSnapshot, ...] = ()
    persona_id: str | None = None
    conversation_id: str | None = None
    metadata: tuple[tuple[str, str], ...] = ()
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.scenario_id.strip() or not self.description.strip():
            raise ValueError("autonomy_scenario_identity_invalid")
        require_aware(self.now, "autonomy_scenario_now")
        object.__setattr__(self, "now", self.now.astimezone(timezone.utc))
        object.__setattr__(self, "trigger_overlays", tuple(self.trigger_overlays))
        object.__setattr__(self, "metadata", tuple(sorted((str(k), str(v)) for k, v in self.metadata)))
        object.__setattr__(self, "tags", tuple(sorted(set(self.tags))))


class AutonomyDecisionLike(Protocol):
    """Optional future-engine adapter seam; no implementation exists in M4."""

    @property
    def action_class(self) -> ActionClass: ...

    @property
    def reason_codes(self) -> tuple[ReasonCode, ...]: ...


def run_scenario_inputs(
    *,
    now: datetime,
    timezone_name: str,
    temporal_inputs: TemporalInputs,
    motivational_snapshot: MotivationalSnapshot,
    trigger_overlays: tuple[TriggerSnapshot, ...] = (),
) -> DerivedScenarioInputs:
    """Derive only M3 temporal/trigger inputs, never an autonomy decision.

    Overlays are test-only composition for independent scopes (for example,
    global system activity next to one conversation's idle context).
    """
    require_aware(now, "autonomy_scenario_now")
    current = now.astimezone(timezone.utc)
    if motivational_snapshot.generated_at.astimezone(timezone.utc) != current:
        raise ValueError("autonomy_inputs_must_share_fixed_now")
    temporal = compute_temporal_context(temporal_inputs, timezone_name=timezone_name, now=current)
    trigger = compute_trigger_snapshot(temporal=temporal, motivation=motivational_snapshot)
    for overlay in trigger_overlays:
        if overlay.generated_at.astimezone(timezone.utc) != current:
            raise ValueError("autonomy_trigger_overlays_must_share_fixed_now")
        trigger = _merge_scope_triggers(trigger, overlay)
    return DerivedScenarioInputs(temporal, motivational_snapshot, trigger)


def _merge_scope_triggers(left: TriggerSnapshot, right: TriggerSnapshot) -> TriggerSnapshot:
    """Append facts from distinct test scopes without coalescing or ranking actions."""
    merged = list((*left.triggers, *right.triggers))
    merged.sort(key=lambda item: (
        -item.strength, -item.freshness, str(item.trigger_type), item.key,
        item.source, item.occurred_at.isoformat(), item.source_refs,
    ))
    warnings = tuple(sorted(set(left.data_warnings) | set(right.data_warnings)))
    return TriggerSnapshot(
        generated_at=left.generated_at,
        triggers=tuple(merged),
        strongest_trigger_types=left.strongest_trigger_types + right.strongest_trigger_types,
        strongest_trigger_keys=left.strongest_trigger_keys + right.strongest_trigger_keys,
        has_active_triggers=left.has_active_triggers or right.has_active_triggers,
        data_warnings=warnings,
    )


def build_autonomy_scenario(
    *,
    scenario_id: str,
    description: str,
    now: datetime,
    timezone_name: str,
    temporal_inputs: TemporalInputs,
    motivational_snapshot: MotivationalSnapshot,
    persona_id: str | None = None,
    conversation_id: str | None = None,
    metadata: Mapping[str, str] | None = None,
    tags: tuple[str, ...] = (),
    trigger_overlays: tuple[TriggerSnapshot, ...] = (),
) -> AutonomyScenario:
    """Build a fixed-time scenario using read-only M3 projections."""
    derived = run_scenario_inputs(
        now=now,
        timezone_name=timezone_name,
        temporal_inputs=temporal_inputs,
        motivational_snapshot=motivational_snapshot,
        trigger_overlays=trigger_overlays,
    )
    return AutonomyScenario(
        scenario_id=scenario_id,
        description=description,
        now=now,
        timezone=timezone_name,
        temporal_inputs=temporal_inputs,
        motivational_snapshot=motivational_snapshot,
        trigger_snapshot=derived.trigger,
        trigger_overlays=trigger_overlays,
        persona_id=persona_id,
        conversation_id=conversation_id,
        metadata=tuple((metadata or {}).items()),
        tags=tags,
    )


def assert_scenario_contract(
    expectation: ScenarioExpectation,
    *,
    action_class: ActionClass | None = None,
    reason_codes: tuple[ReasonCode, ...] = (),
    suppression_reasons: tuple[ReasonCode, ...] = (),
) -> None:
    """Reusable assertion helper for an eventual M5 adapter result."""
    if action_class is not None:
        action_class = ActionClass(action_class)
        if expectation.expected_action_class is not None and action_class != expectation.expected_action_class:
            raise AssertionError(f"unexpected_action_class:{action_class}")
        if expectation.allowed_action_classes and action_class not in expectation.allowed_action_classes:
            raise AssertionError(f"disallowed_action_class:{action_class}")
    reasons = {ReasonCode(value) for value in reason_codes}
    missing = set(expectation.required_reason_codes) - reasons
    forbidden = set(expectation.forbidden_reason_codes) & reasons
    if missing:
        raise AssertionError(f"missing_reason_codes:{','.join(sorted(map(str, missing)))}")
    if forbidden:
        raise AssertionError(f"forbidden_reason_codes:{','.join(sorted(map(str, forbidden)))}")
    actual_suppressions = {ReasonCode(value) for value in suppression_reasons}
    if actual_suppressions != set(expectation.expected_suppression_reasons):
        raise AssertionError("suppression_reasons_mismatch")


def _json_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, StrEnum):
        return str(value)
    if hasattr(value, "__dataclass_fields__"):
        return {name: _json_value(getattr(value, name)) for name in value.__dataclass_fields__}
    if isinstance(value, Mapping):
        return {str(key): _json_value(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (tuple, list, set, frozenset)):
        values = [_json_value(item) for item in value]
        return sorted(values, key=lambda item: json.dumps(item, sort_keys=True)) if isinstance(value, (set, frozenset)) else values
    return value


def serialize_scenario(
    scenario: AutonomyScenario,
    expectation: ScenarioExpectation | None = None,
) -> str:
    """Stable JSON-like test artifact; no production API or content fields."""
    payload: dict[str, object] = {"scenario": _json_value(scenario)}
    if expectation is not None:
        payload["expectation"] = _json_value(expectation)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


ScenarioEntry: TypeAlias = tuple[AutonomyScenario, ScenarioExpectation]
