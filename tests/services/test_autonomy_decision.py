from __future__ import annotations

from dataclasses import replace
from datetime import timedelta, timezone
import inspect
import json
import unittest

from app.models.autonomy_decision import ActionClass, AutonomyReasonCode as Reason
from app.models.motivation import MotivationalSnapshot
from app.models.trigger_context import TriggerSignal, TriggerSnapshot, TriggerType
from app.services.mindcore.autonomy_decision import (
    ACTIONABLE_DEADLINE_SECONDS,
    MIN_ACTION_IDLE_SECONDS,
    NEED_ACTIVATION_THRESHOLD,
    RECENT_USER_SUPPRESSION_SECONDS,
    STALE_GOAL_SECONDS,
    decide_autonomy,
)
from tests.autonomy.scenarios import FIXED_NOW, get_scenario
from app.services.mindcore.temporal_context import compute_temporal_context


def _replace_scenario(scenario_id: str, *, motivation=None, temporal=None, triggers=None):
    scenario, _ = get_scenario(scenario_id)
    return (
        motivation or scenario.motivational_snapshot,
        temporal or compute_temporal_context(
            scenario.temporal_inputs, timezone_name=scenario.timezone, now=scenario.now
        ),
        triggers or scenario.trigger_snapshot,
    )


def _decision(scenario_id: str, **kwargs):
    return decide_autonomy(*_replace_scenario(scenario_id, **kwargs))


