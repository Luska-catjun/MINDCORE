#!/usr/bin/env python3
"""Manual, local-file secure sync commands for synthetic/portable schema-24 DBs."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any

from app.services.manual_sync_transport import (read_pairing_artifact, read_sync_artifact,
                                                write_pairing_artifact, write_sync_artifact)
from app.services.persona_sync import (PersonaSyncEngine, SyncError, apply_remote_delta_json,
                                       canonical_json, sync_metadata_path)
from app.services.persona_sync_crypto import PersonaSyncSecurity, SyncSecurityError


def _regular_file(value: str) -> Path:
    path = Path(value)
    try:
        details = path.lstat()
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
            raise SyncSecurityError("PERSONA_DATABASE_UNAVAILABLE")
    except OSError as error:
        raise SyncSecurityError("PERSONA_DATABASE_UNAVAILABLE") from error
    return path.resolve(strict=True)


def _state_path(value: str) -> Path:
    path = Path(value)
    if ".." in path.parts or "\x00" in os.fspath(path):
        raise SyncSecurityError("SYNC_STATE_PATH_INVALID")
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.is_symlink() or not path.is_dir():
            raise SyncSecurityError("SYNC_STATE_PATH_INVALID")
    except OSError as error:
        raise SyncSecurityError("SYNC_STATE_UNAVAILABLE") from error
    return path.resolve(strict=True)


def _identity(args: argparse.Namespace) -> dict[str, Any]:
    identity = PersonaSyncSecurity(_state_path(args.state_dir)).identity()
    return {"device_id": identity.device_id, "fingerprint": identity.fingerprint,
            "public_key": identity.public_key}


def _handle(args: argparse.Namespace) -> dict[str, Any]:
    state_dir = _state_path(args.state_dir)
    security = PersonaSyncSecurity(state_dir)
    if args.command == "identity":
        identity = security.identity()
        return {"device_id": identity.device_id, "fingerprint": identity.fingerprint,
                "public_key": identity.public_key}
    if args.command == "pair-export":
        return {"artifact": str(write_pairing_artifact(args.out, security.pairing_artifact()))}
    if args.command == "pair-import":
        artifact = read_pairing_artifact(args.artifact)
        identity = security.pair(artifact, args.confirm_fingerprint)
        return {"device_id": identity.device_id, "peer_device_id": artifact["device_id"],
                "peer_fingerprint": artifact["fingerprint"], "peer_state": "TRUSTED"}
    if args.command == "peer-state":
        return {"peer_device_id": args.peer, "state": security.peer_state(args.peer)}
    if args.command == "revoke":
        security.revoke(args.peer)
        return {"peer_device_id": args.peer, "state": security.peer_state(args.peer)}
    if args.command == "export":
        persona = args.persona_id
        database = _regular_file(args.database)
        identity_path = _regular_file(args.identity_path) if args.identity_path else None
        identity = security.identity()
        engine = PersonaSyncEngine(database, sync_metadata_path(state_dir), identity.device_id,
                                   identity_path)
        scan = engine.scan(persona)
        if not scan.delta.entries and not scan.delta.conflicts:
            raise SyncError("NO_CHANGES")
        envelope = security.encrypt(args.peer, persona, canonical_json(scan.delta.as_dict()))
        path = write_sync_artifact(args.out, envelope)
        return {"artifact": str(path), "delta_entries": len(scan.delta.entries),
                "delta_conflicts": len(scan.delta.conflicts), "persona_id": persona,
                "sequence": envelope["sequence"], "status": "EXPORTED"}
    if args.command == "apply":
        persona = args.persona_id
        database = _regular_file(args.database)
        identity_path = _regular_file(args.identity_path) if args.identity_path else None
        envelope = read_sync_artifact(args.artifact)
        inbound = security.decrypt_and_reserve(persona, envelope)
        result = json.loads(apply_remote_delta_json(
            database, state_dir, security.identity().device_id, persona,
            inbound.sender_device_id, inbound.payload_json))
        security.complete_inbound(inbound.sender_device_id, inbound.message_id, inbound.sequence)
        # Keep Desktop's local identity projection in sync only when using the
        # protocol's SQLite identity entity is ever allowed by the core. Current
        # v1 reject behavior remains fail-closed and file identity is not mutated.
        del identity_path
        return {"applied": result["applied"], "idempotent": result["idempotent"],
                "persona_id": result["persona_id"], "status": result["status"]}
    raise SyncSecurityError("COMMAND_UNSUPPORTED")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="MindCore Desktop manual secure sync (protocol v1)")
    commands = root.add_subparsers(dest="command", required=True)
    def state_command(name: str, help_text: str) -> argparse.ArgumentParser:
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--state-dir", required=True,
                             help="explicit app-private sync sidecar directory")
        return command
    state_command("identity", "show the public identity and fingerprint")
    export_pair = state_command("pair-export", "write a public .mindcorepair artifact")
    export_pair.add_argument("--out", required=True)
    import_pair = state_command("pair-import", "trust a peer after out-of-band fingerprint confirmation")
    import_pair.add_argument("--artifact", required=True)
    import_pair.add_argument("--confirm-fingerprint", required=True)
    peer_state = state_command("peer-state", "inspect a peer trust state")
    peer_state.add_argument("--peer", required=True)
    revoke = state_command("revoke", "revoke a trusted peer")
    revoke.add_argument("--peer", required=True)
    export = state_command("export", "encrypt changed records into a .mindcoresync file")
    export.add_argument("--database", required=True, help="local schema-24 SQLite Persona snapshot")
    export.add_argument("--persona-id", required=True)
    export.add_argument("--peer", required=True)
    export.add_argument("--identity-path", help="optional text/identity.json source file")
    export.add_argument("--out", required=True)
    apply = state_command("apply", "authenticate and apply a .mindcoresync file")
    apply.add_argument("--database", required=True, help="local schema-24 SQLite Persona snapshot")
    apply.add_argument("--persona-id", required=True)
    apply.add_argument("--identity-path", help="reserved, read-only local identity path")
    apply.add_argument("--artifact", required=True)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = _handle(args)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return 0
    except (SyncSecurityError, SyncError) as error:
        code = getattr(error, "code", str(error))
        print(json.dumps({"error": code}, sort_keys=True, separators=(",", ":")), file=sys.stderr)
        return 2
    except Exception:
        print('{"error":"SYNC_OPERATION_FAILED"}', file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
