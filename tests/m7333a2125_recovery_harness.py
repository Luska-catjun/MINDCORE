"""Test-only cross-device recovery harness primitives for M7.3.3-A.2.1.2.5.

This module is deliberately under tests and is not imported by the Desktop app.
The runtime callable uses the same schema-24 ``TursoPool`` and production
``recover_incomplete_turns`` entry point as Desktop startup.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import socket
import subprocess
import threading
import time
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.database.migrations import SCHEMA_VERSION_KEY
from app.database.persona_storage import PERSONA_ID_KEY
from app.database.turso import TursoPool
from app.services.turn_durability import TurnDurability
from app.services.turn_recovery import recover_incomplete_turns


@dataclass(frozen=True)
class RecoveryResult:
    persona_id: str
    schema_version: int
    recovered_turns: int
    cognition_observations: dict[str, int] | None = None
    settled_provider_turns: int = 0


async def run_desktop_recovery_once(database_url: str, auth_token: str,
                                    persona_id: str,
                                    observer: "CognitionExecutionObserver | None" = None,
                                    interrupted_provider_turns: dict[str, str] | None = None
                                    ) -> RecoveryResult:
    """Validate the shared owner, then invoke the real Desktop startup recovery."""
    pool = TursoPool(database_url, auth_token)
    try:
        async with pool.acquire() as connection:
            schema = await connection.fetchval(
                "select value from schema_metadata where key=$1", SCHEMA_VERSION_KEY)
            owner = await connection.fetchval(
                "select value from schema_metadata where key=$1", PERSONA_ID_KEY)
            if owner is None:
                owner = await connection.fetchval(
                    "select value from schema_metadata where key='android_persona_id'")
        if int(schema) != 24:
            raise RuntimeError("HARNESS_SCHEMA_MUST_BE_24")
        if str(owner) != str(persona_id):
            raise RuntimeError("HARNESS_PERSONA_MISMATCH")
        candidate_ids = list((interrupted_provider_turns or {}).keys())
        async with pool.acquire() as connection:
            before_rows = await connection.fetch(
                "select turn_id,status from chat_turns where turn_id in (" +
                ",".join(f"${index + 1}" for index in range(len(candidate_ids))) + ")",
                *candidate_ids) if candidate_ids else []
        before = {str(row["turn_id"]): str(row["status"]) for row in before_rows}
        if observer is None:
            count = await recover_incomplete_turns(
                pool, interrupted_provider_turns=interrupted_provider_turns,
                recovery_device_id="98e27b8c-071a-4bf2-8f29-8417178b80a7")
        else:
            original = TurnDurability.run_stage

            async def observed_run_stage(durability, turn_id, stage_name, operation,
                                        *, transactional=True):
                async def observed_operation(stage_pool):
                    observer.record("stage_claim_count")
                    observer.record("stage_execute_count")
                    return await operation(stage_pool)
                try:
                    result = await original(
                        durability, turn_id, stage_name, observed_operation,
                        transactional=transactional)
                except BaseException:
                    raise
                else:
                    observer.record("stage_complete_count")
                    return result

            from unittest.mock import patch
            with patch.object(TurnDurability, "run_stage", observed_run_stage):
                count = await recover_incomplete_turns(
                    pool, interrupted_provider_turns=interrupted_provider_turns,
                    recovery_device_id="98e27b8c-071a-4bf2-8f29-8417178b80a7")
        async with pool.acquire() as connection:
            after_rows = await connection.fetch(
                "select turn_id,status from chat_turns where turn_id in (" +
                ",".join(f"${index + 1}" for index in range(len(candidate_ids))) + ")",
                *candidate_ids) if candidate_ids else []
        after = {str(row["turn_id"]): str(row["status"]) for row in after_rows}
        settled = sum(before.get(turn_id) == "pending" and after.get(turn_id) == "core_failed"
                      for turn_id in candidate_ids)
        return RecoveryResult(str(owner), int(schema), int(count),
                              observer.snapshot() if observer is not None else None, settled)
    finally:
        await pool.close()


class SyntheticProviderObserver:
    """Deterministic callback recorder for test-injected provider functions."""
    def __init__(self, response: str = "Synthetic harness reply") -> None:
        self.response = response
        self._calls: list[dict[str, str]] = []
        self._lock = threading.Lock()

    def invoke(self, turn_id: str, origin_device_id: str) -> str:
        if not turn_id or not origin_device_id:
            raise ValueError("PROVIDER_OBSERVER_IDENTITY_REQUIRED")
        with self._lock:
            self._calls.append({"turn_id": str(turn_id),
                                "origin_device_id": str(origin_device_id)})
        return self.response

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            calls = list(self._calls)
        return {"provider_invocation_count": len(calls), "provider_call_ids": calls}


class CognitionExecutionObserver:
    """Thread-safe test observer used around real stage owner callbacks."""
    def __init__(self) -> None:
        self._counts = {name: 0 for name in (
            "stage_claim_count", "stage_execute_count", "stage_complete_count",
            "memory_mutation_count", "relationship_mutation_count",
            "additional_cognition_mutation_count")}
        self._lock = threading.Lock()

    def record(self, name: str, amount: int = 1) -> None:
        if name not in self._counts or amount < 0:
            raise ValueError("COGNITION_OBSERVER_EVENT_INVALID")
        with self._lock:
            self._counts[name] += amount

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)


class CrossDeviceBarrier:
    """HTTP barrier and optional sqld controller for Android instrumentation."""
    def __init__(self, host: str = "127.0.0.1", port: int = 0,
                 sqld: "SqldLifecycle | None" = None) -> None:
        gate = threading.Condition()
        participants: set[str] = set()
        released = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                route, _, query = self.path.partition("?")
                if route in {"/stop-db", "/start-db"} and sqld is not None:
                    try:
                        if route == "/stop-db":
                            sqld.stop()
                            result = {"stopped": sqld.unavailable()}
                        else:
                            sqld.start()
                            result = {"started": not sqld.unavailable(),
                                      "database_path": sqld.database_path}
                        status = 200 if all(result.values()) else 500
                    except Exception as error:
                        result = {"error_type": type(error).__name__}
                        status = 500
                    body = json.dumps(result).encode()
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                name = query.partition("=")[2]
                if name not in {"desktop", "android"}:
                    self.send_error(400, "invalid participant")
                    return
                with gate:
                    participants.add(name)
                    if participants == {"desktop", "android"}:
                        released.set()
                        gate.notify_all()
                    else:
                        gate.wait_for(released.is_set, timeout=20)
                status = 200 if released.is_set() else 408
                body = json.dumps({"released": released.is_set(),
                                   "participants": sorted(participants)}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self._server = ThreadingHTTPServer((host, port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        name="m7333a2125-barrier", daemon=True)
        self._released = released
        self._participants = participants

    @property
    def address(self) -> tuple[str, int]:
        host, port = self._server.server_address
        return (host, port)

    def start(self) -> None:
        self._thread.start()

    def wait_released(self, timeout: float = 20.0) -> bool:
        return self._released.wait(timeout)

    def participants(self) -> list[str]:
        return sorted(self._participants)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread.is_alive():
            self._thread.join(timeout=2)


class SqldLifecycle:
    """Start/stop/restart the same explicit synthetic sqld storage directory."""
    def __init__(self, executable: str, database_path: str,
                 host: str = "127.0.0.1", port: int = 33075,
                 log_path: str | None = None) -> None:
        self.executable = str(Path(executable).resolve())
        self.database_path = str(Path(database_path).resolve())
        self.host, self.port = host, int(port)
        self.log_path = log_path
        self.process: subprocess.Popen[bytes] | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self, timeout: float = 20.0) -> None:
        if self.process and self.process.poll() is None:
            raise RuntimeError("SQLD_ALREADY_RUNNING")
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        if self.log_path:
            with open(self.log_path, "ab") as output:
                self.process = subprocess.Popen(
                    [self.executable, "-d", self.database_path,
                     "--http-listen-addr", f"{self.host}:{self.port}",
                     "--http-self-url", self.url],
                    stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                )
        else:
            self.process = subprocess.Popen(
                [self.executable, "-d", self.database_path,
                 "--http-listen-addr", f"{self.host}:{self.port}",
                 "--http-self-url", self.url],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
            )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("SQLD_EXITED_BEFORE_READY")
            try:
                with socket.create_connection((self.host, self.port), timeout=0.25):
                    return
            except OSError:
                pass
            time.sleep(0.05)
        self.stop()
        raise TimeoutError("SQLD_READINESS_TIMEOUT")

    def stop(self, timeout: float = 10.0) -> None:
        process = self.process
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)

    def restart(self, timeout: float = 20.0) -> None:
        self.stop()
        self.start(timeout)

    def unavailable(self) -> bool:
        try:
            with socket.create_connection((self.host, self.port), timeout=0.25):
                return False
        except OSError:
            return True

    def close(self) -> None:
        self.stop()


class LocalHranaMetadataProxy:
    """Forward Hrana to real loopback sqld and strip its HTTP affinity URL only."""
    def __init__(self, upstream_url: str, host: str = "127.0.0.1", port: int = 0,
                 error_observer=None, request_observer=None) -> None:
        from urllib.parse import urlsplit
        parsed = urlsplit(upstream_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("TEST_PROXY_UPSTREAM_MUST_BE_LOOPBACK_HTTP")
        upstream = upstream_url.rstrip("/")

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                received = time.monotonic()
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                request = Request(upstream + self.path, data=body, method="POST",
                                  headers={"Content-Type": self.headers.get(
                                      "Content-Type", "application/json")})
                try:
                    with urlopen(request, timeout=25) as response:
                        status = response.status
                        content_type = response.headers.get("Content-Type", "application/json")
                        payload = response.read()
                    decoded = json.loads(payload)
                    if error_observer is not None and isinstance(decoded, dict):
                        for item in decoded.get("results", []):
                            if isinstance(item, dict) and item.get("type") == "error":
                                error_observer({"upstream_error": item.get("error")})
                    if isinstance(decoded, dict):
                        decoded.pop("base_url", None)
                    payload = json.dumps(decoded, separators=(",", ":")).encode()
                except Exception as error:
                    if error_observer is not None:
                        error_observer({"proxy_error_type": type(error).__name__})
                    status, content_type = 502, "application/json"
                    payload = json.dumps({"proxy_error_type": type(error).__name__}).encode()
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                if request_observer is not None:
                    statements = json.loads(body).get("requests", [])
                    request_observer({
                        "received": received, "completed": time.monotonic(),
                        "elapsed_ms": round((time.monotonic() - received) * 1000, 3),
                        "sql_head": [str(item.get("stmt", {}).get("sql", ""))[:90]
                                     for item in statements],
                        "http_status": status,
                    })

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self._server = ThreadingHTTPServer((host, port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        name="m7333a2125-hrana-proxy", daemon=True)

    @property
    def address(self) -> tuple[str, int]:
        host, port = self._server.server_address
        return (host, port)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread.is_alive():
            self._thread.join(timeout=2)


async def inspect_durable_turn(database_url: str, auth_token: str,
                              persona_id: str, turn_id: str) -> dict[str, Any]:
    """Read a turn ledger plus stable Memory, Relationship and Episode counts."""
    pool = TursoPool(database_url, auth_token)
    try:
        async with pool.acquire() as connection:
            owner = await connection.fetchval(
                "select value from schema_metadata where key=$1", PERSONA_ID_KEY)
            if owner is None:
                owner = await connection.fetchval(
                    "select value from schema_metadata where key='android_persona_id'")
            if str(owner) != str(persona_id):
                raise RuntimeError("HARNESS_PERSONA_MISMATCH")
            turn = await connection.fetchrow(
                "select turn_id,conversation_id,user_message_id,assistant_message_id,status "
                "from chat_turns where turn_id=$1", turn_id)
            if turn is None:
                return {"persona_id": str(owner), "turn": None, "stages": []}
            stages = await connection.fetch(
                "select stage_name,status,attempt_count,completed_at from chat_turn_stages "
                "where turn_id=$1 order by stage_name", turn_id)
            user_id = turn["user_message_id"]
            memory = await connection.fetchval(
                "select count(*) from memories where source_message_id=$1", user_id)
            experience = await connection.fetchval(
                "select experience_id from experiences where user_message_id=$1 limit 1", user_id)
            relationship = (await connection.fetchval(
                "select count(*) from relationship_log where source_experience_id=$1",
                experience) if experience is not None else 0)
            episodes = await connection.fetchval(
                "select count(*) from episodes where user_message_id=$1", user_id)
        return {"persona_id": str(owner), "turn": dict(turn),
                "stages": [dict(row) for row in stages],
                "memory_mutation_count": int(memory or 0),
                "relationship_mutation_count": int(relationship or 0),
                "additional_cognition_mutation_count": int(episodes or 0)}
    finally:
        await pool.close()


def invoke_barrier(address: tuple[str, int], participant: str,
                   timeout: float = 25.0) -> dict[str, Any]:
    if participant not in {"desktop", "android"}:
        raise ValueError("BARRIER_PARTICIPANT_INVALID")
    request = (f"http://{address[0]}:{address[1]}/arrive?participant={participant}")
    from urllib.request import Request
    with urlopen(Request(request, data=b"", method="POST"), timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError("CROSS_DEVICE_BARRIER_NOT_RELEASED")
        return json.loads(response.read())