def _serialize(decision) -> str:
    payload = {
        "action_class": str(decision.action_class),
        "reason_codes": [str(item) for item in decision.reason_codes],
        "suppression_reasons": [str(item) for item in decision.suppression_reasons],
        "confidence": decision.confidence,
        "urgency": decision.urgency,
        "primary_trigger_type": str(decision.primary_trigger_type) if decision.primary_trigger_type else None,
        "primary_trigger_key": decision.primary_trigger_key,
        "evaluated_at": decision.evaluated_at.isoformat(),
        "decision_factors": list(decision.decision_factors),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


class AutonomyDecisionTests(unittest.TestCase):
    def test_core_expected_actions_and_suppression_hierarchy(self) -> None:
        self.assertEqual(_decision("a_fresh_no_activity").action_class, ActionClass.DO_NOT_ACT)
        self.assertEqual(_decision("g_strong_need_only").action_class, ActionClass.ACT)
        self.assertEqual(_decision("i_stale_active_goal").action_class, ActionClass.ACT)
        self.assertEqual(_decision("j_goal_deadline_24h").action_class, ActionClass.ACT)
        recent = _decision("h_strong_need_recent_user")
        self.assertEqual(recent.action_class, ActionClass.DEFER)
        self.assertEqual(recent.suppression_reasons, (Reason.RECENT_USER_ACTIVITY,))
        self.assertEqual(_decision("e_long_idle_5h").action_class, ActionClass.DEFER)
        self.assertEqual(_decision("m_system_event_long_idle").action_class, ActionClass.DEFER)
        self.assertEqual(_decision("k_goal_overdue").action_class, ActionClass.DEFER)

    def test_identical_input_is_byte_stable_for_one_thousand_evaluations(self) -> None:
        first = _decision("q_same_timestamp_100_repeat")
        serialized = _serialize(first)
        for _ in range(1000):
            actual = _decision("q_same_timestamp_100_repeat")
            self.assertEqual(actual, first)
            self.assertEqual(_serialize(actual), serialized)

    def test_trigger_order_does_not_change_decision_or_reason_order(self) -> None:
        motivation, temporal, triggers = _replace_scenario("n_conflicting_scoped_signals")
        forward = decide_autonomy(motivation, temporal, triggers)
        shuffled = replace(triggers, triggers=tuple(reversed(triggers.triggers)))
        shuffled_motivation = replace(
            motivation,
            needs=tuple(reversed(motivation.needs)),
            goals=tuple(reversed(motivation.goals)),
        )
        backward = decide_autonomy(shuffled_motivation, temporal, shuffled)
        self.assertEqual(forward, backward)
        self.assertEqual(tuple(map(str, forward.reason_codes)), tuple(sorted(map(str, forward.reason_codes))))
        self.assertEqual(tuple(map(str, forward.suppression_reasons)), tuple(sorted(map(str, forward.suppression_reasons))))

    def test_restart_rebuilt_snapshots_produce_the_same_decision(self) -> None:
        scenario, _ = get_scenario("p_restart_equivalence")
        temporal_a = compute_temporal_context(
            scenario.temporal_inputs, timezone_name=scenario.timezone, now=scenario.now,
        )
        temporal_b = compute_temporal_context(
            scenario.temporal_inputs, timezone_name=scenario.timezone, now=scenario.now,
        )
        first = decide_autonomy(scenario.motivational_snapshot, temporal_a, scenario.trigger_snapshot)
        second = decide_autonomy(scenario.motivational_snapshot, temporal_b, scenario.trigger_snapshot)
        self.assertEqual(first, second)

    def test_timezone_equivalent_snapshot_instants_produce_equal_decision(self) -> None:
        motivation, temporal, triggers = _replace_scenario("g_strong_need_only")
        offset = timezone(timedelta(hours=9))
        localized_motivation = replace(motivation, generated_at=motivation.generated_at.astimezone(offset))
        localized_temporal = replace(
            temporal,
            now=temporal.now.astimezone(offset),
            local_now=temporal.local_now.astimezone(offset),
            last_user_activity_at=temporal.last_user_activity_at.astimezone(offset) if temporal.last_user_activity_at else None,
            last_persona_activity_at=temporal.last_persona_activity_at.astimezone(offset) if temporal.last_persona_activity_at else None,
            last_system_activity_at=temporal.last_system_activity_at.astimezone(offset) if temporal.last_system_activity_at else None,
            last_conversation_activity_at=temporal.last_conversation_activity_at.astimezone(offset) if temporal.last_conversation_activity_at else None,
        )
        localized_triggers = replace(
            triggers,
            generated_at=triggers.generated_at.astimezone(offset),
            triggers=tuple(replace(item, occurred_at=item.occurred_at.astimezone(offset)) for item in triggers.triggers),
        )
        self.assertEqual(
            decide_autonomy(motivation, temporal, triggers),
            decide_autonomy(localized_motivation, localized_temporal, localized_triggers),
        )

    def test_snapshot_time_mismatch_is_rejected_before_decision(self) -> None:
        motivation, temporal, triggers = _replace_scenario("g_strong_need_only")
        with self.assertRaisesRegex(ValueError, "must_share_generated_at"):
            decide_autonomy(replace(motivation, generated_at=motivation.generated_at + timedelta(seconds=1)), temporal, triggers)
        with self.assertRaisesRegex(ValueError, "must_share_generated_at"):
            decide_autonomy(motivation, temporal, replace(triggers, generated_at=triggers.generated_at + timedelta(seconds=1)))

    def test_invalid_temporal_numeric_context_is_rejected(self) -> None:
        motivation, temporal, triggers = _replace_scenario("g_strong_need_only")
        with self.assertRaisesRegex(ValueError, "temporal_context_invalid"):
            decide_autonomy(motivation, replace(temporal, idle_pressure=float("nan")), triggers)

    def test_recent_user_suppression_boundary_with_actionable_need(self) -> None:
        motivation, temporal, triggers = _replace_scenario("g_strong_need_only")
        need = motivation.needs[0]
        for age, suppressed in (
            (RECENT_USER_SUPPRESSION_SECONDS - .001, True),
            (RECENT_USER_SUPPRESSION_SECONDS, False),
            (RECENT_USER_SUPPRESSION_SECONDS + .001, False),
        ):
            user_at = FIXED_NOW - timedelta(seconds=age)
            modified_temporal = replace(
                temporal, last_user_activity_at=user_at, seconds_since_user_activity=age,
            )
            modified_signal = TriggerSignal(
                TriggerType.USER_ACTIVITY, "durable_user_message", "recent-user", .5, .5, .15,
                user_at, age,
            )
            modified_triggers = replace(
                triggers,
                triggers=triggers.triggers + (modified_signal,),
            )
            with self.subTest(age=age):
                decision = decide_autonomy(motivation, modified_temporal, modified_triggers)
                if suppressed:
                    self.assertIn(Reason.RECENT_USER_ACTIVITY, decision.suppression_reasons)
                    self.assertEqual(decision.action_class, ActionClass.DEFER)
                else:
                    self.assertNotIn(Reason.RECENT_USER_ACTIVITY, decision.suppression_reasons)

    def test_idle_need_staleness_and_deadline_boundaries(self) -> None:
        motivation, temporal, triggers = _replace_scenario("g_strong_need_only")
        for idle, expected in ((MIN_ACTION_IDLE_SECONDS - .001, ActionClass.DEFER),
                               (MIN_ACTION_IDLE_SECONDS, ActionClass.ACT),
                               (MIN_ACTION_IDLE_SECONDS + .001, ActionClass.ACT)):
            decision = decide_autonomy(motivation, replace(temporal, idle_duration_seconds=idle), triggers)
            self.assertEqual(decision.action_class, expected)

        base_need = motivation.needs[0]
        for activation, eligible in ((NEED_ACTIVATION_THRESHOLD - .001, False),
                                     (NEED_ACTIVATION_THRESHOLD, True),
                                     (NEED_ACTIVATION_THRESHOLD + .001, True)):
            need = replace(base_need, activation=activation)
            decision = decide_autonomy(replace(motivation, needs=(need,)), temporal, triggers)
            self.assertEqual(Reason.STRONG_NEED in decision.reason_codes, eligible)

        goal_base = get_scenario("i_stale_active_goal")[0].motivational_snapshot.goals[0]
        for age, stale in ((STALE_GOAL_SECONDS - .001, False), (STALE_GOAL_SECONDS, True),
                           (STALE_GOAL_SECONDS + .001, True)):
            goal = replace(goal_base, updated_at=FIXED_NOW - timedelta(seconds=age))
            decision = _decision("i_stale_active_goal", motivation=MotivationalSnapshot(FIXED_NOW, goals=(goal,)))
            self.assertEqual(Reason.STALE_GOAL in decision.reason_codes, stale)

        deadline_base = get_scenario("j_goal_deadline_24h")[0].motivational_snapshot.goals[0]
        for remaining, pressure in ((7 * 86400 + .001, False), (7 * 86400, True),
                                    (ACTIONABLE_DEADLINE_SECONDS, True),
                                    (ACTIONABLE_DEADLINE_SECONDS + .001, True)):
            goal = replace(deadline_base, expires_at=FIXED_NOW + timedelta(seconds=remaining))
            decision = _decision("j_goal_deadline_24h", motivation=MotivationalSnapshot(FIXED_NOW, goals=(goal,)))
            self.assertEqual(Reason.DEADLINE_PRESSURE in decision.reason_codes, pressure)

    def test_persona_scenarios_and_global_recent_activity_are_isolated(self) -> None:
        persona_a = _decision("persona_a_isolation")
        persona_b = _decision("persona_b_isolation")
        self.assertEqual(persona_a.action_class, ActionClass.ACT)
        self.assertIn(Reason.RECENT_USER_ACTIVITY, persona_b.reason_codes)
        self.assertEqual(persona_b.action_class, ActionClass.DO_NOT_ACT)
        self.assertEqual(_decision("conversation_global_recent").action_class, ActionClass.DO_NOT_ACT)
        self.assertEqual(_decision("conversation_current_idle").action_class, ActionClass.DEFER)

    def test_conflict_annotations_and_explainability_are_content_free(self) -> None:
        decision = _decision("n_conflicting_scoped_signals")
        self.assertIn(Reason.CONFLICTING_SIGNALS, decision.reason_codes)
        self.assertEqual(decision.action_class, ActionClass.DEFER)
        payload = _serialize(decision)
        self.assertNotIn("message", payload.casefold())
        self.assertNotIn("prompt", payload.casefold())
        self.assertTrue(all("=" in item for item in decision.decision_factors))
        self.assertGreaterEqual(decision.confidence, 0.0)
        self.assertLessEqual(decision.confidence, 1.0)
        self.assertGreaterEqual(decision.urgency, 0.0)
        self.assertLessEqual(decision.urgency, 1.0)

    def test_policy_function_has_no_io_runtime_or_execution_dependencies(self) -> None:
        source = inspect.getsource(__import__("app.services.mindcore.autonomy_decision", fromlist=["decide_autonomy"]))
        for forbidden in ("asyncpg", "libsql", "provider", "scheduler", "create_task", "datetime.now", "random", "send_message"):
            self.assertNotIn(forbidden, source.casefold())


if __name__ == "__main__":
    unittest.main()
