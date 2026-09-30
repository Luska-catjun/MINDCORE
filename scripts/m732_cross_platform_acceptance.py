#!/usr/bin/env python3
"""Run an isolated AndroidKeyStore/Desktop-keyring secure resolution exchange.

Requires a dedicated test emulator with the current debug and androidTest APKs.
Only session-scoped synthetic Persona files and a newly generated keyring item
are created; both are removed in the finalizer.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import uuid

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))

from app.services.manual_sync_transport import write_pairing_artifact, write_sync_artifact
from app.services.persona_sync import PersonaSyncEngine, sync_metadata_path
from app.services.persona_sync_crypto import NativeKeyringBackend


PACKAGE = "com.luskacat.mindcore.android"
RUNNER = PACKAGE + ".test/androidx.test.runner.AndroidJUnitRunner"
TEST = "com.luskacat.mindcore.M7311DesktopInteropBridgeTest#processHostBridgeRequest"


def run(argv: list[str], *, payload: bytes | None = None, ok: bool = True) -> bytes:
    result = subprocess.run(argv, input=payload, capture_output=True, check=False, cwd=ROOT)
    if ok and result.returncode:
        raise RuntimeError(f"command failed: {argv[0]} {argv[1]}: {result.stderr[-500:]!r}")
    return result.stdout


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adb", required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--desktop-sidecar", help="built production sidecar executable")
    args = parser.parse_args()
    session = str(uuid.uuid4())
    android_device = str(uuid.uuid4())
    persona = str(uuid.uuid4())
    persona2 = str(uuid.uuid4())
    android_conversation = str(uuid.uuid4())
    desktop_conversation = str(uuid.uuid4())
    alias = "m7311." + session
    relative = f"files/m7311/{session}"
    adb = [args.adb, "-s", args.device]
    desktop_device: str | None = None

    with tempfile.TemporaryDirectory(prefix="m732-actual-", dir=ROOT / "tests") as temporary:
        work = Path(temporary)
        state = work / "desktop-state"
        database = work / "persona" / "mindcore.db"
        database2 = work / "persona-2" / "mindcore.db"
        database.parent.mkdir()
        database2.parent.mkdir()

        def bridge(operation: str, **fields: object) -> dict:
            request = {"operation": operation, "android_device_id": android_device,
                       "android_keystore_alias": alias, **fields}
            run(adb + ["shell", "run-as", PACKAGE, "mkdir", "-p", relative])
            run(adb + ["shell", "run-as", PACKAGE, "tee",
                       f"{relative}/request.json"],
                payload=json.dumps(request, separators=(",", ":")).encode())
            output = run(adb + ["shell", "am", "instrument", "-w", "-r", "-e",
                                "m7311_session", session, "-e", "class", TEST, RUNNER])
            if b"FAILURES" in output or b"INSTRUMENTATION_FAILED" in output:
                raise RuntimeError("Android bridge instrumentation failed")
            result = run(adb + ["exec-out", "run-as", PACKAGE, "cat",
                                f"{relative}/result.json"])
            return json.loads(result)

        def desktop(command: str, **fields: str) -> dict:
            executable = ([args.desktop_sidecar] if args.desktop_sidecar
                          else [sys.executable, "-m", "app.desktop_backend"])
            argv = executable + ["--sync-command", command, "--sync-state-dir", str(state)]
            for key, value in fields.items():
                argv += ["--sync-" + key.replace("_", "-"), value]
            result = subprocess.run(argv, capture_output=True, cwd=ROOT, check=False)
            raw = result.stdout if result.returncode == 0 else result.stderr
            try:
                parsed = json.loads(raw.splitlines()[-1])
            except json.JSONDecodeError as error:
                raise RuntimeError(f"Desktop sync action failed: {raw[-500:]!r}") from error
            if result.returncode:
                return {"error": parsed["error"]}
            return parsed

        def verified(result: dict, expected: str | None = None) -> dict:
            if expected is None and "error" in result:
                raise AssertionError(result)
            if expected is not None and result.get("error") != expected:
                raise AssertionError((expected, result))
            return result

        def desktop_conflicts(owner: str, target: Path) -> dict:
            return verified(desktop("conflicts", database=str(target), persona_id=owner))

        try:
            bootstrap = verified(bridge("bootstrap", personas=[persona, persona2],
                                        conversation_ids=[android_conversation,
                                                          desktop_conversation]))
            assert {item["persona_id"] for item in bootstrap["personas"]} == {persona, persona2}
            for owner, target in ((persona, database), (persona2, database2)):
                target.write_bytes(run(adb + ["exec-out", "run-as", PACKAGE, "cat",
                                            f"{relative}/personas/{owner}/mindcore.db"]))
                (target.parent / "identity.json").write_bytes(run(
                    adb + ["exec-out", "run-as", PACKAGE, "cat",
                           f"{relative}/personas/{owner}/identity.json"]))
            identity = verified(desktop("identity"))
            desktop_device = identity["device_id"]
            android_pair = verified(bridge("identity"))["pairing"]
            android_pair_path = write_pairing_artifact(work / "android.mindcorepair", android_pair)
            desktop_pair_path = work / "desktop.mindcorepair"
            verified(desktop("pair-export", out=str(desktop_pair_path)))
            verified(desktop("pair-import", artifact=str(android_pair_path),
                             confirm_fingerprint=android_pair["fingerprint"]))
            verified(bridge("pair", artifact=json.loads(desktop_pair_path.read_text()),
                            confirmed_fingerprint=identity["fingerprint"]))

            # Establish the shared baseline before independent offline edits.
            assert desktop_conflicts(persona, database)["conflicts"] == []
            assert desktop_conflicts(persona2, database2)["conflicts"] == []
            p2_before = verified(bridge("observe", persona_id=persona2))["manifest"]["records"]
            # Independent offline append with separate conversation sequences.
            desktop_message = str(uuid.uuid4())
            with sqlite3.connect(database) as db:
                db.execute("INSERT INTO messages(id,conversation_id,sequence,role,content,"
                           "source_device,created_at) VALUES(?,?,1,'user',?,?,?)",
                           (desktop_message, desktop_conversation, "desktop synthetic append",
                            desktop_device, "2026-09-03T00:00:00Z"))
            android_append = verified(bridge("append_export", persona_id=persona,
                                              peer_device_id=desktop_device,
                                              conversation_id=android_conversation,
                                              content="android synthetic append"))
            desktop_append_file = work / "desktop-append.mindcoresync"
            verified(desktop("export", database=str(database), persona_id=persona,
                             peer=android_device, out=str(desktop_append_file)))
            verified(bridge("apply", persona_id=persona,
                            envelope=json.loads(desktop_append_file.read_text())))
            android_append_file = write_sync_artifact(work / "android-append.mindcoresync",
                                                      android_append["envelope"])
            verified(desktop("apply", database=str(database), persona_id=persona,
                             artifact=str(android_append_file)))
            with sqlite3.connect(database) as db:
                db.execute("UPDATE relationship SET trust=0.81 WHERE id=1")
            android_change = verified(bridge("relationship_update", persona_id=persona,
                                             peer_device_id=desktop_device, trust=0.91))
            desktop_file = work / "desktop-change.mindcoresync"
            verified(desktop("export", database=str(database), persona_id=persona,
                             peer=android_device, out=str(desktop_file)))
            android_import = bridge("apply", persona_id=persona,
                                    envelope=json.loads(desktop_file.read_text()))
            assert "error" in android_import, android_import
            android_file = write_sync_artifact(work / "android-change.mindcoresync",
                                               android_change["envelope"])
            verified(desktop("apply", database=str(database), persona_id=persona,
                             artifact=str(android_file)), "CONCURRENT_MUTATION")
            inbox = verified(bridge("conflicts", persona_id=persona))["conflicts"]
            assert len(inbox) == 1 and inbox[0]["conflict_type"] == "CONCURRENT_MUTATION", inbox
            desktop_inbox = desktop_conflicts(persona, database)["conflicts"]
            assert len(desktop_inbox) == 1, desktop_inbox
            verified(bridge("resolve", persona_id=persona,
                            conflict_id=inbox[0]["conflict_id"],
                            choice="KEEP_THIS_DEVICE_VERSION"))
            resolution = verified(bridge("secure_export", persona_id=persona,
                                         peer_device_id=desktop_device))
            assert len(resolution["delta"]["resolutions"]) == 1
            resolution_file = write_sync_artifact(work / "android-resolution.mindcoresync",
                                                  resolution["envelope"])
            first = verified(desktop("apply", database=str(database), persona_id=persona,
                                     artifact=str(resolution_file)))
            assert first["applied"] == 1, first
            assert desktop_conflicts(persona, database)["conflicts"] == []
            android_state = verified(bridge("observe", persona_id=persona))["manifest"]["records"]
            desktop_state = PersonaSyncEngine(database, sync_metadata_path(state),
                                              desktop_device).scan(persona).manifest.as_dict()["records"]
            def logical(records: list[dict]) -> dict:
                return {(item["entity_kind"], item["entity_id"]):
                        (item["content_hash"], item["tombstone"]) for item in records}
            if logical(android_state) != logical(desktop_state):
                difference = {str(key): (logical(android_state).get(key),
                                         logical(desktop_state).get(key))
                              for key in logical(android_state).keys() | logical(desktop_state).keys()
                              if logical(android_state).get(key) != logical(desktop_state).get(key)}
                raise AssertionError({"divergent_hashes": difference})

            # Reverse origin: Desktop resolves a second real offline mutation.
            with sqlite3.connect(database) as db:
                db.execute("UPDATE relationship SET trust=0.41 WHERE id=1")
            android_change = verified(bridge("relationship_update", persona_id=persona,
                                             peer_device_id=desktop_device, trust=0.51))
            desktop_file = work / "desktop-change-2.mindcoresync"
            verified(desktop("export", database=str(database), persona_id=persona,
                             peer=android_device, out=str(desktop_file)))
            assert "error" in bridge("apply", persona_id=persona,
                                     envelope=json.loads(desktop_file.read_text()))
            android_file = write_sync_artifact(work / "android-change-2.mindcoresync",
                                               android_change["envelope"])
            verified(desktop("apply", database=str(database), persona_id=persona,
                             artifact=str(android_file)), "CONCURRENT_MUTATION")
            desktop_inbox = desktop_conflicts(persona, database)["conflicts"]
            verified(desktop("resolve", database=str(database), persona_id=persona,
                             conflict_id=desktop_inbox[0]["conflict_id"],
                             choice="KEEP_THIS_DEVICE_VERSION"))
            desktop_file = work / "desktop-resolution.mindcoresync"
            verified(desktop("export", database=str(database), persona_id=persona,
                             peer=android_device, out=str(desktop_file)))
            reverse = verified(bridge("apply", persona_id=persona,
                                      envelope=json.loads(desktop_file.read_text())))
            assert reverse["applied"] == 1, reverse
            assert verified(bridge("conflicts", persona_id=persona))["conflicts"] == []

            # Android tombstone versus a Desktop update; keep the updated row.
            tombstone = verified(bridge("tombstone_export", persona_id=persona,
                                        peer_device_id=desktop_device,
                                        entity_kind="relationship", entity_id="1"))
            with sqlite3.connect(database) as db:
                db.execute("UPDATE relationship SET trust=0.61 WHERE id=1")
            desktop_file = work / "desktop-update-3.mindcoresync"
            verified(desktop("export", database=str(database), persona_id=persona,
                             peer=android_device, out=str(desktop_file)))
            assert "error" in bridge("apply", persona_id=persona,
                                     envelope=json.loads(desktop_file.read_text()))
            tombstone_file = write_sync_artifact(work / "android-tombstone.mindcoresync",
                                                 tombstone["envelope"])
            verified(desktop("apply", database=str(database), persona_id=persona,
                             artifact=str(tombstone_file)), "TOMBSTONE_CONFLICT")
            inbox = verified(bridge("conflicts", persona_id=persona))["conflicts"]
            assert inbox[0]["conflict_type"] == "TOMBSTONE_CONFLICT", inbox
            verified(bridge("resolve", persona_id=persona,
                            conflict_id=inbox[0]["conflict_id"], choice="KEEP_UPDATED_ITEM"))
            resolution = verified(bridge("secure_export", persona_id=persona,
                                         peer_device_id=desktop_device))
            resolution_file = write_sync_artifact(work / "android-keep-updated.mindcoresync",
                                                  resolution["envelope"])
            verified(desktop("apply", database=str(database), persona_id=persona,
                             artifact=str(resolution_file)))

            # Repeat the delete/update race and choose a shared tombstone on Desktop.
            tombstone = verified(bridge("tombstone_export", persona_id=persona,
                                        peer_device_id=desktop_device,
                                        entity_kind="relationship", entity_id="1"))
            with sqlite3.connect(database) as db:
                db.execute("UPDATE relationship SET trust=0.71 WHERE id=1")
            desktop_file = work / "desktop-update-4.mindcoresync"
            verified(desktop("export", database=str(database), persona_id=persona,
                             peer=android_device, out=str(desktop_file)))
            assert "error" in bridge("apply", persona_id=persona,
                                     envelope=json.loads(desktop_file.read_text()))
            tombstone_file = write_sync_artifact(work / "android-tombstone-2.mindcoresync",
                                                 tombstone["envelope"])
            verified(desktop("apply", database=str(database), persona_id=persona,
                             artifact=str(tombstone_file)), "TOMBSTONE_CONFLICT")
            desktop_inbox = desktop_conflicts(persona, database)["conflicts"]
            verified(desktop("resolve", database=str(database), persona_id=persona,
                             conflict_id=desktop_inbox[0]["conflict_id"],
                             choice="DELETE_ON_BOTH_DEVICES"))
            desktop_file = work / "desktop-delete-resolution.mindcoresync"
            verified(desktop("export", database=str(database), persona_id=persona,
                             peer=android_device, out=str(desktop_file)))
            verified(bridge("apply", persona_id=persona,
                            envelope=json.loads(desktop_file.read_text())))
            assert verified(bridge("conflicts", persona_id=persona))["conflicts"] == []
            assert desktop_conflicts(persona, database)["conflicts"] == []
            android_state = verified(bridge("observe", persona_id=persona))["manifest"]["records"]
            desktop_state = PersonaSyncEngine(database, sync_metadata_path(state),
                                              desktop_device).scan(persona).manifest.as_dict()["records"]
            assert logical(android_state) == logical(desktop_state)
            assert logical(android_state)[("relationship", "1")][1] is True
            # Fresh files with already known operations must cause no domain
            # mutation, and routing a P1 file to P2 must fail closed.
            final_desktop_file = work / "desktop-final-noop.mindcoresync"
            verified(desktop("export", database=str(database), persona_id=persona,
                             peer=android_device, out=str(final_desktop_file)))
            assert "error" in bridge("apply", persona_id=persona2,
                                     envelope=json.loads(final_desktop_file.read_text()))
            noop_android = verified(bridge("apply", persona_id=persona,
                                           envelope=json.loads(final_desktop_file.read_text())))
            assert noop_android["applied"] == 0, noop_android
            final_android = verified(bridge("secure_export", persona_id=persona,
                                             peer_device_id=desktop_device))
            final_android_file = write_sync_artifact(work / "android-final-noop.mindcoresync",
                                                     final_android["envelope"])
            assert "error" in desktop("apply", database=str(database2), persona_id=persona2,
                                      artifact=str(final_android_file))
            noop_desktop = verified(desktop("apply", database=str(database), persona_id=persona,
                                            artifact=str(final_android_file)))
            assert noop_desktop["applied"] == 0, noop_desktop
            run(adb + ["shell", "am", "force-stop", PACKAGE])
            assert verified(bridge("conflicts", persona_id=persona))["conflicts"] == []
            assert desktop_conflicts(persona, database)["conflicts"] == []
            p2_after = verified(bridge("observe", persona_id=persona2))["manifest"]["records"]
            desktop_p2_after = PersonaSyncEngine(database2, sync_metadata_path(state),
                                                 desktop_device).scan(persona2).manifest.as_dict()["records"]
            assert logical(p2_before) == logical(p2_after) == logical(desktop_p2_after)
            assert verified(bridge("count_message", persona_id=persona,
                                   message_id=desktop_message))["count"] == 1
            assert verified(bridge("count_message", persona_id=persona,
                                   message_id=android_append["message_id"]))["count"] == 1
            with sqlite3.connect(database) as db:
                assert db.execute("SELECT COUNT(*) FROM messages WHERE id IN (?,?)",
                                  (desktop_message, android_append["message_id"])).fetchone()[0] == 2
            print(json.dumps({"result": "PASS", "persona_count": 2,
                              "mutable_resolution_origin": "Android",
                              "reverse_mutable_resolution_origin": "Desktop",
                              "keep_updated_origin": "Android",
                              "delete_on_both_origin": "Desktop",
                              "desktop_resolution_apply": first,
                              "pending_desktop_conflicts": 0,
                              "final_noop_android_applied": noop_android["applied"],
                              "final_noop_desktop_applied": noop_desktop["applied"],
                              "wrong_persona_rejected": True,
                              "offline_append_records": 2,
                              "append_duplicates": 0,
                              "record_state_equal": True}, sort_keys=True))
        finally:
            if desktop_device and re.fullmatch(r"[0-9a-f-]{36}", desktop_device):
                NativeKeyringBackend().delete_secret(desktop_device)
            try:
                bridge("cleanup")
            finally:
                run(adb + ["shell", "run-as", PACKAGE, "rm", "-r", relative], ok=False)


if __name__ == "__main__":
    main()
