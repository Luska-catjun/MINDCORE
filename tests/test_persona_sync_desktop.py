"""Synthetic schema-24 acceptance tests for the Desktop sync counterpart."""
import copy
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path.cwd()

from app.services.persona_sync import (  # noqa: E402
    BASELINE_ORIGIN_DEVICE_ID,
    NON_SYNC_ENTITY_POLICIES,
    SYNC_ENTITY_POLICIES,
    PersonaSyncEngine,
    SyncApplyConflict,
    SyncDelta,
    DeltaEntry,
    SyncError,
    SyncManifest,
    SyncRecord,
    SyncRevision,
    SyncMetadataStore,
    canonical_hash,
    canonical_json,
    apply_remote_delta,
    detect_conflicts,
    generate_delta,
    manifest_semantically_equal,
    make_resolution,
    sync_metadata_path,
    resolve_conflict_json,
    validate_delta,
    validate_manifest,
)
BASELINE_SQL = (ROOT / "db/turso/baseline_v1.sql").read_text(encoding="utf-8")


PERSONA_A = "71000000-0000-4000-8000-000000000001"
PERSONA_B = "71000000-0000-4000-8000-000000000002"
DEVICE_A = "72000000-0000-4000-8000-000000000001"
DEVICE_B = "72000000-0000-4000-8000-000000000002"
CONVERSATION = "73000000-0000-4000-8000-000000000001"
MESSAGE = "74000000-0000-4000-8000-000000000001"
MEMORY = "75000000-0000-4000-8000-000000000001"


def create_persona_db(path: Path, persona_id: str, *, message_id: str = MESSAGE,
                      identity_text: str = "Synthetic Persona identity") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(BASELINE_SQL)
    connection.execute("INSERT INTO schema_metadata(key,value) VALUES('android_persona_id',?)",
                       (persona_id,))
    connection.execute("INSERT INTO schema_metadata(key,value) VALUES('android_installation_id',?)",
                       (DEVICE_A,))
    connection.execute("INSERT INTO conversations(conversation_id,source_device,started_at,ended_at) "
                       "VALUES(?,?,?,NULL)", (CONVERSATION, "synthetic", "2026-09-01T00:00:00Z"))
    connection.execute("INSERT INTO messages(id,conversation_id,sequence,role,content,source_device,created_at) "
                       "VALUES(?,?,1,'user',?,'synthetic','2026-09-01T00:00:01Z')",
                       (message_id, CONVERSATION, "synthetic private message"))
    connection.execute("INSERT INTO relationship(id,familiarity,trust,affection,shared_experience,"
                       "conflict_history,updated_at,conflict) VALUES(1,0.1,0.2,0.3,0.4,'[]',"
                       "'2026-09-01T00:00:00Z',0.0)")
    connection.execute("INSERT INTO diana_needs(need_key,value,baseline,updated_at,last_triggered_at,metadata) "
                       "VALUES('curiosity',0.5,0.4,'2026-09-01T00:00:00Z',NULL,'{}')")
    connection.execute("INSERT INTO memories(memory_id,content,normalized_content,memory_type,importance,"
                       "source_conversation_id,source_message_id,created_at,updated_at,recall_frequency,"
                       "memory_strength,last_recalled_at,source_episode_id) VALUES(?,?,?,'semantic',0.7,"
                       "?,?,?, ?,0,0.7,NULL,NULL)",
                       (MEMORY, "synthetic independent cognition", "synthetic independent cognition",
                        CONVERSATION, message_id, "2026-09-01T00:00:02Z", "2026-09-01T00:00:02Z"))
    connection.commit()
    connection.close()
    identity = {"persona_id": persona_id, "identity_text": identity_text}
    (path.parent / "identity.json").write_text(canonical_json(identity), encoding="utf-8")
    (path.parent / "persona_config.json").write_text(
        '{"persona_display_name":"Synthetic","timezone":"UTC",'
        '"suggested_user_display_name":"Synthetic User"}', encoding="utf-8")
    return path


def mutate(path: Path, statement: str, parameters: tuple = ()) -> None:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(statement, parameters)
    connection.commit()
    connection.close()


class PersonaSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="m71-sync-", dir=ROOT / "tests")
        self.root = Path(self.temp.name)
        self.db_a = create_persona_db(self.root / "device-a" / "personas" / PERSONA_A / "mindcore.db",
                                      PERSONA_A)
        self.db_b = create_persona_db(self.root / "device-b" / "personas" / PERSONA_A / "mindcore.db",
                                      PERSONA_A)
        self.meta_a = self.root / "device-a" / "sync" / "metadata.sqlite"
        self.meta_b = self.root / "device-b" / "sync" / "metadata.sqlite"
        self.engine_a = PersonaSyncEngine(self.db_a, self.meta_a, DEVICE_A)
        self.engine_b = PersonaSyncEngine(self.db_b, self.meta_b, DEVICE_B)

    def tearDown(self):
        self.temp.cleanup()

    def test_canonical_json_hash_is_stable_and_binary_is_unambiguous(self):
        one = {"z": [2, None], "a": {"beta": 3, "alpha": "한글"}, "binary": b"abc"}
        two = {"binary": b"abc", "a": {"alpha": "한글", "beta": 3}, "z": [2, None]}
        self.assertEqual(canonical_json(one), canonical_json(two))
        self.assertEqual(canonical_hash(one), canonical_hash(two))
        self.assertIn('"$binary_base64":"YWJj"', canonical_json(one))

    def test_policy_is_explicit_and_excludes_secret_and_runtime_entities(self):
        self.assertEqual("SYNC_APPEND_ONLY", SYNC_ENTITY_POLICIES["messages"].policy)
        self.assertEqual("SYNC_MUTABLE", SYNC_ENTITY_POLICIES["relationship"].policy)
        self.assertEqual("RUNTIME_TRANSIENT", NON_SYNC_ENTITY_POLICIES["chat_turn_stages"])
        self.assertEqual("SECRET_LOCAL_ONLY",
                         NON_SYNC_ENTITY_POLICIES["provider credential ID and AndroidKeyStore values"])
        first = self.engine_a.scan(PERSONA_A).manifest
        kinds = {record.entity_kind for record in first.records}
        self.assertNotIn("chat_turns", kinds)
        self.assertNotIn("chat_turn_stages", kinds)
        self.assertNotIn("schema_metadata", kinds)
        self.assertIn("messages", kinds)

    def test_initial_baseline_is_read_only_deterministic_and_semantically_shared(self):
        first_a = self.engine_a.scan(PERSONA_A)
        first_b = self.engine_b.scan(PERSONA_A)
        self.assertEqual((), first_a.delta.entries)
        self.assertEqual((), first_b.delta.entries)
        self.assertTrue(manifest_semantically_equal(first_a.manifest, first_b.manifest))
        self.assertEqual(first_a.manifest.as_dict()["records"],
                         self.engine_a.scan(PERSONA_A).manifest.as_dict()["records"])
        self.assertEqual(0, len(self.engine_a.scan(PERSONA_A).delta.entries))
        connection = sqlite3.connect(self.db_a)
        self.assertEqual("24", connection.execute(
            "SELECT value FROM schema_metadata WHERE key='turso_baseline_version'").fetchone()[0])
        connection.close()

    def test_new_message_is_the_only_append_delta_then_unchanged_is_empty(self):
        self.engine_a.scan(PERSONA_A)
        new_message = "74000000-0000-4000-8000-000000000002"
        mutate(self.db_a, "INSERT INTO messages(id,conversation_id,sequence,role,content,source_device,created_at) "
               "VALUES(?,?,2,'user','synthetic new message','synthetic','2026-09-02T00:00:00Z')",
               (new_message, CONVERSATION))
        changed = self.engine_a.scan(PERSONA_A)
        self.assertEqual([("created", "messages", new_message)], [
            (item.change, item.record.entity_kind, item.record.entity_id) for item in changed.delta.entries])
        self.assertEqual((), self.engine_a.scan(PERSONA_A).delta.entries)
        self.assertEqual((), self.engine_b.scan(PERSONA_A).delta.entries)

    def test_mutable_record_update_gets_logical_revision(self):
        baseline = self.engine_a.scan(PERSONA_A).manifest
        before = next(item for item in baseline.records if item.entity_kind == "relationship")
        mutate(self.db_a, "UPDATE relationship SET trust=0.8,updated_at='2099-01-01T00:00:00Z' WHERE id=1")
        changed = self.engine_a.scan(PERSONA_A)
        self.assertEqual(1, len(changed.delta.entries))
        entry = changed.delta.entries[0]
        self.assertEqual("updated", entry.change)
        self.assertEqual("relationship", entry.record.entity_kind)
        self.assertEqual(DEVICE_A, entry.record.revision.origin_device_id)
        self.assertGreater(entry.record.revision.counter, 0)
        self.assertEqual(before.content_hash, entry.record.revision.parent_hash)

    def test_local_only_metadata_and_recovery_mutation_do_not_change_manifest(self):
        before = self.engine_a.scan(PERSONA_A).manifest
        mutate(self.db_a, "UPDATE schema_metadata SET value='other-install' WHERE key='android_installation_id'")
        mutate(self.db_a, "INSERT INTO chat_turns(turn_id,conversation_id,status,created_at,updated_at) "
               "VALUES('76000000-0000-4000-8000-000000000001',?,'pending',?,?)",
               (CONVERSATION, "2026-09-02T00:00:00Z", "2026-09-02T00:00:00Z"))
        after = self.engine_a.scan(PERSONA_A)
        self.assertTrue(manifest_semantically_equal(before, after.manifest))
        self.assertEqual((), after.delta.entries)

    def test_hard_delete_generates_durable_tombstone_without_cognition_cascade(self):
        baseline = self.engine_a.scan(PERSONA_A).manifest
        mutate(self.db_a, "DELETE FROM messages WHERE id=?", (MESSAGE,))
        result = self.engine_a.scan(PERSONA_A)
        changes = {(item.change, item.record.entity_kind, item.record.entity_id)
                   for item in result.delta.entries}
        self.assertEqual({("deleted", "messages", MESSAGE), ("updated", "memories", MEMORY)}, changes)
        self.assertTrue(next(item.record for item in result.delta.entries
                             if item.record.entity_kind == "messages").tombstone)
        message = next(item for item in result.manifest.records
                       if item.entity_kind == "messages" and item.entity_id == MESSAGE)
        self.assertTrue(message.tombstone)
        memory = next(item for item in result.manifest.records
                      if item.entity_kind == "memories" and item.entity_id == MEMORY)
        self.assertFalse(memory.tombstone)
        db = sqlite3.connect(self.db_a)
        self.assertEqual(1, db.execute("SELECT count(*) FROM memories WHERE memory_id=?", (MEMORY,)).fetchone()[0])
        self.assertIsNone(db.execute("SELECT source_message_id FROM memories WHERE memory_id=?",
                                     (MEMORY,)).fetchone()[0])
        db.close()
        delta = generate_delta(baseline, result.manifest, PERSONA_A)
        self.assertEqual({"deleted", "updated"}, {item.change for item in delta.entries})
        self.assertEqual((), self.engine_a.scan(PERSONA_A).delta.entries)

    def test_explicit_tombstone_is_sync_metadata_only(self):
        baseline = self.engine_a.scan(PERSONA_A).manifest
        tombstone = self.engine_a.create_tombstone(PERSONA_A, "messages", MESSAGE)
        self.assertTrue(tombstone.tombstone)
        db = sqlite3.connect(self.db_a)
        self.assertEqual(1, db.execute("SELECT count(*) FROM messages WHERE id=?", (MESSAGE,)).fetchone()[0])
        db.close()
        current = self.engine_a.scan(PERSONA_A)
        self.assertTrue(next(record for record in current.manifest.records
                             if record.entity_kind == "messages" and record.entity_id == MESSAGE).tombstone)
        self.assertEqual(["deleted"], [item.change for item in generate_delta(
            baseline, current.manifest, PERSONA_A).entries])

    def test_independent_append_between_devices_is_not_a_conflict(self):
        base_a = self.engine_a.scan(PERSONA_A).manifest
        base_b = self.engine_b.scan(PERSONA_A).manifest
        self.assertTrue(manifest_semantically_equal(base_a, base_b))
        message_b = "74000000-0000-4000-8000-000000000003"
        mutate(self.db_b, "INSERT INTO messages(id,conversation_id,sequence,role,content,source_device,created_at) "
               "VALUES(?,?,2,'user','independent B event','synthetic','2026-09-02T00:00:00Z')",
               (message_b, CONVERSATION))
        current_b = self.engine_b.scan(PERSONA_A)
        conflicts = detect_conflicts(self.engine_a.scan(PERSONA_A).manifest, current_b.manifest)
        self.assertEqual((), conflicts)
        self.assertEqual([message_b], [item.record.entity_id for item in current_b.delta.entries])

    def test_same_id_append_payload_mismatch_is_hard_conflict(self):
        base = self.engine_a.scan(PERSONA_A).manifest
        message = next(item for item in base.records if item.entity_kind == "messages")
        changed_payload = copy.deepcopy(message.canonical_payload)
        changed_payload["content"] = "different synthetic content"
        changed = SyncRecord(PERSONA_A, "messages", message.entity_id, DEVICE_B,
            changed_payload, canonical_hash(changed_payload), SyncRevision(DEVICE_B, 1, message.content_hash))
        remote_records = tuple(sorted((changed if item.entity_kind == "messages" and item.entity_id == message.entity_id
                                       else item for item in base.records),
                                      key=lambda item: (item.entity_kind, item.entity_id)))
        remote = SyncManifest(PERSONA_A, DEVICE_B, remote_records)
        conflicts = detect_conflicts(base, remote)
        self.assertEqual("IDENTITY_PAYLOAD_CONFLICT", conflicts[0].code)
        delta = generate_delta(base, remote, PERSONA_A)
        self.assertEqual((), delta.entries)
        self.assertEqual("IDENTITY_PAYLOAD_CONFLICT", delta.conflicts[0].code)

    def test_concurrent_mutable_changes_share_ancestor_and_do_not_lww_merge(self):
        base_a = self.engine_a.scan(PERSONA_A).manifest
        base_b = self.engine_b.scan(PERSONA_A).manifest
        self.assertTrue(manifest_semantically_equal(base_a, base_b))
        mutate(self.db_a, "UPDATE relationship SET trust=0.6 WHERE id=1")
        mutate(self.db_b, "UPDATE relationship SET trust=0.9 WHERE id=1")
        changed_a = self.engine_a.scan(PERSONA_A).manifest
        changed_b = self.engine_b.scan(PERSONA_A).manifest
        conflicts = detect_conflicts(changed_a, changed_b)
        self.assertEqual(["CONCURRENT_MUTATION"], [item.code for item in conflicts])
        conflict_delta = generate_delta(base_b, changed_a, PERSONA_A)
        self.assertEqual((), conflict_delta.conflicts)
        self.assertEqual(["updated"], [item.change for item in conflict_delta.entries])
        peer_delta = generate_delta(changed_b, changed_a, PERSONA_A)
        self.assertEqual(["CONCURRENT_MUTATION"], [item.code for item in peer_delta.conflicts])
        self.assertEqual((), peer_delta.entries)

    def _message_record(self, message_id: str, sequence: int, content: str) -> SyncRecord:
        payload = {
            "id": message_id, "conversation_id": CONVERSATION, "sequence": sequence,
            "role": "user", "content": content, "source_device": "synthetic",
            "created_at": f"2026-09-03T00:00:{sequence:02d}Z",
        }
        return SyncRecord(PERSONA_A, "messages", message_id, DEVICE_A, payload,
                          canonical_hash(payload), SyncRevision(DEVICE_A, sequence, None, None))

    def _delta(self, *records: SyncRecord) -> dict:
        entries = tuple(DeltaEntry("created", record) for record in sorted(
            records, key=lambda item: (item.entity_kind, item.entity_id)))
        return SyncDelta(PERSONA_A, DEVICE_A, entries).as_dict()

    def test_remote_append_apply_is_atomic_idempotent_and_checkpoints_after_domain_commit(self):
        self.engine_a.scan(PERSONA_A)
        self.engine_b.scan(PERSONA_A)
        new_id = "74000000-0000-4000-8000-000000000004"
        mutate(self.db_a,
               "INSERT INTO messages(id,conversation_id,sequence,role,content,source_device,created_at) "
               "VALUES(?,?,2,'user','remote append','synthetic','2026-09-03T00:00:02Z')",
               (new_id, CONVERSATION))
        outbound = self.engine_a.scan(PERSONA_A).delta.as_dict()
        first = json.loads(apply_remote_delta(self.db_b, self.root / "device-b", DEVICE_B,
            PERSONA_A, DEVICE_A, outbound))
        self.assertEqual(1, first["applied"])
        second = json.loads(apply_remote_delta(self.db_b, self.root / "device-b", DEVICE_B,
            PERSONA_A, DEVICE_A, outbound))
        self.assertEqual((0, 1), (second["applied"], second["idempotent"]))
        db = sqlite3.connect(self.db_b)
        self.assertEqual(1, db.execute("SELECT count(*) FROM messages WHERE id=?", (new_id,)).fetchone()[0])
        db.close()

    def test_synthetic_device_a_b_bidirectional_independent_append_converges(self):
        self.engine_a.scan(PERSONA_A)
        self.engine_b.scan(PERSONA_A)
        message_a = "74000000-0000-4000-8000-000000000031"
        mutate(self.db_a,
               "INSERT INTO messages(id,conversation_id,sequence,role,content,source_device,created_at) "
               "VALUES(?,?,2,'user','A independent event','synthetic','2026-09-03T00:00:02Z')",
               (message_a, CONVERSATION))
        delta_a = self.engine_a.scan(PERSONA_A).delta.as_dict()
        apply_remote_delta(self.db_b, self.root / "device-b", DEVICE_B,
                           PERSONA_A, DEVICE_A, delta_a)

        message_b = "74000000-0000-4000-8000-000000000032"
        mutate(self.db_b,
               "INSERT INTO messages(id,conversation_id,sequence,role,content,source_device,created_at) "
               "VALUES(?,?,3,'user','B independent event','synthetic','2026-09-03T00:00:03Z')",
               (message_b, CONVERSATION))
        delta_b = self.engine_b.scan(PERSONA_A).delta.as_dict()
        apply_remote_delta(self.db_a, self.root / "device-a", DEVICE_A,
                           PERSONA_A, DEVICE_B, delta_b)
        final_a = self.engine_a.scan(PERSONA_A).manifest
        final_b = self.engine_b.scan(PERSONA_A).manifest
        self.assertTrue(manifest_semantically_equal(final_a, final_b))
        self.assertEqual((), self.engine_a.scan(PERSONA_A).delta.entries)
        self.assertEqual((), self.engine_b.scan(PERSONA_A).delta.entries)

    def test_remote_mutable_descendant_applies_and_concurrent_change_is_not_overwritten(self):
        self.engine_a.scan(PERSONA_A)
        self.engine_b.scan(PERSONA_A)
        mutate(self.db_a, "UPDATE relationship SET trust=0.77 WHERE id=1")
        remote = self.engine_a.scan(PERSONA_A).delta.as_dict()
        result = json.loads(apply_remote_delta(self.db_b, self.root / "device-b", DEVICE_B,
            PERSONA_A, DEVICE_A, remote))
        self.assertEqual(1, result["applied"])
        db = sqlite3.connect(self.db_b)
        self.assertEqual(0.77, db.execute("SELECT trust FROM relationship WHERE id=1").fetchone()[0])
        db.close()

    def _persisted_conflicts(self, peer: str = DEVICE_A) -> list[dict]:
        with SyncMetadataStore(sync_metadata_path(self.root / "device-b"), DEVICE_B) as store:
            return store.list_conflicts(PERSONA_A)

    def test_apply_conflicts_are_durable_content_free_and_deduplicated(self):
        self.engine_a.scan(PERSONA_A)
        self.engine_b.scan(PERSONA_A)

        local_message = self.engine_b.scan(PERSONA_A).manifest
        original = next(item for item in local_message.records
                        if item.entity_kind == "messages" and item.entity_id == MESSAGE)
        changed_payload = copy.deepcopy(original.canonical_payload)
        changed_payload["content"] = "remote conflicting payload"
        changed = SyncRecord(PERSONA_A, "messages", MESSAGE, DEVICE_A,
                             changed_payload, canonical_hash(changed_payload),
                             SyncRevision(DEVICE_A, 1, original.content_hash))
        append_conflict = SyncDelta(PERSONA_A, DEVICE_A,
                                    (DeltaEntry("created", changed),)).as_dict()
        for _ in range(2):
            with self.assertRaises(SyncApplyConflict) as caught:
                apply_remote_delta(self.db_b, self.root / "device-b", DEVICE_B,
                                   PERSONA_A, DEVICE_A, append_conflict)
            self.assertEqual("IDENTITY_PAYLOAD_CONFLICT", caught.exception.code)
        records = self._persisted_conflicts()
        self.assertEqual(1, len(records))
        self.assertEqual("IDENTITY_PAYLOAD_CONFLICT", records[0]["conflict_type"])
        self.assertIn(records[0]["resolution_state"], {"UNRESOLVED", "RESOLVED"})
        self.assertNotIn("remote conflicting payload", canonical_json(records))
        first_seen = records[0]["first_seen_ms"]
        self.assertGreaterEqual(records[0]["last_seen_ms"], first_seen)

        # Reopen the sidecar to model process restart; the conflict identity and
        # unresolved state survive without retaining any domain payload.
        restarted = self._persisted_conflicts()
        self.assertEqual(records[0]["conflict_id"], restarted[0]["conflict_id"])
        self.assertEqual("UNRESOLVED", restarted[0]["resolution_state"])

    def test_concurrent_mutable_conflict_is_durably_recorded(self):
        self.engine_a.scan(PERSONA_A)
        self.engine_b.scan(PERSONA_A)
        mutate(self.db_a, "UPDATE relationship SET trust=0.73 WHERE id=1")
        remote = self.engine_a.scan(PERSONA_A).delta.as_dict()
        mutate(self.db_b, "UPDATE relationship SET trust=0.91 WHERE id=1")
        with self.assertRaises(SyncApplyConflict) as caught:
            apply_remote_delta(self.db_b, self.root / "device-b", DEVICE_B,
                               PERSONA_A, DEVICE_A, remote)
        self.assertEqual("CONCURRENT_MUTATION", caught.exception.code)
        records = self._persisted_conflicts()
        self.assertEqual(1, len(records))
        self.assertEqual("relationship", records[0]["entity_kind"])
        self.assertTrue(records[0]["local_revision"])
        self.assertTrue(records[0]["remote_revision"])
        self.assertNotIn("0.91", canonical_json(records))

    def test_concurrent_tombstone_conflict_is_durable_and_preserves_domain_row(self):
        self.engine_a.scan(PERSONA_A)
        self.engine_b.scan(PERSONA_A)
        tombstone = self.engine_a.create_tombstone(PERSONA_A, "messages", MESSAGE)
        delta = SyncDelta(PERSONA_A, DEVICE_A,
                          (DeltaEntry("deleted", tombstone),)).as_dict()
        mutate(self.db_b, "UPDATE messages SET content='local concurrent edit' WHERE id=?", (MESSAGE,))
        with self.assertRaises(SyncApplyConflict) as caught:
            apply_remote_delta(self.db_b, self.root / "device-b", DEVICE_B,
                               PERSONA_A, DEVICE_A, delta)
        self.assertEqual("TOMBSTONE_CONFLICT", caught.exception.code)
        db = sqlite3.connect(self.db_b)
        self.assertEqual("local concurrent edit", db.execute(
            "SELECT content FROM messages WHERE id=?", (MESSAGE,)).fetchone()[0])
        db.close()
        records = self._persisted_conflicts()
        self.assertEqual(1, len(records))
        self.assertEqual("TOMBSTONE_CONFLICT", records[0]["conflict_type"])
        self.assertNotIn("local concurrent edit", canonical_json(records))

        mutate(self.db_a, "UPDATE relationship SET trust=0.81 WHERE id=1")
        conflict_delta = self.engine_a.scan(PERSONA_A).delta.as_dict()
        mutate(self.db_b, "UPDATE relationship SET trust=0.91 WHERE id=1")
        with self.assertRaises(SyncApplyConflict) as caught:
            apply_remote_delta(self.db_b, self.root / "device-b", DEVICE_B,
                               PERSONA_A, DEVICE_A, conflict_delta)
        self.assertEqual("CONCURRENT_MUTATION", caught.exception.code)
        db = sqlite3.connect(self.db_b)
        self.assertEqual(0.91, db.execute("SELECT trust FROM relationship WHERE id=1").fetchone()[0])
        db.close()

    def test_remote_tombstone_is_idempotent_and_does_not_cascade_delete(self):
        self.engine_a.scan(PERSONA_A)
        self.engine_b.scan(PERSONA_A)
        tombstone = self.engine_a.create_tombstone(PERSONA_A, "messages", MESSAGE)
        delta = SyncDelta(PERSONA_A, DEVICE_A, (DeltaEntry("deleted", tombstone),)).as_dict()
        result = json.loads(apply_remote_delta(self.db_b, self.root / "device-b", DEVICE_B,
            PERSONA_A, DEVICE_A, delta))
        self.assertEqual("APPLIED", result["status"])
        db = sqlite3.connect(self.db_b)
        self.assertEqual(1, db.execute("SELECT count(*) FROM messages WHERE id=?", (MESSAGE,)).fetchone()[0])
        self.assertEqual(1, db.execute("SELECT count(*) FROM memories WHERE memory_id=?", (MEMORY,)).fetchone()[0])
        db.close()
        scan = self.engine_b.scan(PERSONA_A)
        self.assertTrue(next(record for record in scan.manifest.records
                             if record.entity_kind == "messages" and record.entity_id == MESSAGE).tombstone)

    def test_concurrent_local_delete_and_remote_message_update_persist_tombstone_conflict(self):
        self.engine_a.scan(PERSONA_A)
        self.engine_b.scan(PERSONA_A)
        mutate(self.db_b, "UPDATE messages SET content='concurrent synthetic update' WHERE id=?",
               (MESSAGE,))
        remote_update = self.engine_b.scan(PERSONA_A).delta.as_dict()
        mutate(self.db_a, "DELETE FROM messages WHERE id=?", (MESSAGE,))
        local = self.engine_a.scan(PERSONA_A)
        self.assertTrue(any(entry.change == "deleted" and entry.record.entity_id == MESSAGE
                            for entry in local.delta.entries))

        with self.assertRaisesRegex(SyncApplyConflict, "CONCURRENT_DELETE_MUTATION"):
            apply_remote_delta(self.db_a, self.root / "device-a", DEVICE_A,
                               PERSONA_A, DEVICE_B, remote_update)

        with SyncMetadataStore(self.meta_a, DEVICE_A) as store:
            conflicts = store.list_conflicts(PERSONA_A)
        conflict = next(item for item in conflicts if item["entity_kind"] == "messages"
                        and item["entity_id"] == MESSAGE)
        self.assertEqual("TOMBSTONE_CONFLICT", conflict["conflict_type"])

    def test_remote_delta_validation_is_atomic_and_wrong_persona_is_rejected(self):
        self.engine_b.scan(PERSONA_A)
        first = self._message_record("74000000-0000-4000-8000-000000000011", 2, "would insert")
        second = self._message_record("74000000-0000-4000-8000-000000000012", 1, "unique violation")
        with self.assertRaises(sqlite3.IntegrityError):
            apply_remote_delta(self.db_b, self.root / "device-b", DEVICE_B,
                PERSONA_A, DEVICE_A, self._delta(first, second))
        db = sqlite3.connect(self.db_b)
        self.assertEqual(0, db.execute("SELECT count(*) FROM messages WHERE id=?",
                                       (first.entity_id,)).fetchone()[0])
        db.close()

        wrong = SyncDelta(PERSONA_B, DEVICE_A, ()).as_dict()
        with self.assertRaises(SyncError):
            apply_remote_delta(self.db_b, self.root / "device-b", DEVICE_B,
                               PERSONA_A, DEVICE_A, wrong)

    def test_domain_commit_before_checkpoint_recovers_by_idempotent_retry(self):
        self.engine_a.scan(PERSONA_A)
        self.engine_b.scan(PERSONA_A)
        new_id = "74000000-0000-4000-8000-000000000021"
        mutate(self.db_a,
               "INSERT INTO messages(id,conversation_id,sequence,role,content,source_device,created_at) "
               "VALUES(?,?,2,'user','checkpoint recovery','synthetic','2026-09-03T00:00:02Z')",
               (new_id, CONVERSATION))
        outbound = self.engine_a.scan(PERSONA_A).delta.as_dict()
        with mock.patch("app.services.persona_sync.SyncMetadataStore.checkpoint_remote_records",
                        side_effect=RuntimeError("synthetic checkpoint crash")):
            with self.assertRaises(RuntimeError):
                apply_remote_delta(self.db_b, self.root / "device-b", DEVICE_B,
                                   PERSONA_A, DEVICE_A, outbound)
        db = sqlite3.connect(self.db_b)
        self.assertEqual(1, db.execute("SELECT count(*) FROM messages WHERE id=?", (new_id,)).fetchone()[0])
        db.close()

        recovered = json.loads(apply_remote_delta(self.db_b, self.root / "device-b", DEVICE_B,
            PERSONA_A, DEVICE_A, outbound))
        self.assertEqual((0, 1), (recovered["applied"], recovered["idempotent"]))
        db = sqlite3.connect(self.db_b)
        self.assertEqual(1, db.execute("SELECT count(*) FROM messages WHERE id=?", (new_id,)).fetchone()[0])
        db.close()

    def test_mutable_revision_retains_common_ancestor_across_multiple_local_updates(self):
        base_a = self.engine_a.scan(PERSONA_A).manifest
        base_b = self.engine_b.scan(PERSONA_A).manifest
        baseline_relationship = next(item for item in base_b.records if item.entity_kind == "relationship")
        mutate(self.db_a, "UPDATE relationship SET trust=0.5 WHERE id=1")
        self.engine_a.scan(PERSONA_A)
        mutate(self.db_a, "UPDATE relationship SET trust=0.6 WHERE id=1")
        current_a = self.engine_a.scan(PERSONA_A).manifest
        current_relationship = next(item for item in current_a.records if item.entity_kind == "relationship")
        self.assertEqual(baseline_relationship.content_hash, current_relationship.revision.ancestor_hash)
        self.assertEqual((), detect_conflicts(current_a, base_b))
        self.assertTrue(manifest_semantically_equal(base_a, base_b))

    def test_wrong_persona_scan_manifest_and_delta_are_rejected(self):
        with self.assertRaisesRegex(SyncError, "WRONG_PERSONA"):
            self.engine_a.scan(PERSONA_B)
        baseline = self.engine_a.scan(PERSONA_A).manifest
        with self.assertRaisesRegex(SyncError, "WRONG_PERSONA"):
            generate_delta(baseline, baseline, PERSONA_B)
        value = baseline.as_dict()
        value["persona_id"] = PERSONA_B
        value["manifest_hash"] = canonical_hash({key: value[key] for key in value if key != "manifest_hash"})
        with self.assertRaisesRegex(SyncError, "WRONG_PERSONA"):
            validate_manifest(value, PERSONA_A)
        delta = generate_delta(baseline, baseline, PERSONA_A).as_dict()
        delta["persona_id"] = PERSONA_B
        delta["delta_hash"] = canonical_hash({key: delta[key] for key in delta if key != "delta_hash"})
        with self.assertRaisesRegex(SyncError, "WRONG_PERSONA"):
            validate_delta(delta, PERSONA_A)

    def test_unknown_manifest_and_record_extensions_are_safely_rejected(self):
        value = self.engine_a.scan(PERSONA_A).manifest.as_dict()
        value["future_transport_hint"] = "ignored-by-no-one"
        value["manifest_hash"] = canonical_hash({key: value[key] for key in value
                                                 if key != "manifest_hash"})
        with self.assertRaisesRegex(SyncError, "MANIFEST_FIELDS_UNSUPPORTED"):
            validate_manifest(value, PERSONA_A)
        value = self.engine_a.scan(PERSONA_A).manifest.as_dict()
        value["records"][0]["unrecognized_field"] = "safe-reject"
        value["manifest_hash"] = canonical_hash({key: value[key] for key in value
                                                 if key != "manifest_hash"})
        with self.assertRaisesRegex(SyncError, "RECORD_FIELDS_UNSUPPORTED"):
            validate_manifest(value, PERSONA_A)

    def test_persona_a_and_b_sidecar_baselines_and_manifest_records_are_isolated(self):
        db_b = create_persona_db(self.root / "device-a" / "personas" / PERSONA_B / "mindcore.db",
                                 PERSONA_B, message_id="74000000-0000-4000-8000-000000000099",
                                 identity_text="Synthetic Persona B identity")
        engine_b = PersonaSyncEngine(db_b, self.meta_a, DEVICE_A)
        manifest_a = self.engine_a.scan(PERSONA_A).manifest
        manifest_b = engine_b.scan(PERSONA_B).manifest
        self.assertEqual({PERSONA_A}, {item.persona_id for item in manifest_a.records})
        self.assertEqual({PERSONA_B}, {item.persona_id for item in manifest_b.records})
        mutate(self.db_a, "UPDATE relationship SET trust=0.8 WHERE id=1")
        self.assertEqual(1, len(self.engine_a.scan(PERSONA_A).delta.entries))
        self.assertEqual((), engine_b.scan(PERSONA_B).delta.entries)

    def test_metadata_wipe_and_corruption_rebuild_baseline_without_persona_data_loss(self):
        before = self.engine_a.scan(PERSONA_A).manifest
        for suffix in ("", "-wal", "-shm"):
            path = Path(str(self.meta_a) + suffix)
            if path.exists():
                path.unlink()
        wiped = self.engine_a.scan(PERSONA_A)
        self.assertEqual((), wiped.delta.entries)
        self.assertTrue(manifest_semantically_equal(before, wiped.manifest))
        self.meta_a.write_bytes(b"synthetic damaged sync metadata")
        recovered = self.engine_a.scan(PERSONA_A)
        self.assertTrue(recovered.metadata_recovered)
        self.assertEqual((), recovered.delta.entries)
        self.assertTrue(manifest_semantically_equal(before, recovered.manifest))
        db = sqlite3.connect(self.db_a)
        self.assertEqual(1, db.execute("SELECT count(*) FROM messages WHERE id=?", (MESSAGE,)).fetchone()[0])
        self.assertEqual(1, db.execute("SELECT count(*) FROM memories WHERE memory_id=?", (MEMORY,)).fetchone()[0])
        db.close()

    def test_wrong_device_sidecar_is_not_accepted_as_same_installation(self):
        self.engine_a.scan(PERSONA_A)
        with self.assertRaisesRegex(SyncError, "SYNC_METADATA_DEVICE_MISMATCH"):
            PersonaSyncEngine(self.db_a, self.meta_a, DEVICE_B).scan(PERSONA_A)

    def test_remote_seen_is_metadata_only_and_wrong_persona_rejects(self):
        local = self.engine_a.scan(PERSONA_A).manifest
        self.engine_a.record_remote_seen(self.engine_b.scan(PERSONA_A).manifest, PERSONA_A)
        with self.assertRaisesRegex(SyncError, "WRONG_PERSONA"):
            self.engine_a.record_remote_seen(local, PERSONA_B)
        after = self.engine_a.scan(PERSONA_A)
        self.assertEqual((), after.delta.entries)
        self.assertTrue(manifest_semantically_equal(local, after.manifest))

    def test_mutable_resolution_is_syncable_and_replay_safe(self):
        self.engine_a.scan(PERSONA_A)
        self.engine_b.scan(PERSONA_A)
        mutate(self.db_a, "UPDATE relationship SET trust=0.8 WHERE id=1")
        mutate(self.db_b, "UPDATE relationship SET trust=0.9 WHERE id=1")
        delta_a = self.engine_a.scan(PERSONA_A).delta.as_dict()
        delta_b = self.engine_b.scan(PERSONA_A).delta.as_dict()
        with self.assertRaises(SyncApplyConflict):
            apply_remote_delta(self.db_a, self.root / "device-a", DEVICE_A,
                               PERSONA_A, DEVICE_B, delta_b)
        with self.assertRaises(SyncApplyConflict):
            apply_remote_delta(self.db_b, self.root / "device-b", DEVICE_B,
                               PERSONA_A, DEVICE_A, delta_a)
        with SyncMetadataStore(self.meta_b, DEVICE_B) as store:
            conflict = store.list_conflicts(PERSONA_A)[0]
        resolved = json.loads(resolve_conflict_json(
            self.db_b, self.root / "device-b", DEVICE_B, PERSONA_A,
            conflict["conflict_id"], "KEEP_THIS_DEVICE_VERSION", canonical_json(delta_a)))
        self.assertEqual("RESOLVED", resolved["status"])
        outbound = self.engine_b.scan(PERSONA_A).delta.as_dict()
        self.assertEqual(1, len(outbound["resolutions"]))
        first = json.loads(apply_remote_delta(self.db_a, self.root / "device-a",
            DEVICE_A, PERSONA_A, DEVICE_B, outbound))
        second = json.loads(apply_remote_delta(self.db_a, self.root / "device-a",
            DEVICE_A, PERSONA_A, DEVICE_B, outbound))
        self.assertEqual(1, first["applied"])
        self.assertEqual(0, second["applied"])
        self.assertEqual(0, len(self.engine_a.scan(PERSONA_A).delta.entries))
        with SyncMetadataStore(self.meta_a, DEVICE_A) as store:
            self.assertEqual([], store.list_conflicts(PERSONA_A))
            self.assertEqual(1, len(store.resolution_rows(PERSONA_A)))

    def test_tombstone_resolution_preserves_independent_cognition(self):
        self.engine_a.scan(PERSONA_A)
        self.engine_b.scan(PERSONA_A)
        deleted = self.engine_a.create_tombstone(PERSONA_A, "relationship", "1")
        delta_a = SyncDelta(PERSONA_A, DEVICE_A, (DeltaEntry("deleted", deleted),)).as_dict()
        mutate(self.db_b, "UPDATE relationship SET trust=0.9 WHERE id=1")
        delta_b = self.engine_b.scan(PERSONA_A).delta.as_dict()
        with self.assertRaises(SyncApplyConflict):
            apply_remote_delta(self.db_a, self.root / "device-a", DEVICE_A,
                               PERSONA_A, DEVICE_B, delta_b)
        with self.assertRaises(SyncApplyConflict):
            apply_remote_delta(self.db_b, self.root / "device-b", DEVICE_B,
                               PERSONA_A, DEVICE_A, delta_a)
        with SyncMetadataStore(self.meta_b, DEVICE_B) as store:
            conflict = store.list_conflicts(PERSONA_A)[0]
        resolve_conflict_json(self.db_b, self.root / "device-b", DEVICE_B, PERSONA_A,
                              conflict["conflict_id"], "DELETE_ON_BOTH_DEVICES",
                              canonical_json(delta_a))
        outbound = self.engine_b.scan(PERSONA_A).delta.as_dict()
        apply_remote_delta(self.db_a, self.root / "device-a", DEVICE_A,
                           PERSONA_A, DEVICE_B, outbound)
        with SyncMetadataStore(self.meta_a, DEVICE_A) as store:
            self.assertTrue(store.local_tombstone(PERSONA_A, "relationship", "1"))
        self.assertEqual((), self.engine_a.scan(PERSONA_A).delta.conflicts)
        self.assertEqual((), self.engine_b.scan(PERSONA_A).delta.conflicts)
        db = sqlite3.connect(self.db_a)
        self.assertEqual(1, db.execute("SELECT count(*) FROM memories").fetchone()[0])
        db.close()

    def test_competing_resolutions_need_second_explicit_choice(self):
        self.engine_a.scan(PERSONA_A)
        self.engine_b.scan(PERSONA_A)
        mutate(self.db_a, "UPDATE relationship SET trust=0.8 WHERE id=1")
        mutate(self.db_b, "UPDATE relationship SET trust=0.9 WHERE id=1")
        delta_a = self.engine_a.scan(PERSONA_A).delta.as_dict()
        delta_b = self.engine_b.scan(PERSONA_A).delta.as_dict()
        for db, root, device, peer, delta in (
            (self.db_a, self.root / "device-a", DEVICE_A, DEVICE_B, delta_b),
            (self.db_b, self.root / "device-b", DEVICE_B, DEVICE_A, delta_a),
        ):
            with self.assertRaises(SyncApplyConflict):
                apply_remote_delta(db, root, device, PERSONA_A, peer, delta)
        with SyncMetadataStore(self.meta_a, DEVICE_A) as store:
            conflict_a = store.list_conflicts(PERSONA_A)[0]["conflict_id"]
        with SyncMetadataStore(self.meta_b, DEVICE_B) as store:
            conflict_b = store.list_conflicts(PERSONA_A)[0]["conflict_id"]
        resolve_conflict_json(self.db_a, self.root / "device-a", DEVICE_A, PERSONA_A,
                              conflict_a, "KEEP_THIS_DEVICE_VERSION", canonical_json(delta_b))
        resolve_conflict_json(self.db_b, self.root / "device-b", DEVICE_B, PERSONA_A,
                              conflict_b, "KEEP_THIS_DEVICE_VERSION", canonical_json(delta_a))
        resolution_a = self.engine_a.scan(PERSONA_A).delta.as_dict()
        resolution_b = self.engine_b.scan(PERSONA_A).delta.as_dict()
        with self.assertRaisesRegex(SyncApplyConflict, "RESOLUTION_CONFLICT"):
            apply_remote_delta(self.db_a, self.root / "device-a", DEVICE_A,
                               PERSONA_A, DEVICE_B, resolution_b)
        with self.assertRaisesRegex(SyncApplyConflict, "RESOLUTION_CONFLICT"):
            apply_remote_delta(self.db_b, self.root / "device-b", DEVICE_B,
                               PERSONA_A, DEVICE_A, resolution_a)
        with SyncMetadataStore(self.meta_a, DEVICE_A) as store:
            conflict = next(item for item in store.list_conflicts(PERSONA_A)
                            if item["conflict_type"] == "RESOLUTION_CONFLICT")
        resolve_conflict_json(self.db_a, self.root / "device-a", DEVICE_A, PERSONA_A,
                              conflict["conflict_id"], "USE_PEER_VERSION",
                              canonical_json(resolution_b))
        final = self.engine_a.scan(PERSONA_A).delta.as_dict()
        apply_remote_delta(self.db_b, self.root / "device-b", DEVICE_B,
                           PERSONA_A, DEVICE_A, final)
        with SyncMetadataStore(self.meta_b, DEVICE_B) as store:
            self.assertEqual([], store.list_conflicts(PERSONA_A))

    def test_resolution_checkpoint_retry_and_stale_reject(self):
        self.engine_a.scan(PERSONA_A)
        self.engine_b.scan(PERSONA_A)
        mutate(self.db_a, "UPDATE relationship SET trust=0.8 WHERE id=1")
        mutate(self.db_b, "UPDATE relationship SET trust=0.9 WHERE id=1")
        delta_a = self.engine_a.scan(PERSONA_A).delta.as_dict()
        delta_b = self.engine_b.scan(PERSONA_A).delta.as_dict()
        with self.assertRaises(SyncApplyConflict):
            apply_remote_delta(self.db_a, self.root / "device-a", DEVICE_A,
                               PERSONA_A, DEVICE_B, delta_b)
        with self.assertRaises(SyncApplyConflict):
            apply_remote_delta(self.db_b, self.root / "device-b", DEVICE_B,
                               PERSONA_A, DEVICE_A, delta_a)
        with SyncMetadataStore(self.meta_b, DEVICE_B) as store:
            conflict = store.list_conflicts(PERSONA_A)[0]
        resolve_conflict_json(self.db_b, self.root / "device-b", DEVICE_B, PERSONA_A,
                              conflict["conflict_id"], "KEEP_THIS_DEVICE_VERSION",
                              canonical_json(delta_a))
        resolution = self.engine_b.scan(PERSONA_A).delta.as_dict()
        with mock.patch("app.services.persona_sync.SyncMetadataStore.record_resolution",
                        side_effect=RuntimeError("synthetic checkpoint crash")):
            with self.assertRaises(RuntimeError):
                apply_remote_delta(self.db_a, self.root / "device-a", DEVICE_A,
                                   PERSONA_A, DEVICE_B, resolution)
        recovered = json.loads(apply_remote_delta(self.db_a, self.root / "device-a",
            DEVICE_A, PERSONA_A, DEVICE_B, resolution))
        self.assertEqual(0, recovered["applied"])
        with SyncMetadataStore(self.meta_a, DEVICE_A) as store:
            self.assertEqual(1, len(store.resolution_rows(PERSONA_A)))
        source = resolution["resolutions"][0]["result"]
        unrelated = ("0" * 64, "1" * 64)
        record = SyncRecord(PERSONA_A, "relationship", "1", DEVICE_B,
                            source["canonical_payload"], source["content_hash"],
                            SyncRevision(DEVICE_B, 99, *unrelated))
        stale = make_resolution(PERSONA_A, "relationship", "1", unrelated, record)
        with self.assertRaisesRegex(SyncError, "STALE_RESOLUTION"):
            apply_remote_delta(self.db_a, self.root / "device-a", DEVICE_A,
                               PERSONA_A, DEVICE_B,
                               SyncDelta(PERSONA_A, DEVICE_B, (), (), 1,
                                         (stale,)).as_dict())

    def test_local_resolution_domain_commit_retries_checkpoint(self):
        self.engine_a.scan(PERSONA_A)
        self.engine_b.scan(PERSONA_A)
        mutate(self.db_a, "UPDATE relationship SET trust=0.8 WHERE id=1")
        mutate(self.db_b, "UPDATE relationship SET trust=0.9 WHERE id=1")
        delta_b = self.engine_b.scan(PERSONA_A).delta.as_dict()
        with self.assertRaises(SyncApplyConflict):
            apply_remote_delta(self.db_a, self.root / "device-a", DEVICE_A,
                               PERSONA_A, DEVICE_B, delta_b)
        with SyncMetadataStore(self.meta_a, DEVICE_A) as store:
            conflict_id = store.list_conflicts(PERSONA_A)[0]["conflict_id"]
        arguments = (self.db_a, self.root / "device-a", DEVICE_A, PERSONA_A,
                     conflict_id, "USE_PEER_VERSION", canonical_json(delta_b))
        with mock.patch("app.services.persona_sync.SyncMetadataStore.record_resolution",
                        side_effect=RuntimeError("synthetic checkpoint crash")):
            with self.assertRaises(RuntimeError):
                resolve_conflict_json(*arguments)
        db = sqlite3.connect(self.db_a)
        self.assertEqual(0.9, db.execute("SELECT trust FROM relationship WHERE id=1").fetchone()[0])
        db.close()
        self.assertEqual("RESOLVED", json.loads(resolve_conflict_json(*arguments))["status"])
        with SyncMetadataStore(self.meta_a, DEVICE_A) as store:
            self.assertEqual(1, len(store.resolution_rows(PERSONA_A)))
            self.assertEqual([], store.list_conflicts(PERSONA_A))

    def test_resolution_recovers_after_real_child_process_death(self):
        self.engine_a.scan(PERSONA_A)
        self.engine_b.scan(PERSONA_A)
        mutate(self.db_a, "UPDATE relationship SET trust=0.8 WHERE id=1")
        mutate(self.db_b, "UPDATE relationship SET trust=0.9 WHERE id=1")
        delta_b = self.engine_b.scan(PERSONA_A).delta.as_dict()
        with self.assertRaises(SyncApplyConflict):
            apply_remote_delta(self.db_a, self.root / "device-a", DEVICE_A,
                               PERSONA_A, DEVICE_B, delta_b)
        with SyncMetadataStore(self.meta_a, DEVICE_A) as store:
            conflict_id = store.list_conflicts(PERSONA_A)[0]["conflict_id"]
        arguments = (str(self.db_a), str(self.root / "device-a"), DEVICE_A,
                     PERSONA_A, conflict_id, "USE_PEER_VERSION", canonical_json(delta_b))
        child = ("import os,sys; from app.services import persona_sync as sync; "
                 "sync._debug_crash_barrier=lambda stage: os._exit(23) if "
                 "stage=='RESOLUTION_DOMAIN_COMMITTED' else None; "
                 "sync.resolve_conflict_json(*sys.argv[1:])")
        crashed = subprocess.run([sys.executable, "-c", child, *arguments],
                                 cwd=ROOT, capture_output=True, timeout=30, check=False)
        self.assertEqual(23, crashed.returncode)
        with sqlite3.connect(self.db_a) as db:
            self.assertEqual(0.9, db.execute(
                "SELECT trust FROM relationship WHERE id=1").fetchone()[0])
        with SyncMetadataStore(self.meta_a, DEVICE_A) as store:
            self.assertEqual(1, len(store.list_conflicts(PERSONA_A)))
            self.assertEqual([], store.resolution_rows(PERSONA_A))
        self.assertEqual("RESOLVED", json.loads(resolve_conflict_json(*arguments))["status"])
        with SyncMetadataStore(self.meta_a, DEVICE_A) as store:
            self.assertEqual([], store.list_conflicts(PERSONA_A))
            self.assertEqual(1, len(store.resolution_rows(PERSONA_A)))


if __name__ == "__main__":
    unittest.main()
