from __future__ import annotations

import unittest

from app.services.startup_timing import emit_startup_timing


class StartupTimingTests(unittest.TestCase):
    def test_only_fixed_safe_labels_and_duration_are_logged(self) -> None:
        with self.assertLogs("diana.startup", level="INFO") as captured:
            emit_startup_timing("database_connect", "pool_create", 17)
            emit_startup_timing("database_connect", "token-secret", 999)
            emit_startup_timing("credential-secret", "pool_create", 999)
        self.assertEqual(
            captured.output,
            [
                "INFO:diana.startup:MINDCORE_STARTUP_TIMING "
                "phase=database_connect operation=pool_create elapsed_ms=17"
            ],
        )


if __name__ == "__main__":
    unittest.main()
