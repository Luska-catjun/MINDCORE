from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
import inspect
import json
import unittest

from app.models.autonomy_decision import ActionClass, AutonomyDecision, AutonomyReasonCode as Reason
from app.models.autonomy_intention import (
    AutonomyIntention,
    DEFAULT_INTENTION_CONSTRAINTS,
    IntentionConstraint,
    IntentionType,
    TargetKind,
)
from app.models.motivation import MotivationalSnapshot
from app.models.trigger_context import TriggerSnapshot, TriggerType
from app.services.mindcore.autonomy_decision import decide_autonomy
from app.services.mindcore.autonomy_intention import derive_autonomy_intention
from app.services.mindcore.temporal_context import compute_temporal_context
from app.services.mindcore.trigger_context import compute_trigger_snapshot
from tests.autonomy.intention_contract import IntentionExpectation, assert_intention_contract
from tests.autonomy.scenarios import FIXED_NOW, SCENARIO_CATALOG, get_scenario


def _inputs(scenario_id: str):
    scenario, _ = get_scenario(scenario_id)
    temporal = compute_temporal_context(
        scenario.temporal_inputs, timezone_name=scenario.timezone, now=scenario.now,
    )
    decision = decide_autonomy(scenario.motivational_snapshot, temporal, scenario.trigger_snapshot)
    return scenario, temporal, decision


def _json(intention: AutonomyIntention | None) -> str:
    if intention is None:
        return "null"
    return json.dumps({
        "intention_type": str(intention.intention_type),
        "target_kind": str(intention.target_kind),
        "target_key": intention.target_key,
        "reason_codes": [str(item) for item in intention.reason_codes],
        "source_trigger_type": str(intention.source_trigger_type) if intention.source_trigger_type else None,
        "source_trigger_key": intention.source_trigger_key,
        "related_need_keys": intention.related_need_keys,
        "related_goal_keys": intention.related_goal_keys,
        "constraints": [str(item) for item in intention.constraints],
        "urgency": intention.urgency,
        "confidence": intention.confidence,
        "evaluated_at": intention.evaluated_at.isoformat(),
        "intention_key": intention.intention_key,
    }, sort_keys=True, separators=(",", ":"))


class AutonomyIntentionModelTests(unittest.TestCase):
    def test_model_is_immutable_and_has_no_dialogue_fields(self) -> None:
        intention = _intention_for("g_strong_need_only")
        self.assertIsNotNone(intention)
        assert intention is not None
        self.assertEqual(intention.intention_type, IntentionType.CHECK_IN)
        self.assertEqual(intention.target_kind, TargetKind.NEED)
        self.assertEqual(intention.intention_key, "CHECK_IN:curiosity")
        self.assertEqual(intention.constraints, DEFAULT_INTENTION_CONSTRAINTS)
        self.assertIn(IntentionConstraint.NO_ACTION_EXECUTION, intention.constraints)
        self.assertNotIn("message", _json(intention).casefold())
        self.assertNotIn("prompt", _json(intention).casefold())
        with self.assertRaises(FrozenInstanceError):
            intention.target_key = "changed"  # type: ignore[misc]

    def test_unknown_enum_invalid_target_kind_and_key_are_rejected(self) -> None:
        intention = _intention_for("g_strong_need_only")
        assert intention is not None
        with self.assertRaises(ValueError):
            replace(intention, intention_type="UNRECOGNIZED")
        with self.assertRaisesRegex(ValueError, "target_kind_mismatch"):
            replace(intention, target_kind=TargetKind.GOAL)
        with self.assertRaisesRegex(ValueError, "key_mismatch"):
            replace(intention, intention_key="CHECK_IN:other")


def _intention_for(scenario_id: str) -> AutonomyIntention | None:
    scenario, temporal, decision = _inputs(scenario_id)
    return derive_autonomy_intention(
        decision, scenario.motivational_snapshot, temporal, scenario.trigger_snapshot,
    )


