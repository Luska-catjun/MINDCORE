"""M4 golden contracts exercised against the production M5 decision engine."""
from __future__ import annotations

import unittest

from app.services.mindcore.autonomy_decision import decide_autonomy
from tests.autonomy.harness import ActionClass, ReasonCode, assert_scenario_contract
from tests.autonomy.scenarios import SCENARIO_CATALOG
from app.services.mindcore.temporal_context import compute_temporal_context


class ProductionAutonomyContractTests(unittest.TestCase):
    def test_all_m4_scenarios_match_action_reason_and_suppression_contracts(self) -> None:
        for scenario, expectation in SCENARIO_CATALOG:
            with self.subTest(scenario=scenario.scenario_id):
                temporal = compute_temporal_context(
                    scenario.temporal_inputs,
                    timezone_name=scenario.timezone,
                    now=scenario.now,
                )
                result = decide_autonomy(scenario.motivational_snapshot, temporal, scenario.trigger_snapshot)
                assert_scenario_contract(
                    expectation,
                    action_class=ActionClass(result.action_class),
                    reason_codes=tuple(ReasonCode(item) for item in result.reason_codes),
                    suppression_reasons=tuple(ReasonCode(item) for item in result.suppression_reasons),
                )


if __name__ == "__main__":
    unittest.main()
