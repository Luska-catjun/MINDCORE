"""M4/M5 scenario-to-M6 integration contract."""
from __future__ import annotations

import unittest

from app.models.autonomy_decision import ActionClass
from app.services.mindcore.autonomy_decision import decide_autonomy
from app.services.mindcore.autonomy_intention import derive_autonomy_intention
from app.services.mindcore.temporal_context import compute_temporal_context
from tests.autonomy.scenarios import SCENARIO_CATALOG


class M5M6ScenarioIntegrationTests(unittest.TestCase):
    def test_all_fifty_one_scenarios_preserve_m5_authority(self) -> None:
        for scenario, _ in SCENARIO_CATALOG:
            temporal = compute_temporal_context(
                scenario.temporal_inputs, timezone_name=scenario.timezone, now=scenario.now,
            )
            decision = decide_autonomy(scenario.motivational_snapshot, temporal, scenario.trigger_snapshot)
            intention = derive_autonomy_intention(
                decision, scenario.motivational_snapshot, temporal, scenario.trigger_snapshot,
            )
            with self.subTest(scenario=scenario.scenario_id):
                if decision.action_class == ActionClass.ACT:
                    self.assertIsNotNone(intention)
                else:
                    self.assertIsNone(intention)


if __name__ == "__main__":
    unittest.main()
