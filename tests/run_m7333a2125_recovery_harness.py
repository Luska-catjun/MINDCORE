"""Run the Desktop+Android M7.3.3-A.2.1.2.5 acceptance harness locally.

Example arguments must identify only the synthetic schema-24 testbed and an
Android debug test device. The harness never pushes, tags, or releases.
"""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import subprocess
import threading

from tests.m7333a2125_recovery_harness import (
    CrossDeviceBarrier,
    LocalHranaMetadataProxy,
    SqldLifecycle,
    invoke_barrier,
    run_desktop_recovery_once,
)


def _unused_port() -> int:
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _environment(android_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    toolchain = android_root.parent / ".toolchain"
    jdks = sorted(toolchain.glob("jdk-*/Contents/Home"))
    if not jdks:
        raise RuntimeError("BUNDLED_JDK_17_NOT_FOUND")
    env.update({
        "JAVA_HOME": str(jdks[-1]),
        "ANDROID_HOME": str(toolchain / "sdk"),
        "ANDROID_SDK_ROOT": str(toolchain / "sdk"),
        "GRADLE_USER_HOME": str(toolchain / "gradle-home"),
    })
    return env


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqld", required=True)
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--android-root", required=True,
                        help="Android repository's nested android/ Gradle root")
    parser.add_argument("--adb", required=True)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--persona-id", default="7f6fa614-3df8-4a78-97a2-31e4a2f7c850")
    parser.add_argument("--desktop-device-id", default="98e27b8c-071a-4bf2-8f29-8417178b80a7")
    parser.add_argument("--android-device-id", default="f789aed2-5761-44b4-a076-532a45bacf2c")
    parser.add_argument("--auth-token", default="synthetic-local-test-token")
    parser.add_argument("--gradle-timeout", type=int, default=900)
    args = parser.parse_args()

    android_root = Path(args.android_root).resolve()
    adb = str(Path(args.adb).resolve())
    lifecycle = SqldLifecycle(args.sqld, args.db_path, port=_unused_port())
    proxy = None
    control = None
    barrier_thread: threading.Thread | None = None
    proxy_port: int | None = None
    try:
        lifecycle.start()
        proxy = LocalHranaMetadataProxy(lifecycle.url)
        proxy.start()
        control = CrossDeviceBarrier(sqld=lifecycle)
        control.start()

        desktop = asyncio.run(run_desktop_recovery_once(
            lifecycle.url, args.auth_token, args.persona_id))
        if desktop.persona_id != args.persona_id or desktop.schema_version != 24:
            raise RuntimeError("DESKTOP_SHARED_PERSONA_RECOVERY_FAILED")
        print(f"DESKTOP_RECOVERY_CALLABLE=PASS schema={desktop.schema_version} "
              f"persona={desktop.persona_id} recovered={desktop.recovered_turns}")

        _, control_port = control.address
        barrier_url = f"http://127.0.0.1:{control_port}/arrive?participant=android"
        desktop_barrier: dict[str, object] = {}

        def desktop_arrive() -> None:
            try:
                desktop_barrier.update(invoke_barrier(control.address, "desktop"))
            except Exception as error:
                desktop_barrier["error_type"] = type(error).__name__

        barrier_thread = threading.Thread(target=desktop_arrive,
                                          name="m7333a2125-desktop-barrier", daemon=True)
        barrier_thread.start()
        proxy_port = proxy.address[1]
        for port in (proxy_port, control_port):
            reverse = subprocess.run(
                [adb, "-s", args.serial, "reverse", f"tcp:{port}", f"tcp:{port}"],
                check=False, capture_output=True, text=True, timeout=15)
            if reverse.returncode:
                raise RuntimeError("ADB_REVERSE_FAILED: " + reverse.stderr[-400:])

        properties = [
            "m7333a2125_acceptance=true",
            "m7333a2125_outage=true",
            "m7333a2125_barrier=true",
            f"m7333a2125_db_url=http://127.0.0.1:{proxy_port}",
            f"m7333a2125_control_url=http://127.0.0.1:{control_port}",
            f"m7333a2125_barrier_url={barrier_url}",
            "class=com.luskacat.mindcore.M7333A2125RecoveryHarnessTest",
        ]
        command = ["./gradlew", "connectedDebugAndroidTest", *(
            "-Pandroid.testInstrumentationRunnerArguments." + value for value in properties)]
        completed = subprocess.run(command, cwd=android_root, env=_environment(android_root),
                                   capture_output=True, text=True,
                                   timeout=args.gradle_timeout)
        print(completed.stdout[-12000:])
        if completed.returncode:
            print(completed.stderr[-6000:])
            raise RuntimeError("ANDROID_RECOVERY_HARNESS_TESTS_FAILED")
        barrier_thread.join(timeout=30)
        if barrier_thread.is_alive() or not desktop_barrier.get("released"):
            raise RuntimeError("CROSS_DEVICE_BARRIER_NOT_RELEASED")
        if not control.wait_released(0):
            raise RuntimeError("BARRIER_RELEASE_WAS_NOT_SHARED")
        print("CROSS_DEVICE_BARRIER=PASS participants=android,desktop")
        return 0
    finally:
        if control is not None:
            control.close()
        if proxy is not None:
            proxy.close()
        lifecycle.close()
        if proxy_port is not None:
            for port in (proxy_port, control.address[1] if control is not None else None):
                if port is not None:
                    subprocess.run([adb, "-s", args.serial, "reverse", "--remove",
                                    f"tcp:{port}"], check=False, capture_output=True,
                                   text=True, timeout=15)


if __name__ == "__main__":
    raise SystemExit(main())
