"""Synthetic protocol-v1 interoperability, persistence, and artifact tests."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid

from app.services.manual_sync_transport import (read_pairing_artifact, read_sync_artifact,
                                                write_pairing_artifact, write_sync_artifact)
from app.services.persona_sync import SCHEMA_VERSION, canonical_json
from app.services.persona_sync_crypto import (NativeKeyringBackend, PersonaSyncSecurity,
                                              SyncSecurityError)


class MemoryKeyring:
    def __init__(self):
        self.values: dict[str, str] = {}

    def get_secret(self, account: str) -> str | None:
        return self.values.get(account)

    def set_secret(self, account: str, value: str) -> None:
        self.values[account] = value

    def delete_secret(self, account: str) -> None:
        self.values.pop(account, None)


class PersonaSyncCryptoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="m73-sync-crypto-")
        self.root = Path(self.temp.name)
        self.persona_id = "81000000-0000-4000-8000-000000000001"
        self.key_a = MemoryKeyring()
        self.key_b = MemoryKeyring()
        self.a = PersonaSyncSecurity(self.root / "device-a", self.key_a)
        self.b = PersonaSyncSecurity(self.root / "device-b", self.key_b)
        self.id_a = self.a.identity()
        self.id_b = self.b.identity()
        self.a.pair(self.b.pairing_artifact(), self.id_b.fingerprint)
        self.b.pair(self.a.pairing_artifact(), self.id_a.fingerprint)

    def tearDown(self):
        self.temp.cleanup()

    def test_device_identity_is_stable_and_separate_from_persona(self):
        reopened = PersonaSyncSecurity(self.root / "device-a", self.key_a).identity()
        self.assertEqual(self.id_a, reopened)
        self.assertNotEqual(self.persona_id, self.id_a.device_id)
        security_db = self.root / "device-a" / "sync" / "security.sqlite"
        contents = security_db.read_bytes()
        self.assertNotIn(base64.b64decode(self.key_a.values[self.id_a.device_id]), contents)
        self.assertEqual(SCHEMA_VERSION, 24)  # Sync identity does not change the cognition schema.

    def test_explicit_pairing_fingerprint_and_revoke(self):
        unknown = PersonaSyncSecurity(self.root / "unknown", MemoryKeyring())
        artifact = self.b.pairing_artifact()
        with self.assertRaisesRegex(SyncSecurityError, "PAIRING_FINGERPRINT_MISMATCH"):
            unknown.pair(artifact, "0" * 64)
        self.assertEqual("UNKNOWN", unknown.peer_state(self.id_b.device_id))
        unknown.pair(artifact, self.id_b.fingerprint)
        self.assertEqual("TRUSTED", unknown.peer_state(self.id_b.device_id))
        unknown.revoke(self.id_b.device_id)
        self.assertEqual("REVOKED", unknown.peer_state(self.id_b.device_id))

    def test_authenticated_envelope_replay_and_restart_persistence(self):
        payload = {"protocol_version": 1, "persona_id": self.persona_id,
                   "source_device_id": self.id_a.device_id, "entries": [], "conflicts": []}
        envelope = self.a.encrypt(self.id_b.device_id, self.persona_id, canonical_json(payload))
        inbound = self.b.decrypt_and_reserve(self.persona_id, canonical_json(envelope))
        self.assertFalse(inbound.retry_pending)
        self.assertEqual(payload, json.loads(inbound.payload_json))
        reopened_b = PersonaSyncSecurity(self.root / "device-b", self.key_b)
        retry = reopened_b.decrypt_and_reserve(self.persona_id, envelope)
        self.assertTrue(retry.retry_pending)
        reopened_b.complete_inbound(retry.sender_device_id, retry.message_id, retry.sequence)
        with self.assertRaisesRegex(SyncSecurityError, "REPLAY_REJECTED"):
            PersonaSyncSecurity(self.root / "device-b", self.key_b).decrypt_and_reserve(
                self.persona_id, envelope)

    def test_tamper_wrong_persona_and_corrupt_trust_fail_closed(self):
        payload = {"persona_id": self.persona_id, "source_device_id": self.id_a.device_id,
                   "protocol_version": 1, "entries": [], "conflicts": []}
        envelope = self.a.encrypt(self.id_b.device_id, self.persona_id, canonical_json(payload))
        changed = dict(envelope)
        cipher = bytearray(base64.b64decode(changed["ciphertext"]))
        cipher[0] ^= 1
        changed["ciphertext"] = base64.b64encode(cipher).decode("ascii")
        with self.assertRaisesRegex(SyncSecurityError, "AUTHENTICATION_FAILED"):
            self.b.decrypt_and_reserve(self.persona_id, changed)
        with self.assertRaisesRegex(SyncSecurityError, "WRONG_PERSONA_OR_RECIPIENT"):
            self.b.decrypt_and_reserve("82000000-0000-4000-8000-000000000001", envelope)
        path = self.root / "device-b" / "sync" / "security.sqlite"
        db = sqlite3.connect(path)
        db.execute("UPDATE trusted_peer SET public_key='invalid' WHERE device_id=?",
                   (self.id_a.device_id,))
        db.commit()
        db.close()
        self.assertEqual("CORRUPT", self.b.peer_state(self.id_a.device_id))
        with self.assertRaisesRegex(SyncSecurityError, "TRUST_STORE_CORRUPT"):
            self.b.decrypt_and_reserve(self.persona_id, envelope)

    def test_unknown_sidecar_version_is_not_reset_or_rewritten(self):
        path = self.root / "corrupt" / "sync" / "security.sqlite"
        path.parent.mkdir(parents=True)
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE preserved(value TEXT)")
        db.execute("PRAGMA user_version=99")
        db.commit()
        db.close()
        before = path.read_bytes()
        with self.assertRaisesRegex(SyncSecurityError, "TRUST_STORE_CORRUPT"):
            PersonaSyncSecurity(self.root / "corrupt", MemoryKeyring()).identity()
        self.assertEqual(before, path.read_bytes())

    def test_pairing_artifact_file_is_exclusive_and_path_safe(self):
        artifact = self.a.pairing_artifact()
        path = write_pairing_artifact(self.root / "a.mindcorepair", artifact)
        self.assertEqual(artifact, read_pairing_artifact(path))
        with self.assertRaisesRegex(SyncSecurityError, "ARTIFACT_ALREADY_EXISTS"):
            write_pairing_artifact(path, artifact)
        with self.assertRaisesRegex(SyncSecurityError, "ARTIFACT_PATH_INVALID"):
            write_pairing_artifact(self.root / "nested" / ".." / "bad.mindcorepair", artifact)

    def test_sync_artifact_contains_only_secure_envelope_fields(self):
        payload = {"content": "synthetic plaintext must not reach transport",
                   "persona_id": self.persona_id}
        envelope = self.a.encrypt(self.id_b.device_id, self.persona_id, canonical_json(payload))
        path = write_sync_artifact(self.root / "payload.mindcoresync", envelope)
        self.assertEqual(envelope, read_sync_artifact(path))
        data = path.read_text(encoding="utf-8")
        self.assertNotIn("synthetic plaintext", data)
        with self.assertRaisesRegex(SyncSecurityError, "ENVELOPE_FIELDS_UNSUPPORTED"):
            write_sync_artifact(self.root / "bad.mindcoresync", {**envelope, "persona": payload})

    def test_os_native_keyring_backend_stores_synthetic_key_outside_sidecar(self):
        service = f"MindCore M7.3.1 isolated test {uuid.uuid4()}"
        backend = NativeKeyringBackend(service_name=service)
        state = self.root / "native-keyring"
        security = PersonaSyncSecurity(state, backend)
        identity = None
        try:
            identity = security.identity()
            secret = backend.get_secret(identity.device_id)
            self.assertIsNotNone(secret)
            self.assertEqual(identity, PersonaSyncSecurity(state, backend).identity())
            db_bytes = (state / "sync" / "security.sqlite").read_bytes()
            raw_private = base64.b64decode(secret or "")
            self.assertNotIn(raw_private, db_bytes)
        finally:
            if identity is not None:
                backend.delete_secret(identity.device_id)


if __name__ == "__main__":
    unittest.main()
