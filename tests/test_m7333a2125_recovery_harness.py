from __future__ import annotations

from pathlib import Path
import os
import sys
import tempfile
import threading
import unittest
from urllib.request import urlopen

from tests.m7333a2125_recovery_harness import (
    CognitionExecutionObserver,
    CrossDeviceBarrier,
    SqldLifecycle,
    SyntheticProviderObserver,
    invoke_barrier,
)


class RecoveryHarnessPrimitiveTests(unittest.TestCase):
    def test_synthetic_provider_records_call_turn_and_origin(self) -> None:
        provider = SyntheticProviderObserver("deterministic response")
        self.assertEqual("deterministic response", provider.invoke("turn-1", "device-d"))
        self.assertEqual({
            "provider_invocation_count": 1,
            "provider_call_ids": [{"turn_id": "turn-1", "origin_device_id": "device-d"}],
        }, provider.snapshot())

    def test_cognition_observer_counts_all_required_events(self) -> None:
        observer = CognitionExecutionObserver()
        for name in ("stage_claim_count", "stage_execute_count", "stage_complete_count",
                     "memory_mutation_count", "relationship_mutation_count",
                     "additional_cognition_mutation_count"):
            observer.record(name)
        self.assertEqual(6, sum(observer.snapshot().values()))

    def test_cross_device_barrier_requires_both_participants(self) -> None:
        barrier = CrossDeviceBarrier()
        barrier.start()
        result: dict[str, object] = {}
        desktop = threading.Thread(target=lambda: result.update(
            {"desktop": invoke_barrier(barrier.address, "desktop")}))
        android = threading.Thread(target=lambda: result.update(
            {"android": invoke_barrier(barrier.address, "android")}))
        try:
            desktop.start()
            self.assertFalse(barrier.wait_released(0.05))
            android.start()
            desktop.join(3)
            android.join(3)
            self.assertFalse(desktop.is_alive())
            self.assertFalse(android.is_alive())
            self.assertTrue(barrier.wait_released(0))
            self.assertEqual(["android", "desktop"], barrier.participants())
            self.assertTrue(result["desktop"]["released"])
            self.assertTrue(result["android"]["released"])
        finally:
            barrier.close()

    @unittest.skipIf(
        sys.platform == "win32",
        "Native sqld 0.24.32 supports macOS/Linux only; no supported Windows binary/target.",
    )
    def test_sqld_controller_stops_and_restarts_same_storage(self) -> None:
        executable = Path(os.environ.get(
            "MINDCORE_TEST_SQLD",
            str(Path(__file__).resolve().parents[2] / "mindcore-android" /
                ".toolchain/libsql-server-aarch64-apple-darwin/sqld"),
        ))
        self.assertTrue(executable.is_file(), "bundled synthetic sqld executable is required")
        with tempfile.TemporaryDirectory(prefix="m7333a2125-sqld-") as directory:
            lifecycle = SqldLifecycle(str(executable), str(Path(directory) / "shared.sqld"), port=0)
            lifecycle.port = _free_port()
            try:
                lifecycle.start()
                self.assertFalse(lifecycle.unavailable())
                self.assertEqual(0, urlopen(lifecycle.url + "/v2/pipeline", timeout=2).status)
            except Exception as error:
                # A pipeline POST is required by Hrana; GET may return a valid
                # HTTP protocol error while proving the real daemon is ready.
                from urllib.error import HTTPError
                if not isinstance(error, HTTPError):
                    raise
                self.assertLess(error.code, 500)
            lifecycle.stop()
            self.assertTrue(lifecycle.unavailable())
            storage = lifecycle.database_path
            lifecycle.restart()
            self.assertEqual(storage, lifecycle.database_path)
            self.assertFalse(lifecycle.unavailable())
            lifecycle.close()


def _free_port() -> int:
    import socket
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


if __name__ == "__main__":
    unittest.main()
