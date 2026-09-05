from __future__ import annotations

import unittest
from uuid import uuid4

from app.services.chat_turn_coordinator import _Latency


class ChatStageDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_stage_logs_start_end_and_preserves_result(self) -> None:
        latency = _Latency(uuid4())

        async def operation():
            return "unchanged"

        with self.assertLogs("diana.chat", level="INFO") as captured:
            self.assertEqual(await latency.measure("working_memory", operation()), "unchanged")

        self.assertTrue(any("CHAT_STAGE_START" in line and "stage=working_memory" in line for line in captured.output))
        self.assertTrue(any("CHAT_STAGE_END" in line and "stage=working_memory" in line and "latency_ms=" in line for line in captured.output))

    async def test_failed_stage_logs_end_before_rethrowing(self) -> None:
        latency = _Latency(uuid4())

        async def operation():
            raise RuntimeError("expected")

        with self.assertLogs("diana.chat", level="INFO") as captured:
            with self.assertRaisesRegex(RuntimeError, "expected"):
                await latency.measure("goals_needs", operation())

        self.assertTrue(any("CHAT_STAGE_START" in line and "stage=goals_needs" in line for line in captured.output))
        self.assertTrue(any("CHAT_STAGE_END" in line and "stage=goals_needs" in line for line in captured.output))

