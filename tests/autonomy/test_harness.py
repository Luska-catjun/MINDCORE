from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import unittest

from app.database.schema_contract import CURRENT_TURSO_BASELINE_VERSION
from app.models.motivation import MotivationalSnapshot, NeedState
from app.models.temporal_context import ActivityPoint, TemporalInputs
from app.models.trigger_context import TriggerType
from app.services.mindcore.temporal_context import (
    compute_temporal_context,
    load_temporal_inputs,
)
from app.services.mindcore.trigger_context import (
    SYSTEM_EVENT_FRESHNESS_HALF_LIFE_SECONDS,
    USER_ACTIVITY_FRESHNESS_HALF_LIFE_SECONDS,
    compute_trigger_snapshot,
)
from tests.autonomy.harness import (
    ActionClass,
    ReasonCode,
    ScenarioExpectation,
    assert_scenario_contract,
    build_autonomy_scenario,
    run_scenario_inputs,
    serialize_scenario,
)
from tests.autonomy.scenarios import (
    DEFAULT_TIMEZONE,
    FIXED_NOW,
    EXPECTATIONS,
    SCENARIO_CATALOG,
    SCENARIOS,
    get_scenario,
)


ROOT = Path(__file__).resolve().parents[2]


class AutonomyScenarioHarnessTests(unittest.TestCase):
    def test_catalog_ids_are_unique_and_expectations_are_separate(self) -> None:
        ids = [scenario.scenario_id for scenario, _ in SCENARIO_CATALOG]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(ids), set(SCENARIOS))
        self.assertEqual(set(ids), set(EXPECTATIONS))
        self.assertTrue(all(not hasattr(scenario, "should_act") for scenario, _ in SCENARIO_CATALOG))
        self.assertTrue(all(not hasattr(scenario, "expected_action_class") for scenario, _ in SCENARIO_CATALOG))

    def test_catalog_has_a_to_z_contract_coverage_and_required_scenarios(self) -> None:
        ids = set(SCENARIOS)
        prefixes = set("abcdefghijklmnopqrstuvwxyz")
        self.assertEqual({item[0] for item in ids}, prefixes)
        for required in (
            "a_fresh_no_activity", "b_user_just_spoke", "e_long_idle_5h",
            "f_very_long_idle_24h", "g_strong_need_only", "h_strong_need_recent_user",
            "i_stale_active_goal", "j_goal_deadline_24h", "k_goal_overdue",
            "l_system_event_recent_user", "m_system_event_long_idle",
            "n_conflicting_scoped_signals", "o_equal_strength_ordering",
            "p_restart_equivalence", "q_same_timestamp_100_repeat",
            "z_duplicate_key_coalescing", "persona_a_isolation", "persona_b_isolation",
            "conversation_current_idle", "conversation_global_recent",
        ):
            self.assertIn(required, ids)

    def test_every_catalog_scenario_is_deterministic_and_serializable(self) -> None:
        for scenario, expectation in SCENARIO_CATALOG:
            with self.subTest(scenario=scenario.scenario_id):
                rebuilt = build_autonomy_scenario(
                    scenario_id=scenario.scenario_id,
                    description=scenario.description,
                    now=scenario.now,
                    timezone_name=scenario.timezone,
                    temporal_inputs=scenario.temporal_inputs,
                    motivational_snapshot=scenario.motivational_snapshot,
                    trigger_overlays=scenario.trigger_overlays,
                    persona_id=scenario.persona_id,
                    conversation_id=scenario.conversation_id,
                    metadata=dict(scenario.metadata),
                    tags=scenario.tags,
                )
                self.assertEqual(rebuilt, scenario)
                serialized = serialize_scenario(scenario, expectation)
                self.assertEqual(serialized, serialize_scenario(scenario, expectation))
                payload = json.loads(serialized)
                self.assertEqual(payload["scenario"]["scenario_id"], scenario.scenario_id)
                self.assertEqual(payload["scenario"]["now"], scenario.now.isoformat().replace("+00:00", "Z"))

    def test_fixed_now_must_be_aware_and_inputs_must_share_it(self) -> None:
        scenario, _ = get_scenario("a_fresh_no_activity")
        with self.assertRaisesRegex(ValueError, "timezone_aware"):
            build_autonomy_scenario(
                scenario_id="naive", description="invalid", now=FIXED_NOW.replace(tzinfo=None),
                timezone_name=DEFAULT_TIMEZONE, temporal_inputs=TemporalInputs(),
                motivational_snapshot=MotivationalSnapshot(FIXED_NOW),
            )
        with self.assertRaisesRegex(ValueError, "share_fixed_now"):
            run_scenario_inputs(
                now=scenario.now + timedelta(seconds=1), timezone_name=scenario.timezone,
                temporal_inputs=scenario.temporal_inputs,
                motivational_snapshot=scenario.motivational_snapshot,
            )

    def test_fixed_scenario_inputs_are_restart_equivalent(self) -> None:
        scenario, _ = get_scenario("p_restart_equivalence")
        first = run_scenario_inputs(
            now=scenario.now, timezone_name=scenario.timezone,
            temporal_inputs=scenario.temporal_inputs,
            motivational_snapshot=scenario.motivational_snapshot,
        )
        second = run_scenario_inputs(
            now=scenario.now, timezone_name=scenario.timezone,
            temporal_inputs=scenario.temporal_inputs,
            motivational_snapshot=scenario.motivational_snapshot,
        )
        self.assertEqual(first.temporal, second.temporal)
        self.assertEqual(first.trigger, second.trigger)
        self.assertEqual(serialize_scenario(scenario), serialize_scenario(scenario))

    def test_q_scenario_is_byte_stable_for_one_hundred_runs(self) -> None:
        scenario, expectation = get_scenario("q_same_timestamp_100_repeat")
        outputs = [serialize_scenario(
            build_autonomy_scenario(
                scenario_id=scenario.scenario_id, description=scenario.description,
                now=scenario.now, timezone_name=scenario.timezone,
                temporal_inputs=scenario.temporal_inputs,
                motivational_snapshot=scenario.motivational_snapshot,
                persona_id=scenario.persona_id, conversation_id=scenario.conversation_id,
                tags=scenario.tags,
            ), expectation,
        ) for _ in range(100)]
        self.assertEqual(len(set(outputs)), 1)

    def test_input_order_does_not_change_temporal_or_trigger_snapshot(self) -> None:
        scenario, _ = get_scenario("o_equal_strength_ordering")
        reversed_inputs = TemporalInputs(
            activity=tuple(reversed(scenario.temporal_inputs.activity)),
            user_has_ever_spoken=scenario.temporal_inputs.user_has_ever_spoken,
            persona_has_ever_spoken=scenario.temporal_inputs.persona_has_ever_spoken,
            data_warnings=tuple(reversed(scenario.temporal_inputs.data_warnings)),
        )
        motivation = scenario.motivational_snapshot
        reversed_motivation = MotivationalSnapshot(
            generated_at=motivation.generated_at,
            needs=tuple(reversed(motivation.needs)), goals=tuple(reversed(motivation.goals)),
            dominant_need_keys=tuple(reversed(motivation.dominant_need_keys)),
            dominant_goal_keys=tuple(reversed(motivation.dominant_goal_keys)),
            data_warnings=tuple(reversed(motivation.data_warnings)),
        )
        first = run_scenario_inputs(now=scenario.now, timezone_name=scenario.timezone,
            temporal_inputs=scenario.temporal_inputs, motivational_snapshot=motivation)
        second = run_scenario_inputs(now=scenario.now, timezone_name=scenario.timezone,
            temporal_inputs=reversed_inputs, motivational_snapshot=reversed_motivation)
        self.assertEqual(first.temporal, second.temporal)
        self.assertEqual(first.trigger, second.trigger)

    def test_persona_a_b_inputs_are_isolated(self) -> None:
        persona_a, _ = get_scenario("persona_a_isolation")
        persona_b, _ = get_scenario("persona_b_isolation")
        a = run_scenario_inputs(now=persona_a.now, timezone_name=persona_a.timezone,
            temporal_inputs=persona_a.temporal_inputs, motivational_snapshot=persona_a.motivational_snapshot)
        b = run_scenario_inputs(now=persona_b.now, timezone_name=persona_b.timezone,
            temporal_inputs=persona_b.temporal_inputs, motivational_snapshot=persona_b.motivational_snapshot)
        self.assertEqual((persona_a.persona_id, persona_b.persona_id), ("persona-a", "persona-b"))
        self.assertGreater(a.temporal.idle_duration_seconds, 10000)
        self.assertEqual(len(a.motivation.needs), 1)
        self.assertLess(b.temporal.idle_duration_seconds, 10)
        self.assertEqual(b.motivation.needs, ())
        self.assertFalse(any(item.key == "curiosity" for item in b.trigger.triggers))

    def test_current_conversation_and_global_activity_are_distinct_fixtures(self) -> None:
        global_scenario, _ = get_scenario("conversation_global_recent")
        current_scenario, _ = get_scenario("conversation_current_idle")
        global_result = run_scenario_inputs(now=global_scenario.now, timezone_name=global_scenario.timezone,
            temporal_inputs=global_scenario.temporal_inputs,
            motivational_snapshot=global_scenario.motivational_snapshot)
        current_result = run_scenario_inputs(now=current_scenario.now, timezone_name=current_scenario.timezone,
            temporal_inputs=current_scenario.temporal_inputs,
            motivational_snapshot=current_scenario.motivational_snapshot)
        self.assertIsNone(global_scenario.conversation_id)
        self.assertEqual(current_scenario.conversation_id, "conversation-current")
        self.assertLess(global_result.temporal.idle_duration_seconds, 10)
        self.assertGreater(current_result.temporal.idle_duration_seconds, 10000)
        self.assertNotEqual(
            global_scenario.temporal_inputs.activity[0].source_ref,
            current_scenario.temporal_inputs.activity[0].source_ref,
        )
        self.assertTrue(any(item.trigger_type == TriggerType.USER_ACTIVITY for item in global_result.trigger.triggers))
        self.assertFalse(any(item.trigger_type == TriggerType.USER_ACTIVITY for item in current_result.trigger.triggers))

    def test_m_system_and_n_conflict_compose_independent_read_only_scopes(self) -> None:
        m, _ = get_scenario("m_system_event_long_idle")
        n, _ = get_scenario("n_conflicting_scoped_signals")
        m_temporal = compute_temporal_context(m.temporal_inputs,
            timezone_name=m.timezone, now=m.now)
        self.assertGreater(m_temporal.idle_duration_seconds, 10000)
        self.assertEqual(len(m.trigger_overlays), 1)
        self.assertIn(TriggerType.SYSTEM_EVENT, {item.trigger_type for item in m.trigger_snapshot.triggers})
        self.assertIn(TriggerType.IDLE_TIME, {item.trigger_type for item in m.trigger_snapshot.triggers})
        self.assertIn(TriggerType.USER_ACTIVITY, {item.trigger_type for item in n.trigger_snapshot.triggers})
        self.assertIn(TriggerType.SYSTEM_EVENT, {item.trigger_type for item in n.trigger_snapshot.triggers})
        self.assertIn(TriggerType.IDLE_TIME, {item.trigger_type for item in n.trigger_snapshot.triggers})
        self.assertEqual(len(n.motivational_snapshot.needs), 1)
        self.assertEqual(len(n.motivational_snapshot.goals), 1)

    def test_scenario_expectation_is_only_a_contract_helper(self) -> None:
        expectation = ScenarioExpectation(
            expected_action_class=ActionClass.DEFER,
            allowed_action_classes=(ActionClass.DEFER, ActionClass.DO_NOT_ACT),
            required_reason_codes=(ReasonCode.RECENT_USER_ACTIVITY,),
        )
        before = serialize_scenario(get_scenario("b_user_just_spoke")[0])
        assert_scenario_contract(expectation, action_class=ActionClass.DEFER,
            reason_codes=(ReasonCode.RECENT_USER_ACTIVITY,))
        after = serialize_scenario(get_scenario("b_user_just_spoke")[0])
        self.assertEqual(before, after)
        with self.assertRaises(AssertionError):
            assert_scenario_contract(expectation, action_class=ActionClass.ACT,
                reason_codes=(ReasonCode.RECENT_USER_ACTIVITY,))
        forbidden = ScenarioExpectation(forbidden_reason_codes=(ReasonCode.LONG_IDLE,))
        with self.assertRaisesRegex(AssertionError, "forbidden_reason_codes"):
            assert_scenario_contract(forbidden, reason_codes=(ReasonCode.LONG_IDLE,))
        suppressed = ScenarioExpectation(expected_suppression_reasons=(ReasonCode.RECENT_USER_ACTIVITY,))
        with self.assertRaisesRegex(AssertionError, "suppression_reasons_mismatch"):
            assert_scenario_contract(suppressed)

    def test_scenario_metadata_tags_and_models_are_immutable(self) -> None:
        source, _ = get_scenario("a_fresh_no_activity")
        scenario = build_autonomy_scenario(
            scenario_id="metadata_example", description="fixed metadata fixture", now=source.now,
            timezone_name=source.timezone, temporal_inputs=source.temporal_inputs,
            motivational_snapshot=source.motivational_snapshot,
            metadata={"scope": "current", "fixture": "isolated"}, tags=("z", "a", "a"),
        )
        self.assertEqual(scenario.metadata, (("fixture", "isolated"), ("scope", "current")))
        self.assertEqual(scenario.tags, ("a", "z"))
        with self.assertRaises(FrozenInstanceError):
            scenario.scenario_id = "mutated"  # type: ignore[misc]

    def test_scenario_builder_has_no_database_provider_or_scheduler_dependency(self) -> None:
        source = (ROOT / "tests/autonomy/harness.py").read_text(encoding="utf-8")
        self.assertNotIn("asyncpg", source)
        self.assertNotIn("libsql", source)
        self.assertNotIn("provider", source.casefold())
        self.assertNotIn("scheduler", source.casefold())
        self.assertNotIn("sleep(", source)
        self.assertNotIn("should_act", source)
        self.assertNotIn("score_total", source)

    def test_schema_marker_stays_23_and_m7_adds_no_schema_or_migration_files(self) -> None:
        self.assertEqual(CURRENT_TURSO_BASELINE_VERSION, "23")
        status = __import__("subprocess").run(
            ["git", "status", "--short", "--untracked-files=all"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        changed = {line[3:] for line in status if len(line) > 3}
        schema_paths = {
            "app/database/migrations.py",
            "app/database/schema_contract.py",
            "db/turso/baseline_v1.sql",
        }
        self.assertTrue(changed.isdisjoint(schema_paths))
        self.assertFalse(any(path.startswith("db/migrations/") for path in changed))

    def test_catalog_expectations_use_test_contract_vocabulary(self) -> None:
        self.assertEqual(
            {item.value for item in ReasonCode},
            {
                "RECENT_USER_ACTIVITY", "INSUFFICIENT_IDLE", "LONG_IDLE", "STRONG_NEED",
                "STALE_GOAL", "DEADLINE_PRESSURE", "SYSTEM_EVENT", "NO_DURABLE_ACTIVITY",
                "CONFLICTING_SIGNALS", "NO_ACTIONABLE_MOTIVATION",
            },
        )
        self.assertEqual(get_scenario("e_long_idle_5h")[1].allowed_action_classes,
            (ActionClass.ACT, ActionClass.DEFER))
        self.assertIn(ReasonCode.LONG_IDLE, get_scenario("e_long_idle_5h")[1].required_reason_codes)
        self.assertIn(ReasonCode.NO_DURABLE_ACTIVITY, get_scenario("a_fresh_no_activity")[1].required_reason_codes)
        self.assertEqual(set(get_scenario("k_goal_overdue")[1].allowed_action_classes),
            {ActionClass.DO_NOT_ACT, ActionClass.DEFER})


class TemporalTriggerBoundaryCatalogTests(unittest.TestCase):
    def test_idle_boundaries_and_pressure_saturation(self) -> None:
        cases = (("r_idle_2m_below", "active"), ("r_idle_2m_at", "recent"), ("r_idle_2m_above", "recent"),
                 ("s_idle_15m_below", "recent"), ("s_idle_15m_at", "idle"), ("s_idle_15m_above", "idle"),
                 ("t_idle_2h_below", "idle"), ("t_idle_2h_at", "long_idle"), ("t_idle_2h_above", "long_idle"))
        for scenario_id, expected in cases:
            with self.subTest(scenario=scenario_id):
                scenario, _ = get_scenario(scenario_id)
                result = compute_temporal_context(scenario.temporal_inputs,
                    timezone_name=scenario.timezone, now=scenario.now)
                self.assertEqual(result.idle_level, expected)
        for scenario_id in ("f_very_long_idle_24h", "u_idle_24h_saturation"):
            self.assertEqual(get_scenario(scenario_id)[0].trigger_snapshot.generated_at, FIXED_NOW)
            temporal = compute_temporal_context(get_scenario(scenario_id)[0].temporal_inputs,
                timezone_name=DEFAULT_TIMEZONE, now=FIXED_NOW)
            self.assertEqual(temporal.idle_pressure, 1.0)

    def test_daypart_edges(self) -> None:
        expected = {
            "v_daypart_045959": "late_night", "v_daypart_050000": "morning",
            "v_daypart_115959": "morning", "v_daypart_120000": "afternoon",
            "v_daypart_165959": "afternoon", "v_daypart_170000": "evening",
            "v_daypart_205959": "evening", "v_daypart_210000": "night",
        }
        for scenario_id, daypart in expected.items():
            scenario, _ = get_scenario(scenario_id)
            temporal = compute_temporal_context(scenario.temporal_inputs,
                timezone_name=scenario.timezone, now=scenario.now)
            self.assertEqual(temporal.daypart, daypart, scenario_id)

    def test_need_persistence_extremes_and_freshness_are_deterministic(self) -> None:
        zero, _ = get_scenario("w_need_persistence_zero")
        one, _ = get_scenario("w_need_persistence_one")
        zero_trigger = next(item for item in zero.trigger_snapshot.triggers if item.trigger_type == TriggerType.NEED_ACTIVATION)
        one_trigger = next(item for item in one.trigger_snapshot.triggers if item.trigger_type == TriggerType.NEED_ACTIVATION)
        self.assertEqual(zero.motivational_snapshot.needs[0].persistence, 0.0)
        self.assertEqual(one.motivational_snapshot.needs[0].persistence, 1.0)
        self.assertLess(zero_trigger.freshness, one_trigger.freshness)

    def test_goal_deadline_horizon_cases(self) -> None:
        for scenario_id, has_deadline in (
            ("x_goal_deadline_beyond_7d", False), ("x_goal_deadline_at_7d", False),
            ("x_goal_deadline_1d", True), ("x_goal_deadline_now", True),
            ("x_goal_deadline_overdue", True),
        ):
            scenario, _ = get_scenario(scenario_id)
            present = any(item.trigger_type == TriggerType.GOAL_DEADLINE for item in scenario.trigger_snapshot.triggers)
            self.assertEqual(present, has_deadline, scenario_id)

    def test_user_and_system_transient_threshold_boundaries(self) -> None:
        user_at, _ = get_scenario("y_user_trigger_at_epsilon")
        user_above, _ = get_scenario("y_user_trigger_above_epsilon")
        system_at, _ = get_scenario("y_system_trigger_at_epsilon")
        system_above, _ = get_scenario("y_system_trigger_above_epsilon")
        for scenario in (user_at, system_at):
            self.assertFalse(any(item.trigger_type in (TriggerType.USER_ACTIVITY, TriggerType.SYSTEM_EVENT)
                for item in scenario.trigger_snapshot.triggers))
        for scenario, trigger_type in ((user_above, TriggerType.USER_ACTIVITY), (system_above, TriggerType.SYSTEM_EVENT)):
            signal = next(item for item in scenario.trigger_snapshot.triggers if item.trigger_type == trigger_type)
            self.assertGreater(signal.strength, 0.001)
        self.assertGreater(USER_ACTIVITY_FRESHNESS_HALF_LIFE_SECONDS, 0)
        self.assertGreater(SYSTEM_EVENT_FRESHNESS_HALF_LIFE_SECONDS, 0)

    def test_duplicate_trigger_coalescing_is_order_independent_and_preserves_refs(self) -> None:
        scenario, _ = get_scenario("z_duplicate_key_coalescing")
        first = run_scenario_inputs(now=scenario.now, timezone_name=scenario.timezone,
            temporal_inputs=scenario.temporal_inputs,
            motivational_snapshot=scenario.motivational_snapshot)
        motivation = scenario.motivational_snapshot
        reversed_motivation = MotivationalSnapshot(motivation.generated_at,
            needs=tuple(reversed(motivation.needs)), goals=motivation.goals)
        second = run_scenario_inputs(now=scenario.now, timezone_name=scenario.timezone,
            temporal_inputs=scenario.temporal_inputs, motivational_snapshot=reversed_motivation)
        first_need = [item for item in first.trigger.triggers if item.trigger_type == TriggerType.NEED_ACTIVATION]
        second_need = [item for item in second.trigger.triggers if item.trigger_type == TriggerType.NEED_ACTIVATION]
        self.assertEqual(first_need, second_need)
        self.assertEqual(len(first_need), 1)
        self.assertTrue(first_need[0].source_refs)


class _LegacyRowsConnection:
    async def fetch(self, query: str, *_args):
        if "initiator_actor" in query:
            return []
        return [
            {"id": "legacy-user", "role": "user", "created_at": "2026-09-23T11:00:00"},
            {"id": "legacy-diana", "role": "diana", "created_at": "2026-09-23T11:01:00"},
            {"id": "legacy-assistant", "role": "assistant", "created_at": "2026-09-23T11:02:00"},
        ]

    async def fetchrow(self, _query: str, *_args):
        return {"user_spoke": True, "persona_spoke": True}


class _LegacyRowsPool:
    @asynccontextmanager
    async def acquire(self):
        yield _LegacyRowsConnection()


class LegacyCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_roles_and_naive_timestamps_follow_m3_utc_mapping(self) -> None:
        inputs = await load_temporal_inputs(_LegacyRowsPool())  # type: ignore[arg-type]
        roles = {item.source_ref: item.actor for item in inputs.activity}
        self.assertEqual(roles, {
            "legacy-user": "user", "legacy-diana": "persona", "legacy-assistant": "persona",
        })
        self.assertTrue(all(item.occurred_at.tzinfo == timezone.utc for item in inputs.activity))
        self.assertIn("legacy_naive_timestamp_assumed_utc:message_created_at", inputs.data_warnings)
        self.assertTrue(inputs.user_has_ever_spoken)
        self.assertTrue(inputs.persona_has_ever_spoken)


if __name__ == "__main__":
    unittest.main()
