"""Focused evidence driver reusing the accepted .2.5 harness, synthetic only.

This driver stops at the first unmet safety contract. It never substitutes a
host import of Android Python for instrumentation in an actual Android process.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from unittest.mock import patch
from uuid import UUID

from fastapi import BackgroundTasks
from app.config import Settings
from app.database.migrations import ensure_turso_schema_current
from app.database.persona_storage import bind_persona_identity
from app.database.turso import TursoPool
from app.models.enums import MessageRole
from app.models.turn_context import TurnContext
from app.schemas.chat import ChatRequest
from app.schemas.conversations import ConversationCreate
from app.schemas.messages import MessageCreate
from app.services import repository
from app.services.chat_turn_coordinator import ChatTurnCoordinator
from tests.m7333a2125_recovery_harness import (
    CognitionExecutionObserver, CrossDeviceBarrier, LocalHranaMetadataProxy,
    SqldLifecycle, SyntheticProviderObserver, invoke_barrier,
    run_desktop_recovery_once,
)

P = "7f6fa614-3df8-4a78-97a2-31e4a2f7c850"
D = "98e27b8c-071a-4bf2-8f29-8417178b80a7"
A = "f789aed2-5761-44b4-a076-532a45bacf2c"
TOKEN = "synthetic-local-test-token"
ROOT = Path(__file__).resolve().parents[1]


def port():
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        return connection.getsockname()[1]


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")


async def seed(url):
    pool = TursoPool(url, TOKEN)
    try:
        async with pool.acquire() as connection:
            await ensure_turso_schema_current(connection, target_version=24)
            await bind_persona_identity(connection, P)
        conversation = await repository.create_conversation(
            pool, ConversationCreate(source_device=D))
        return str(conversation["id"])
    finally:
        await pool.close()


async def desktop_turn(url, conversation, evidence, *, checkpoint=None, content=None):
    pool = TursoPool(url, TOKEN)
    provider = SyntheticProviderObserver("Synthetic persisted Desktop reply")
    settings = Settings(_env_file=None, persona_id=P, diana_timezone="UTC",
                        llm_fallback_provider=None)
    text = content or ("M7333A2126_T_D provider boundary" if checkpoint else
            "Desktop-origin product message: 지난주 친구랑 observatory에 다녀왔어. 같이 해줘서 고마워.")

    async def generate(*_args, **_kwargs):
        async with pool.acquire() as connection:
            turn = await connection.fetchrow(
                "select t.* from chat_turns t join messages m on m.id=t.user_message_id "
                "where m.content=$1 order by t.created_at desc limit 1", text)
            stages = await connection.fetch(
                "select * from chat_turn_stages where turn_id=$1 order by stage_name",
                turn["turn_id"])
        marker = next((stage for stage in stages if stage["stage_name"] == "provider_generate"), None)
        evidence_row = {"turn": dict(turn), "stages": stages,
                        "provider_started_visible_before_invocation": bool(marker and marker["status"] == "running"),
                        "provider_invocation_count": 0, "provider_call_ids": []}
        if checkpoint == "B":
            write(evidence / f"checkpoint-{checkpoint}-{turn['turn_id']}.json", evidence_row)
            os._exit(93)
        invocation = {"turn_id": str(turn["turn_id"]), "origin_device_id": D,
                      "content": text, "checkpoint": checkpoint}
        with (evidence / "provider-invocations.jsonl").open("a", encoding="utf-8") as calls:
            calls.write(json.dumps(invocation) + "\n")
        reply = provider.invoke(str(turn["turn_id"]), D)
        evidence_row.update(provider.snapshot())
        if checkpoint:
            # Real process interruption at the provider boundary; no durability
            # row is manufactured and no failure handler is mocked.
            write(evidence / f"checkpoint-{checkpoint}-{turn['turn_id']}.json", evidence_row)
            os._exit(91)
        write(evidence / "desktop-provider-smoke.json", evidence_row)
        return reply

    async def memory(_settings, user_content, _diana_content):
        return json.dumps({"should_store": True, "memory": user_content,
            "memory_type": "shared_event", "importance": 0.72})

    try:
        background = BackgroundTasks()
        with patch("app.services.chat_turn_coordinator.generate_reply", generate), \
                patch("app.services.memory_service.generate_memory_candidate", memory):
            result = await ChatTurnCoordinator(pool=pool).execute(
                payload=ChatRequest(conversation_id=UUID(conversation),
                    role=MessageRole.user, content=text, source_device=D),
                turn_context=TurnContext.user_text(), background_tasks=background,
                settings=settings, identity_prompt="Synthetic acceptance Persona")
            await background()
        write(evidence / "desktop-smoke-result.json", result)
    finally:
        await pool.close()


def android(adb, serial, evidence, name, klass, arguments):
    subprocess.run([adb, "-s", serial, "logcat", "-c"],
                   capture_output=True, text=True, timeout=30, check=True)
    command = [adb, "-s", serial, "shell", "am", "instrument", "-w", "-r",
               "-e", "class", "com.luskacat.mindcore." + klass]
    for key, value in arguments.items():
        command += ["-e", key, str(value)]
    command += ["com.luskacat.mindcore.android.test/androidx.test.runner.AndroidJUnitRunner"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=300)
    tagged = subprocess.run([adb, "-s", serial, "logcat", "-d", "-s",
                            "M7333A21262:I", "M7333A2126R1:I"],
                            capture_output=True, text=True, timeout=30)
    combined = result.stdout + result.stderr + tagged.stdout + tagged.stderr
    (evidence / (name + ".log")).write_text(combined)
    print(name, combined[-1800:], flush=True)
    if (result.returncode or "OK (" not in result.stdout or "FAILURES!!!" in result.stdout
            or "INSTRUMENTATION_STATUS_CODE: -4" in result.stdout):
        raise RuntimeError(name + "_FAILED")
    return combined


async def append_desktop(url, conversation, start):
    while int(time.time() * 1000) < start:
        await asyncio.sleep(0.01)
    pool = TursoPool(url, TOKEN)
    try:
        for index in range(10):
            await repository.create_message(pool, MessageCreate(
                conversation_id=UUID(conversation), role=MessageRole.user,
                content=f"M7333A2122_CROSS_D_10_{index:03d}", source_device=D))
    finally:
        await pool.close()


async def inspect(url, conversation):
    pool = TursoPool(url, TOKEN)
    try:
        async with pool.acquire() as connection:
            rows = await connection.fetch("select * from messages where conversation_id=$1 order by sequence", conversation)
            turns = await connection.fetch("select * from chat_turns order by created_at")
            stages = await connection.fetch("select * from chat_turn_stages order by turn_id,stage_name")
            memories = await connection.fetch("select * from memories")
            relationships = await connection.fetch("select * from relationship_log")
        return dict(messages=rows, turns=turns, stages=stages,
                    memories=memories, relationships=relationships)
    finally:
        await pool.close()


async def inspect_turn_id(url, turn_id):
    pool = TursoPool(url, TOKEN)
    try:
        async with pool.acquire() as connection:
            turn = await connection.fetchrow("select * from chat_turns where turn_id=$1", turn_id)
            stages = await connection.fetch(
                "select stage_name,status,attempt_count,started_at,completed_at "
                "from chat_turn_stages where turn_id=$1 order by stage_name", turn_id)
            assistant_count = await connection.fetchval(
                "select count(*) from messages where id=(select assistant_message_id from chat_turns where turn_id=$1)",
                turn_id)
        stage = next((row for row in stages if row["stage_name"] == "provider_generate"), None)
        return {"turn": dict(turn) if turn else None, "stages": stages,
                "provider_status": stage["status"] if stage else None,
                "assistant_count": int(assistant_count or 0)}
    finally:
        await pool.close()


async def inspect_content(url, conversation, content, evidence):
    pool = TursoPool(url, TOKEN)
    try:
        async with pool.acquire() as connection:
            turn_id = await connection.fetchval(
                "select turn_id from chat_turns where conversation_id=$1 and user_message_id in "
                "(select id from messages where conversation_id=$1 and content=$2) order by created_at desc limit 1",
                conversation, content)
        durable = await inspect_turn_id(url, turn_id) if turn_id else None
        calls = []
        calls_path = evidence / "provider-invocations.jsonl"
        if calls_path.is_file():
            calls = [json.loads(line) for line in calls_path.read_text().splitlines() if line]
        return {**(durable or {}), "provider_calls": sum(call["content"] == content for call in calls)}
    finally:
        await pool.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--android-root", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--db-path")
    parser.add_argument("--serial", default="emulator-5560")
    parser.add_argument("--child-url")
    parser.add_argument("--child-conversation")
    parser.add_argument("--child-checkpoint", choices=("A", "B", "C"))
    parser.add_argument("--child-content")
    parser.add_argument("--append-url")
    parser.add_argument("--append-conversation")
    parser.add_argument("--append-start", type=int)
    parser.add_argument("--append-isolated", dest="append_isolated", action="store_true", default=True,
                        help="Run native Desktop append outside the Android HTTP proxy process (default).")
    parser.add_argument("--diagnostic-same-process", dest="append_isolated", action="store_false",
                        help="Diagnostic only: reproduce native-call starvation of the host proxy.")
    args = parser.parse_args()
    evidence = Path(args.evidence).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    if args.child_url:
        if args.child_checkpoint == "A":
            from app.services.turn_durability import TurnDurability
            async def fail_provider_marker(_durability, _turn_id):
                raise RuntimeError("injected provider_started persistence failure")
            with patch.object(TurnDurability, "mark_provider_started", fail_provider_marker):
                asyncio.run(desktop_turn(args.child_url, args.child_conversation, evidence,
                    content=args.child_content))
        elif args.child_checkpoint in {"B", "C"}:
            asyncio.run(desktop_turn(args.child_url, args.child_conversation, evidence,
                checkpoint=args.child_checkpoint, content=args.child_content))
            raise RuntimeError("INTERRUPTION_NOT_REACHED")
        else:
            asyncio.run(desktop_turn(args.child_url, args.child_conversation, evidence,
                content=args.child_content))
        raise RuntimeError("INTERRUPTION_NOT_REACHED")
    if args.append_url:
        asyncio.run(append_desktop(args.append_url, args.append_conversation, args.append_start))
        return 0
    root = Path(args.android_root).resolve()
    adb = str(root / ".toolchain/sdk/platform-tools/adb")
    lifecycle = SqldLifecycle(str(root / ".toolchain/libsql-server-aarch64-apple-darwin/sqld"),
                              args.db_path or str(evidence / "shared.sqld"), port=port(),
                              log_path=str(evidence / "sqld.log"))
    proxy = None
    control = None
    concurrent_thread = None
    concurrent_errors = []
    hrana_errors = []
    hrana_requests = []
    heartbeat = []
    heartbeat_stop = threading.Event()
    def tick():
        prior = time.monotonic()
        while not heartbeat_stop.wait(0.01):
            now = time.monotonic()
            heartbeat.append(round((now - prior) * 1000, 3))
            prior = now
    heartbeat_thread = threading.Thread(target=tick, daemon=True)
    heartbeat_thread.start()
    result = {"persona_id": P, "desktop_device": D, "android_device": A,
              "schema_version": 24, "started_at": datetime.now(timezone.utc).isoformat()}
    try:
        lifecycle.start()
        conversation = asyncio.run(seed(lifecycle.url))
        result["conversation_id"] = conversation
        result["database_path"] = lifecycle.database_path
        asyncio.run(desktop_turn(lifecycle.url, conversation, evidence))
        # The accepted restart smoke requires at least five durable messages.
        # Create both Desktop turns through the actual coordinator, not SQL.
        asyncio.run(desktop_turn(lifecycle.url, conversation, evidence))
        observer = CognitionExecutionObserver()
        result["desktop_recovery"] = asyncio.run(run_desktop_recovery_once(lifecycle.url, TOKEN, P, observer)).__dict__
        proxy = LocalHranaMetadataProxy(lifecycle.url, error_observer=hrana_errors.append,
                                        request_observer=hrana_requests.append)
        proxy.start()
        control = CrossDeviceBarrier(sqld=lifecycle)
        control.start()
        for value in (proxy.address[1], control.address[1]):
            subprocess.run([adb, "-s", args.serial, "reverse", f"tcp:{value}", f"tcp:{value}"], check=True)
        endpoint = f"http://127.0.0.1:{proxy.address[1]}"
        common = {"shared_db_url": endpoint, "shared_db_conversation_id": conversation}
        android(adb, args.serial, evidence, "shared-smoke", "ZM7333A211SharedDbTest",
                {**common, "shared_db_acceptance": "true"})
        android(adb, args.serial, evidence, "accepted-harness-recovery",
            "M7333A2125RecoveryHarnessTest#androidRecoveryEntryPointUsesSharedPersona", {
                "m7333a2125_acceptance": "true", "m7333a2125_db_url": endpoint})
        android(adb, args.serial, evidence, "accepted-harness-outage",
            "M7333A2125RecoveryHarnessTest#androidOutageMessageWriteIsCallableAndRestoresDatabase", {
                "m7333a2125_outage": "true", "m7333a2125_db_url": endpoint,
                "m7333a2125_control_url": f"http://127.0.0.1:{control.address[1]}",
                "m7333a2125_expected_db_path": lifecycle.database_path})
        barrier_result = {}
        thread = threading.Thread(target=lambda: barrier_result.update(invoke_barrier(control.address, "desktop")))
        thread.start()
        android(adb, args.serial, evidence, "accepted-harness-barrier",
            "M7333A2125RecoveryHarnessTest#androidParticipantWaitsForDesktopOnCrossDeviceBarrier", {
                "m7333a2125_barrier": "true",
                "m7333a2125_barrier_url": f"http://127.0.0.1:{control.address[1]}/arrive?participant=android"})
        thread.join(30)
        result["barrier"] = barrier_result
        if not barrier_result.get("released"):
            raise RuntimeError("BARRIER_NOT_RELEASED")
        start = int(time.time() * 1000) + 5000
        def desktop_append():
            try:
                if args.append_isolated:
                    child = subprocess.run([sys.executable, "-m", "tests.run_m7333a2126_safety_evidence",
                        "--android-root", str(root), "--evidence", str(evidence),
                        "--append-url", lifecycle.url, "--append-conversation", conversation,
                        "--append-start", str(start)], cwd=ROOT, capture_output=True,
                        text=True, timeout=90)
                    (evidence / "desktop-append.log").write_text(child.stdout + child.stderr)
                    if child.returncode:
                        raise RuntimeError("ISOLATED_APPEND_FAILED:" + str(child.returncode))
                else:
                    asyncio.run(append_desktop(lifecycle.url, conversation, start))
            except BaseException as error:
                concurrent_errors.append(repr(error))
        thread = threading.Thread(target=desktop_append)
        concurrent_thread = thread
        thread.start()
        android(adb, args.serial, evidence, "concurrent-smoke", "M7333A212ConcurrentTest", {
            **common, "shared_db_concurrency": "true", "shared_db_operations": "10",
            "shared_db_start_at_ms": start})
        thread.join(60)
        if thread.is_alive() or concurrent_errors:
            raise RuntimeError("DESKTOP_CONCURRENT_APPEND_FAILED:" + repr(concurrent_errors))
        state = asyncio.run(inspect(lifecycle.url, conversation))
        write(evidence / "shared-baseline.json", state)
        desktop = [r for r in state["messages"] if r["content"].startswith("M7333A2122_CROSS_D_10_")]
        mobile = [r for r in state["messages"] if r["content"].startswith("M7333A2122_ANDROID_010_")]
        result["concurrency"] = {"iterations": 10, "expected_desktop": 10,
            "actual_desktop": len(desktop), "expected_android": 20, "actual_android": len(mobile),
            "unique_ids": len({str(r["id"]) for r in desktop + mobile})}
        if len(desktop) != 10 or len(mobile) != 20 or result["concurrency"]["unique_ids"] != 30:
            raise RuntimeError("CONCURRENT_ROWS_MISMATCH")
        env = {key: value for key, value in os.environ.items()
               if key in {"PATH", "HOME", "LANG", "TMPDIR"}}
        marker_failure_child = subprocess.run([sys.executable, "-m", "tests.run_m7333a2126_safety_evidence",
            "--android-root", str(root), "--evidence", str(evidence), "--child-url", lifecycle.url,
            "--child-conversation", conversation, "--child-checkpoint", "A",
            "--child-content", "M7333A21261_CHECKPOINT_A"], cwd=ROOT, env=env,
            capture_output=True, text=True, timeout=120)
        (evidence / "checkpoint-A.log").write_text(marker_failure_child.stdout + marker_failure_child.stderr)
        if marker_failure_child.returncode == 0:
            raise RuntimeError("CHECKPOINT_A_DID_NOT_FAIL_CLOSED")
        checkpoint_a = asyncio.run(inspect_content(lifecycle.url, conversation,
            "M7333A21261_CHECKPOINT_A", evidence))
        result["checkpoint_a"] = checkpoint_a
        context_a = next((row for row in checkpoint_a.get("stages", [])
                          if row["stage_name"] == "context_prepare"), None)
        if (checkpoint_a["provider_calls"] != 0 or checkpoint_a["provider_status"] != "pending"
                or context_a is None or context_a["status"] != "completed"):
            raise RuntimeError("CHECKPOINT_A_PROVIDER_STARTED_AFTER_MARKER_FAILURE")

        checkpoints = []
        for iteration in range(10):
            for checkpoint in ("B", "C"):
                marker = f"M7333A21261_CHECKPOINT_{checkpoint}_{iteration:02d}"
                child = subprocess.run([sys.executable, "-m", "tests.run_m7333a2126_safety_evidence",
                    "--android-root", str(root), "--evidence", str(evidence), "--child-url", lifecycle.url,
                    "--child-conversation", conversation, "--child-checkpoint", checkpoint,
                    "--child-content", marker], cwd=ROOT, env=env, capture_output=True,
                    text=True, timeout=120)
                (evidence / f"checkpoint-{checkpoint}-{iteration:02d}.log").write_text(child.stdout + child.stderr)
                expected_exit = 93 if checkpoint == "B" else 91
                target = asyncio.run(inspect_content(lifecycle.url, conversation, marker, evidence))
                turn_id = target["turn"]["turn_id"] if target.get("turn") else "missing"
                observation_path = evidence / f"checkpoint-{checkpoint}-{turn_id}.json"
                if child.returncode != expected_exit or not observation_path.is_file():
                    raise RuntimeError(f"CHECKPOINT_{checkpoint}_INTERRUPTION_FAILED_{iteration}")
                observation = json.loads(observation_path.read_text())
                expected_calls = 0 if checkpoint == "B" else 1
                if not observation["provider_started_visible_before_invocation"] or observation["provider_invocation_count"] != expected_calls:
                    raise RuntimeError(f"CHECKPOINT_{checkpoint}_DURABILITY_ASSERTION_FAILED_{iteration}")
                durable = asyncio.run(inspect_turn_id(lifecycle.url, observation["turn"]["turn_id"]))
                if durable["provider_status"] != "running" or durable["assistant_count"] != 0:
                    raise RuntimeError(f"CHECKPOINT_{checkpoint}_DURABLE_STATE_FAILED_{iteration}")
                calls = asyncio.run(inspect_content(lifecycle.url, conversation, marker, evidence))["provider_calls"]
                if calls != expected_calls:
                    raise RuntimeError(f"CHECKPOINT_{checkpoint}_PROVIDER_CALL_COUNT_FAILED_{iteration}")
                checkpoints.append({"checkpoint": checkpoint, "turn_id": observation["turn"]["turn_id"],
                    "provider_invocations": expected_calls, "provider_started": durable["provider_status"],
                    "provider_started_visible_before_invocation": observation[
                        "provider_started_visible_before_invocation"],
                    "assistant_count": durable["assistant_count"]})
        result["interruption_checkpoints"] = checkpoints
        b_rows = [row for row in checkpoints if row["checkpoint"] == "B"]
        c_rows = [row for row in checkpoints if row["checkpoint"] == "C"]
        result["provider_invocations"] = sum(row["provider_invocations"] for row in checkpoints)
        result["provider_invocations_without_durable_provider_started"] = sum(
            not row["provider_started_visible_before_invocation"] for row in checkpoints
        )
        result["missing_provider_started_after_provider_invocation"] = sum(
            row["provider_invocations"] and row["provider_started"] != "running"
            for row in checkpoints
        )
        result["duplicate_assistant_outputs"] = sum(row["assistant_count"] > 1 for row in checkpoints)
        result["stress_iterations"] = len(checkpoints)
        if len(b_rows) != 10 or len(c_rows) != 10:
            raise RuntimeError("INTERRUPTION_STRESS_ITERATIONS_INCOMPLETE")

        android(adb, args.serial, evidence, "same-owner-recovery-sanity",
            "M7333A2125RecoveryHarnessTest#androidRecoveryEntryPointUsesSharedPersona", {
                "m7333a2125_acceptance": "true", "m7333a2125_db_url": endpoint})
        android(adb, args.serial, evidence, "android-desktop-provider-recovery",
            "M7333A2125RecoveryHarnessTest#androidRecoveryDoesNotReplayDesktopProviderStartedTurn", {
                "m7333a21261_desktop_turn": "true", "m7333a2125_db_url": endpoint,
                "m7333a21261_turn_id": c_rows[-1]["turn_id"]})
        # Deterministic Android process-interruption evidence: use the actual
        # Android foreground prepare/provider marker path and return before
        # durable result persistence. Desktop recovery first runs without that
        # explicit evidence to reproduce the former pending-turn behavior.
        android_output = android(adb, args.serial, evidence, "android-origin-interruption-10",
            "M7333A2125RecoveryHarnessTest#androidOriginProviderInvocationWaitsForHostInterruption", {
                "m7333a2126_r1_android_origin": "true", "m7333a2125_db_url": endpoint,
                "m7333a2126_r1_conversation_id": conversation,
                "m7333a2126_r1_content": "M7333A21262_ANDROID_ORIGIN",
                "m7333a21262_iterations": "10", "m7333a21262_no_wait": "true"})
        import re
        android_origin_ids = re.findall(r"ANDROID_PROVIDER_INVOKED turn=([0-9a-f-]{36})", android_output)
        if len(android_origin_ids) != 10:
            raise RuntimeError("ANDROID_ORIGIN_INTERRUPTION_COUNT_MISMATCH")
        result["android_origin_interruption_turns"] = []
        for turn_id in android_origin_ids:
            before = asyncio.run(inspect_turn_id(lifecycle.url, turn_id))
            if before["provider_status"] != "running" or before["assistant_count"] != 0:
                raise RuntimeError("ANDROID_ORIGIN_INTERRUPTION_STATE_INVALID")
            result["android_origin_interruption_turns"].append(turn_id)
        asyncio.run(run_desktop_recovery_once(lifecycle.url, TOKEN, P))
        for turn_id in android_origin_ids:
            old_recovery = asyncio.run(inspect_turn_id(lifecycle.url, turn_id))
            if old_recovery["turn"]["status"] != "pending":
                raise RuntimeError("ANDROID_ORIGIN_OLD_BEHAVIOR_NOT_REPRODUCED")
        result["android_origin_old_behavior"] = {
            "provider_replays": 0, "pending_after_unqualified_desktop_recovery": 10}
        settled_count = asyncio.run(run_desktop_recovery_once(
            lifecycle.url, TOKEN, P,
            interrupted_provider_turns={turn_id: A for turn_id in android_origin_ids}))
        if settled_count.settled_provider_turns != 10:
            raise RuntimeError("ANDROID_ORIGIN_DESKTOP_SETTLEMENT_COUNT_MISMATCH")
        for turn_id in android_origin_ids:
            after = asyncio.run(inspect_turn_id(lifecycle.url, turn_id))
            if (after["turn"]["status"] != "core_failed"
                    or after["turn"]["safe_error_category"] != "PROVIDER_INDETERMINATE"
                    or after["provider_status"] != "failed" or after["assistant_count"] != 0):
                raise RuntimeError("ANDROID_ORIGIN_DESKTOP_SETTLEMENT_INVALID")
        android(adb, args.serial, evidence, "android-origin-owner-reconnect-idempotence",
            "M7333A2125RecoveryHarnessTest#androidRecoveryAfterForeignSettlementIsIdempotent", {
                "m7333a21262_reconnect": "true", "m7333a21262_turn_ids": ",".join(android_origin_ids),
                "m7333a2125_db_url": endpoint})
        repeat_desktop = asyncio.run(run_desktop_recovery_once(
            lifecycle.url, TOKEN, P,
            interrupted_provider_turns={turn_id: A for turn_id in android_origin_ids}))
        if repeat_desktop.settled_provider_turns != 0:
            raise RuntimeError("REPEATED_DESKTOP_SETTLEMENT_NOT_NOOP")
        result["android_origin_settlements"] = 10

        # Reverse direction on actual Desktop-origin interrupted provider turns.
        # Five cross-platform races and five Android-only settlements.
        settlement_race_results = []
        for index, row in enumerate(c_rows[:5]):
            barrier = CrossDeviceBarrier()
            barrier.start()
            subprocess.run([adb, "-s", args.serial, "reverse", f"tcp:{barrier.address[1]}",
                            f"tcp:{barrier.address[1]}"], check=True)
            race_result = {}
            def desktop_racer(turn_id=row["turn_id"], race_barrier=barrier):
                try:
                    invoke_barrier(race_barrier.address, "desktop")
                    race_result["desktop"] = asyncio.run(run_desktop_recovery_once(
                        lifecycle.url, TOKEN, P,
                        interrupted_provider_turns={turn_id: D}))
                except BaseException as race_error:
                    race_result["error"] = repr(race_error)
            racer = threading.Thread(target=desktop_racer)
            racer.start()
            race_output = android(adb, args.serial, evidence, f"settlement-race-{index:02d}",
                "M7333A2125RecoveryHarnessTest#androidRecoveryDoesNotReplayDesktopProviderStartedTurn", {
                    "m7333a21261_desktop_turn": "true", "m7333a21262_settle": "true",
                    "m7333a21262_barrier_url": f"http://127.0.0.1:{barrier.address[1]}/arrive?participant=android",
                    "m7333a2125_db_url": endpoint,
                    "m7333a21261_turn_id": row["turn_id"]})
            racer.join(60)
            subprocess.run([adb, "-s", args.serial, "reverse", "--remove",
                            f"tcp:{barrier.address[1]}"], capture_output=True)
            barrier.close()
            if racer.is_alive() or "error" in race_result:
                raise RuntimeError("SETTLEMENT_RACE_DESKTOP_PARTICIPANT_FAILED")
            android_match = re.search(
                r"ANDROID_SETTLEMENT_RECOVERY turn=" + re.escape(row["turn_id"])
                + r" observer=\{[^\n]*?\"indeterminate\":\s*(\d+)", race_output)
            if not android_match:
                raise RuntimeError("ANDROID_SETTLEMENT_OBSERVER_MISSING")
            android_settlement = int(android_match.group(1))
            desktop_settlement = int(race_result["desktop"].settled_provider_turns)
            if android_settlement + desktop_settlement != 1:
                raise RuntimeError("SETTLEMENT_RACE_NOT_EXACTLY_ONCE")
            after = asyncio.run(inspect_turn_id(lifecycle.url, row["turn_id"]))
            if (after["turn"]["status"] != "core_failed"
                    or after["turn"]["safe_error_category"] != "PROVIDER_INDETERMINATE"
                    or after["assistant_count"] != 0):
                raise RuntimeError("SETTLEMENT_RACE_TERMINAL_STATE_INVALID")
            settlement_race_results.append({"turn_id": row["turn_id"],
                "android_settlements": android_settlement,
                "desktop_settlements": desktop_settlement})
        remaining_desktop_ids = [row["turn_id"] for row in c_rows[5:10]]
        android(adb, args.serial, evidence, "desktop-origin-android-settlement-5",
            "M7333A2125RecoveryHarnessTest#androidRecoveryDoesNotReplayDesktopProviderStartedTurn", {
                "m7333a21261_desktop_turn": "true", "m7333a21262_settle": "true",
                "m7333a21261_turn_ids": ",".join(remaining_desktop_ids),
                "m7333a2125_db_url": endpoint})
        result["settlement_race"] = settlement_race_results
        result["desktop_origin_android_settlements"] = len(remaining_desktop_ids) + 5
        repeated_reverse = asyncio.run(run_desktop_recovery_once(
            lifecycle.url, TOKEN, P,
            interrupted_provider_turns={row["turn_id"]: D for row in c_rows}))
        if repeated_reverse.settled_provider_turns != 0:
            raise RuntimeError("DESKTOP_ORIGIN_OWNER_RECONNECT_NOT_IDEMPOTENT")

        # Successful Android foreground control: provider result is committed
        # through the real chat completion path and remains non-indeterminate.
        android_success_output = android(adb, args.serial, evidence, "normal-android-turn",
            "M7333A2125RecoveryHarnessTest#androidOriginProviderInvocationWaitsForHostInterruption", {
                "m7333a2126_r1_android_origin": "true", "m7333a2126_r1_complete": "true",
                "m7333a21262_no_wait": "true", "m7333a2125_db_url": endpoint,
                "m7333a2126_r1_conversation_id": conversation,
                "m7333a2126_r1_content": "M7333A21262_NORMAL_ANDROID"})
        success_match = re.search(r"ANDROID_PROVIDER_INVOKED turn=([0-9a-f-]{36})", android_success_output)
        if not success_match:
            raise RuntimeError("NORMAL_ANDROID_TURN_ID_MISSING")
        success_turn = asyncio.run(inspect_turn_id(lifecycle.url, success_match.group(1)))
        if (success_turn["turn"]["status"] != "complete"
                or success_turn["provider_status"] != "completed"
                or success_turn["assistant_count"] != 1
                or success_turn["turn"]["safe_error_category"] is not None):
            raise RuntimeError("NORMAL_ANDROID_TURN_INVALID")
        result["normal_android_turn"] = "PASS"

        # Hold the Android synthetic provider callback open while Desktop runs
        # normal recovery with no interruption proof; then let the owner persist
        # the valid result to prove the fresh turn was not falsely terminalized.
        active_barrier = CrossDeviceBarrier()
        active_release = CrossDeviceBarrier()
        active_barrier.start()
        active_release.start()
        for barrier in (active_barrier, active_release):
            subprocess.run([adb, "-s", args.serial, "reverse", f"tcp:{barrier.address[1]}",
                            f"tcp:{barrier.address[1]}"], check=True)
        active_result = {}
        active_content = "M7333A21262 ACTIVE CONTROL iteration 0"
        def recover_during_active_provider():
            try:
                invoke_barrier(active_barrier.address, "desktop")
                asyncio.run(run_desktop_recovery_once(lifecycle.url, TOKEN, P))
                active_result["during"] = asyncio.run(inspect_content(
                    lifecycle.url, conversation, active_content, evidence))
                if (active_result["during"].get("turn", {}).get("status") != "pending"
                        or active_result["during"].get("provider_status") != "running"
                        or active_result["during"].get("assistant_count") != 0):
                    raise RuntimeError("ACTIVE_PROVIDER_FALSE_SETTLEMENT")
                invoke_barrier(active_release.address, "desktop")
            except BaseException as active_error:
                active_result["error"] = repr(active_error)
        active_thread = threading.Thread(target=recover_during_active_provider)
        active_thread.start()
        active_output = android(adb, args.serial, evidence, "active-provider-control",
            "M7333A2125RecoveryHarnessTest#androidOriginProviderInvocationWaitsForHostInterruption", {
                "m7333a2126_r1_android_origin": "true", "m7333a2126_r1_complete": "true",
                "m7333a21262_active_control": "true", "m7333a2125_db_url": endpoint,
                "m7333a2126_r1_conversation_id": conversation,
                "m7333a2126_r1_content": "M7333A21262_ACTIVE_CONTROL",
                "m7333a21262_active_barrier_url": f"http://127.0.0.1:{active_barrier.address[1]}/arrive?participant=android",
                "m7333a21262_release_barrier_url": f"http://127.0.0.1:{active_release.address[1]}/arrive?participant=android"})
        active_thread.join(60)
        for barrier in (active_barrier, active_release):
            subprocess.run([adb, "-s", args.serial, "reverse", "--remove",
                            f"tcp:{barrier.address[1]}"], capture_output=True)
            barrier.close()
        if active_thread.is_alive() or "error" in active_result:
            raise RuntimeError("ACTIVE_PROVIDER_BARRIER_FAILED")
        active_match = re.search(r"ANDROID_PROVIDER_INVOKED turn=([0-9a-f-]{36})", active_output)
        if not active_match:
            raise RuntimeError("ACTIVE_PROVIDER_CONTROL_TURN_MISSING")
        active_turn = active_match.group(1)
        active_after = asyncio.run(inspect_turn_id(lifecycle.url, active_turn))
        if (active_after["turn"]["status"] != "complete"
                or active_after["provider_status"] != "completed"
                or active_after["assistant_count"] != 1):
            raise RuntimeError("ACTIVE_PROVIDER_CONTROL_FALSE_SETTLEMENT")
        result.update({"android_replay_of_desktop_provider": 0,
                       "android_duplicate_assistant_outputs": 0,
                       "same_owner_recovery_sanity": "PASS",
                       "provider_started_visible_before_provider_invocation": "YES",
                       "provider_started_durability": "PASS",
                       "android_origin_provider_replays": 0,
                       "desktop_origin_provider_replays": 0,
                       "stale_provider_started_turns_left_running": 0,
                       "active_provider_turn_false_settlements": 0,
                       "duplicate_terminal_transitions": 0,
                       "focused_failures": 0, "focused_errors": 0,
                       "focused_core_skips": 0, "verdict": "PASS"})
        print("FOCUSED_GATE=PASS", flush=True)
        return 0
    except BaseException as error:
        result.update(verdict="FAIL", error_type=type(error).__name__, error=str(error))
        print("FOCUSED_GATE=FAIL", str(error), flush=True)
        return 1
    finally:
        if concurrent_thread is not None:
            concurrent_thread.join(60)
            result["desktop_append_errors"] = concurrent_errors
            result["desktop_append_thread_still_running"] = concurrent_thread.is_alive()
        result["hrana_errors"] = hrana_errors
        result["append_isolated"] = args.append_isolated
        heartbeat_stop.set()
        heartbeat_thread.join(2)
        result["host_heartbeat_max_gap_ms"] = max(heartbeat, default=0)
        write(evidence / "hrana-request-timings.json", hrana_requests)
        if lifecycle.process is not None and lifecycle.process.poll() is None and "conversation_id" in result:
            try:
                write(evidence / "final-durable-state.json", asyncio.run(inspect(lifecycle.url, result["conversation_id"])))
            except Exception as inspection_error:
                result["final_inspection_error"] = type(inspection_error).__name__
        write(evidence / "result.json", result)
        if proxy:
            subprocess.run([adb, "-s", args.serial, "reverse", "--remove", f"tcp:{proxy.address[1]}"], capture_output=True)
            proxy.close()
        if control:
            subprocess.run([adb, "-s", args.serial, "reverse", "--remove", f"tcp:{control.address[1]}"], capture_output=True)
            control.close()
        lifecycle.close()


if __name__ == "__main__":
    raise SystemExit(main())