class AutonomyIntentionTests(unittest.TestCase):
    def test_all_m4_m5_scenarios_return_none_or_exactly_one_valid_intention(self) -> None:
        self.assertEqual(len(SCENARIO_CATALOG), 51)
        for scenario, _ in SCENARIO_CATALOG:
            temporal = compute_temporal_context(
                scenario.temporal_inputs, timezone_name=scenario.timezone, now=scenario.now,
            )
            decision = decide_autonomy(scenario.motivational_snapshot, temporal, scenario.trigger_snapshot)
            results = [derive_autonomy_intention(
                decision, scenario.motivational_snapshot, temporal, scenario.trigger_snapshot,
            )]
            with self.subTest(scenario=scenario.scenario_id):
                if decision.action_class == ActionClass.ACT:
                    self.assertEqual(len(results), 1)
                    self.assertIsNotNone(results[0])
                else:
                    self.assertIsNone(results[0])

    def test_non_act_authority_never_creates_intention(self) -> None:
        for scenario_id in ("a_fresh_no_activity", "b_user_just_spoke", "h_strong_need_recent_user"):
            scenario, temporal, decision = _inputs(scenario_id)
            with self.subTest(scenario=scenario_id):
                self.assertNotEqual(decision.action_class, ActionClass.ACT)
                self.assertIsNone(derive_autonomy_intention(
                    decision, scenario.motivational_snapshot, temporal, scenario.trigger_snapshot,
                ))

    def test_need_goal_and_deadline_types_and_reason_propagation(self) -> None:
        check_in = _intention_for("g_strong_need_only")
        revisit = _intention_for("i_stale_active_goal")
        remind = _intention_for("j_goal_deadline_24h")
        assert check_in is not None and revisit is not None and remind is not None
        self.assertEqual((check_in.intention_type, check_in.target_kind), (IntentionType.CHECK_IN, TargetKind.NEED))
        self.assertIn(Reason.STRONG_NEED, check_in.reason_codes)
        self.assertIn(Reason.LONG_IDLE, check_in.reason_codes)
        self.assertEqual((revisit.intention_type, revisit.target_kind), (IntentionType.REVISIT_GOAL, TargetKind.GOAL))
        self.assertEqual((remind.intention_type, remind.target_kind), (IntentionType.REMIND_DEADLINE, TargetKind.GOAL))
        self.assertIn(Reason.DEADLINE_PRESSURE, remind.reason_codes)
        self.assertIn(Reason.STALE_GOAL, remind.reason_codes)
        self.assertEqual(remind.target_key, "goal-a")
        self.assertEqual(remind.related_goal_keys, ("goal-a",))
        self.assertEqual(remind.source_trigger_type, TriggerType.GOAL_DEADLINE)

    def test_deadline_precedes_staleness_for_the_same_goal(self) -> None:
        intention = _intention_for("j_goal_deadline_24h")
        assert intention is not None
        self.assertEqual(intention.intention_type, IntentionType.REMIND_DEADLINE)
        self.assertEqual(intention.reason_codes, tuple(sorted((
            Reason.CONFLICTING_SIGNALS,
            Reason.DEADLINE_PRESSURE,
            Reason.LONG_IDLE,
            Reason.STALE_GOAL,
        ), key=str)))

    def test_near_deadline_target_precedes_stale_goal_and_need(self) -> None:
        scenario, temporal, _ = _inputs("g_strong_need_only")
        goal = get_scenario("j_goal_deadline_24h")[0].motivational_snapshot.goals[0]
        motivation = MotivationalSnapshot(
            FIXED_NOW,
            needs=scenario.motivational_snapshot.needs,
            goals=(goal,),
        )
        triggers = compute_trigger_snapshot(temporal=temporal, motivation=motivation)
        decision = decide_autonomy(motivation, temporal, triggers)
        self.assertEqual(decision.action_class, ActionClass.ACT)
        intention = derive_autonomy_intention(decision, motivation, temporal, triggers)
        assert intention is not None
        self.assertEqual(intention.intention_type, IntentionType.REMIND_DEADLINE)

    def test_multiple_need_and_goal_target_ties_are_deterministic(self) -> None:
        scenario, temporal, _ = _inputs("g_strong_need_only")
        base_need = scenario.motivational_snapshot.needs[0]
        needs = (
            replace(base_need, key="z_need", activation=.9, urgency=.7, persistence=.5),
            replace(base_need, key="a_need", activation=.9, urgency=.7, persistence=.5),
        )
        motivation = MotivationalSnapshot(FIXED_NOW, needs=needs)
        triggers = compute_trigger_snapshot(temporal=temporal, motivation=motivation)
        decision = decide_autonomy(motivation, temporal, triggers)
        self.assertEqual(decision.action_class, ActionClass.ACT)
        first = derive_autonomy_intention(decision, motivation, temporal, triggers)
        shuffled_motivation = replace(motivation, needs=tuple(reversed(needs)))
        shuffled_triggers = replace(triggers, triggers=tuple(reversed(triggers.triggers)))
        shuffled_decision = decide_autonomy(shuffled_motivation, temporal, shuffled_triggers)
        second = derive_autonomy_intention(shuffled_decision, shuffled_motivation, temporal, shuffled_triggers)
        self.assertEqual(first, second)
        assert first is not None
        self.assertEqual(first.target_key, "a_need")

        stale_scenario, stale_temporal, _ = _inputs("i_stale_active_goal")
        base_goal = stale_scenario.motivational_snapshot.goals[0]
        goals = (
            replace(base_goal, goal_key="z_goal", urgency=.8, updated_at=FIXED_NOW - timedelta(days=4)),
            replace(base_goal, goal_key="a_goal", urgency=.8, updated_at=FIXED_NOW - timedelta(days=4)),
        )
        goal_motivation = MotivationalSnapshot(FIXED_NOW, goals=goals)
        goal_triggers = compute_trigger_snapshot(temporal=stale_temporal, motivation=goal_motivation)
        goal_decision = decide_autonomy(goal_motivation, stale_temporal, goal_triggers)
        self.assertEqual(goal_decision.action_class, ActionClass.ACT)
        goal_intention = derive_autonomy_intention(goal_decision, goal_motivation, stale_temporal, goal_triggers)
        assert goal_intention is not None
        self.assertEqual(goal_intention.target_key, "a_goal")

    def test_multiple_deadlines_choose_closest_then_urgency_then_stable_key(self) -> None:
        scenario, temporal, _ = _inputs("j_goal_deadline_24h")
        base = scenario.motivational_snapshot.goals[0]
        goals = (
            replace(base, goal_key="z_later", expires_at=FIXED_NOW + timedelta(hours=8), urgency=.9),
            replace(base, goal_key="b_closer", expires_at=FIXED_NOW + timedelta(hours=2), urgency=.4),
            replace(base, goal_key="a_same_deadline", expires_at=FIXED_NOW + timedelta(hours=2), urgency=.8),
        )
        motivation = MotivationalSnapshot(FIXED_NOW, goals=goals)
        triggers = compute_trigger_snapshot(temporal=temporal, motivation=motivation)
        decision = decide_autonomy(motivation, temporal, triggers)
        intention = derive_autonomy_intention(decision, motivation, temporal, triggers)
        assert intention is not None
        self.assertEqual(intention.intention_type, IntentionType.REMIND_DEADLINE)
        self.assertEqual(intention.target_key, "a_same_deadline")

    def test_system_event_mapping_requires_an_existing_content_free_signal(self) -> None:
        scenario, temporal, deferred = _inputs("m_system_event_long_idle")
        self.assertEqual(deferred.action_class, ActionClass.DEFER)
        self.assertIsNone(derive_autonomy_intention(deferred, scenario.motivational_snapshot, temporal, scenario.trigger_snapshot))
        # Synthetic ACT tests only the M6 mapping boundary: current M5 does not
        # authorize a system-only event, and M6 does not change that policy.
        authorized = replace(
            deferred,
            action_class=ActionClass.ACT,
            reason_codes=deferred.reason_codes + (Reason.SYSTEM_EVENT,),
            suppression_reasons=(),
        )
        intention = derive_autonomy_intention(
            authorized, scenario.motivational_snapshot, temporal, scenario.trigger_snapshot,
        )
        assert intention is not None
        self.assertEqual(intention.intention_type, IntentionType.ACKNOWLEDGE_EVENT)
        self.assertEqual(intention.target_kind, TargetKind.SYSTEM_EVENT)
        self.assertEqual(intention.source_trigger_type, TriggerType.SYSTEM_EVENT)

    def test_need_goal_and_system_conflict_uses_semantic_hierarchy(self) -> None:
        scenario, temporal, _ = _inputs("g_strong_need_only")
        system_scenario, _, _ = _inputs("m_system_event_long_idle")
        goal = get_scenario("i_stale_active_goal")[0].motivational_snapshot.goals[0]
        motivation = MotivationalSnapshot(FIXED_NOW, needs=scenario.motivational_snapshot.needs, goals=(goal,))
        combined_signals = tuple((*scenario.trigger_snapshot.triggers, *system_scenario.trigger_snapshot.triggers))
        triggers = TriggerSnapshot(FIXED_NOW, triggers=combined_signals)
        decision = decide_autonomy(motivation, temporal, triggers)
        self.assertEqual(decision.action_class, ActionClass.ACT)
        intention = derive_autonomy_intention(decision, motivation, temporal, triggers)
        assert intention is not None
        self.assertEqual(intention.intention_type, IntentionType.REVISIT_GOAL)

    def test_time_mismatch_and_invalid_act_combinations_fail_explicitly(self) -> None:
        scenario, temporal, decision = _inputs("g_strong_need_only")
        with self.assertRaisesRegex(ValueError, "must_share_generated_at"):
            derive_autonomy_intention(
                replace(decision, evaluated_at=decision.evaluated_at + timedelta(seconds=1)),
                scenario.motivational_snapshot, temporal, scenario.trigger_snapshot,
            )
        no_basis = replace(decision, reason_codes=(Reason.LONG_IDLE,))
        with self.assertRaisesRegex(ValueError, "no_actionable_target"):
            derive_autonomy_intention(no_basis, scenario.motivational_snapshot, temporal, scenario.trigger_snapshot)
        missing_need = replace(decision, reason_codes=(Reason.STRONG_NEED,))
        with self.assertRaisesRegex(ValueError, "need_target_missing"):
            derive_autonomy_intention(missing_need, MotivationalSnapshot(FIXED_NOW), temporal, scenario.trigger_snapshot)
        missing_stale = replace(decision, reason_codes=(Reason.STALE_GOAL,))
        with self.assertRaisesRegex(ValueError, "stale_goal_target_missing"):
            derive_autonomy_intention(missing_stale, MotivationalSnapshot(FIXED_NOW), temporal, scenario.trigger_snapshot)
        missing_deadline = replace(decision, reason_codes=(Reason.DEADLINE_PRESSURE,))
        with self.assertRaisesRegex(ValueError, "deadline_target_missing"):
            derive_autonomy_intention(missing_deadline, MotivationalSnapshot(FIXED_NOW), temporal, scenario.trigger_snapshot)

        event_scenario, event_temporal, event_decision = _inputs("m_system_event_long_idle")
        fake_system_act = replace(event_decision, action_class=ActionClass.ACT, reason_codes=(Reason.SYSTEM_EVENT,))
        no_events = TriggerSnapshot(FIXED_NOW)
        with self.assertRaisesRegex(ValueError, "system_event_target_missing"):
            derive_autonomy_intention(fake_system_act, event_scenario.motivational_snapshot, event_temporal, no_events)

    def test_same_inputs_produce_identical_intention_one_thousand_times(self) -> None:
        scenario, temporal, decision = _inputs("j_goal_deadline_24h")
        encoded = None
        for _ in range(1000):
            actual = derive_autonomy_intention(
                decision, scenario.motivational_snapshot, temporal, scenario.trigger_snapshot,
            )
            current = _json(actual)
            if encoded is None:
                encoded = current
            self.assertEqual(current, encoded)

    def test_shuffled_inputs_and_restart_rebuild_keep_same_intention_and_serialization(self) -> None:
        scenario, temporal, decision = _inputs("j_goal_deadline_24h")
        first = derive_autonomy_intention(decision, scenario.motivational_snapshot, temporal, scenario.trigger_snapshot)
        reordered_motivation = replace(
            scenario.motivational_snapshot,
            needs=tuple(reversed(scenario.motivational_snapshot.needs)),
            goals=tuple(reversed(scenario.motivational_snapshot.goals)),
        )
        reordered_triggers = replace(scenario.trigger_snapshot, triggers=tuple(reversed(scenario.trigger_snapshot.triggers)))
        reordered_decision = decide_autonomy(reordered_motivation, temporal, reordered_triggers)
        second = derive_autonomy_intention(reordered_decision, reordered_motivation, temporal, reordered_triggers)
        self.assertEqual(first, second)
        self.assertEqual(_json(first), _json(second))

        restart_temporal = compute_temporal_context(
            scenario.temporal_inputs, timezone_name=scenario.timezone, now=scenario.now,
        )
        restart_decision = decide_autonomy(scenario.motivational_snapshot, restart_temporal, scenario.trigger_snapshot)
        restarted = derive_autonomy_intention(
            restart_decision, scenario.motivational_snapshot, restart_temporal, scenario.trigger_snapshot,
        )
        self.assertEqual(first, restarted)

    def test_persona_and_conversation_scenarios_remain_isolated(self) -> None:
        a, temporal_a, decision_a = _inputs("persona_a_isolation")
        b, temporal_b, decision_b = _inputs("persona_b_isolation")
        result_a = derive_autonomy_intention(decision_a, a.motivational_snapshot, temporal_a, a.trigger_snapshot)
        result_b = derive_autonomy_intention(decision_b, b.motivational_snapshot, temporal_b, b.trigger_snapshot)
        self.assertEqual(result_a.intention_type if result_a else None, IntentionType.CHECK_IN)
        self.assertIsNone(result_b)
        self.assertNotEqual(a.persona_id, b.persona_id)

    def test_intention_contract_helper_and_confidence_urgency_passthrough(self) -> None:
        scenario, temporal, decision = _inputs("g_strong_need_only")
        intention = derive_autonomy_intention(decision, scenario.motivational_snapshot, temporal, scenario.trigger_snapshot)
        assert_intention_contract(IntentionExpectation(
            expected_intention_type=IntentionType.CHECK_IN,
            expected_target_kind=TargetKind.NEED,
            required_target_key="curiosity",
            required_reason_codes=(Reason.STRONG_NEED, Reason.LONG_IDLE),
        ), intention)
        assert intention is not None
        self.assertEqual(intention.confidence, decision.confidence)
        self.assertEqual(intention.urgency, decision.urgency)

    def test_pure_module_has_no_io_provider_scheduler_or_message_generation(self) -> None:
        source = inspect.getsource(__import__("app.services.mindcore.autonomy_intention", fromlist=["derive_autonomy_intention"]))
        for forbidden in ("asyncpg", "libsql", "provider", "scheduler", "create_task", "datetime.now", "random", "send_message", "insert into", "chat_turns"):
            self.assertNotIn(forbidden, source.casefold())


if __name__ == "__main__":
    unittest.main()
