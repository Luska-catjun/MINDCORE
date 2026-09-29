"""Desktop counterpart for MindCore Persona Sync Protocol v1.

The sync engine reads and transactionally updates only an allowlist of schema-24
domain entities. Device-local hashes, revisions, trust, replay and conflicts
remain in sidecars, separate from Persona cognition authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import uuid
from typing import Any, Mapping

from app.database.schema_contract import REQUIRED_COLUMNS, REQUIRED_PRIMARY_KEYS


PROTOCOL_VERSION = 1
SCHEMA_VERSION = 24
BASELINE_ORIGIN_DEVICE_ID = "00000000-0000-0000-0000-000000000000"
MAX_RECORDS = 10_000
MAX_RECORD_BYTES = 1_048_576
MAX_MANIFEST_BYTES = 33_554_432
MAX_ID_BYTES = 256
MAX_IDENTITY_FILE_BYTES = 65_536
_FETCH_BATCH = 128


def _debug_crash_barrier(stage: str) -> None:
    """No-op seam; Desktop crash tests replace it only in a child process."""
    del stage


class SyncError(ValueError):
    """Safe, content-free sync foundation error."""


class SyncMetadataDeviceMismatch(SyncError):
    pass


class SyncApplyConflict(SyncError):
    """A safe remote apply conflict; no partial domain transaction is committed."""

    def __init__(self, code: str, entity_kind: str = "", entity_id: str = "",
                 *, local_hash: str | None = None, remote_hash: str | None = None,
                 local_revision: str | None = None, remote_revision: str | None = None):
        self.code = code
        self.entity_kind = entity_kind
        self.entity_id = entity_id
        self.local_hash = local_hash
        self.remote_hash = remote_hash
        self.local_revision = local_revision
        self.remote_revision = remote_revision
        super().__init__(code)


@dataclass(frozen=True)
class EntityPolicy:
    policy: str
    identity_column: str
    reason: str
    conflict_class: str


# This is the entire DB read allowlist. The columns for each selected table
# come from the frozen schema-24 baseline, never from arbitrary DB discovery.
SYNC_ENTITY_POLICIES: dict[str, EntityPolicy] = {
    "conversations": EntityPolicy("SYNC_MUTABLE", "conversation_id", "Conversation lifecycle can receive an end timestamp.", "CONCURRENT_MUTATION"),
    "decision_log": EntityPolicy("SYNC_MUTABLE", "id", "Decision status and resolution can change.", "CONCURRENT_MUTATION"),
    "diana_goals": EntityPolicy("SYNC_MUTABLE", "id", "Goal status, progress, confidence, and expiry evolve.", "CONCURRENT_MUTATION"),
    "diana_identity": EntityPolicy("SYNC_MUTABLE", "id", "Durable Persona identity profile.", "CONCURRENT_MUTATION"),
    "diana_knowledge": EntityPolicy("SYNC_MUTABLE", "knowledge_id", "Knowledge confidence and reinforcement metadata evolve.", "CONCURRENT_MUTATION"),
    "diana_knowledge_facts": EntityPolicy("SYNC_MUTABLE", "knowledge_fact_id", "Knowledge facts may be reinforced or contradicted.", "CONCURRENT_MUTATION"),
    "diana_narrative_evidence": EntityPolicy("SYNC_APPEND_ONLY", "id", "Durable evidence event; narrative projection is separate.", "IDENTITY_PAYLOAD_CONFLICT"),
    "diana_narratives": EntityPolicy("SYNC_MUTABLE", "id", "Narrative status, confidence, and counts are mutable.", "CONCURRENT_MUTATION"),
    "diana_need_events": EntityPolicy("SYNC_APPEND_ONLY", "id", "Durable need change event.", "IDENTITY_PAYLOAD_CONFLICT"),
    "diana_needs": EntityPolicy("SYNC_MUTABLE", "need_key", "Current per-Persona need snapshot.", "CONCURRENT_MUTATION"),
    "diana_preference_evidence": EntityPolicy("SYNC_APPEND_ONLY", "diana_preference_evidence_id", "Durable preference evidence event.", "IDENTITY_PAYLOAD_CONFLICT"),
    "diana_preferences": EntityPolicy("SYNC_MUTABLE", "diana_preference_id", "Preference confidence, status, and counts evolve.", "CONCURRENT_MUTATION"),
    "diana_response_intentions": EntityPolicy("SYNC_APPEND_ONLY", "id", "Completed-turn intention record; no executor state is included.", "IDENTITY_PAYLOAD_CONFLICT"),
    "diana_self_model": EntityPolicy("SYNC_MUTABLE", "id", "Self-model claim confidence and status evolve.", "CONCURRENT_MUTATION"),
    "diana_self_model_evidence": EntityPolicy("SYNC_APPEND_ONLY", "id", "Durable self-model evidence event.", "IDENTITY_PAYLOAD_CONFLICT"),
    "diana_state": EntityPolicy("SYNC_MUTABLE", "id", "Current cognition state snapshot.", "CONCURRENT_MUTATION"),
    "emotion_attributions": EntityPolicy("SYNC_APPEND_ONLY", "emotion_attribution_id", "Durable emotion attribution event.", "IDENTITY_PAYLOAD_CONFLICT"),
    "episodes": EntityPolicy("SYNC_MUTABLE", "episode_id", "Episode record has mutable recall, strength, and decay fields.", "CONCURRENT_MUTATION"),
    "experiences": EntityPolicy("SYNC_APPEND_ONLY", "experience_id", "Completed durable experience record.", "IDENTITY_PAYLOAD_CONFLICT"),
    "memories": EntityPolicy("SYNC_MUTABLE", "memory_id", "Memory strength and recall fields evolve.", "CONCURRENT_MUTATION"),
    "messages": EntityPolicy("SYNC_APPEND_ONLY", "id", "Durable conversation content with stable message identity.", "IDENTITY_PAYLOAD_CONFLICT"),
    "preference_evidence": EntityPolicy("SYNC_APPEND_ONLY", "evidence_id", "Durable preference evidence event.", "IDENTITY_PAYLOAD_CONFLICT"),
    "preferences": EntityPolicy("SYNC_MUTABLE", "preference_id", "Current preference state and evidence counts evolve.", "CONCURRENT_MUTATION"),
    "relationship": EntityPolicy("SYNC_MUTABLE", "id", "Current relationship snapshot.", "CONCURRENT_MUTATION"),
    "relationship_log": EntityPolicy("SYNC_APPEND_ONLY", "relationship_log_id", "Durable relationship change event.", "IDENTITY_PAYLOAD_CONFLICT"),
    "semantic_facts": EntityPolicy("SYNC_MUTABLE", "fact_id", "Semantic fact confidence and embeddings may be refreshed.", "CONCURRENT_MUTATION"),
    "state_log": EntityPolicy("SYNC_APPEND_ONLY", "id", "Durable state history event.", "IDENTITY_PAYLOAD_CONFLICT"),
    "persona.identity": EntityPolicy("SYNC_MUTABLE", "persona_id", "Validated identity.json content is part of the same Persona identity.", "CONCURRENT_MUTATION"),
}

# Explicit non-sync classifications from the audited schema-24 database and
# app state. The policy document contains the per-entity reasoning.
NON_SYNC_ENTITY_POLICIES: dict[str, str] = {
    "autonomy_executions": "RUNTIME_TRANSIENT",
    "chat_turn_stages": "RUNTIME_TRANSIENT",
    "chat_turns": "RUNTIME_TRANSIENT",
    "diana_working_memory_items": "RUNTIME_TRANSIENT",
    "schema_metadata": "DERIVED_SYNC_STATE",
    "personas.json registry selection": "LOCAL_ONLY",
    "provider/model preference in registry": "LOCAL_ONLY",
    "provider credential ID and AndroidKeyStore values": "SECRET_LOCAL_ONLY",
    "selected Persona and selected conversation": "LOCAL_ONLY",
    "composer draft, scroll position, theme, debug state": "LOCAL_ONLY",
    "identity.json device-independent file metadata": "DERIVED_SYNC_STATE",
    "persona_config.json": "LOCAL_ONLY",
    "avatar package asset": "LOCAL_ONLY",
    "sync metadata sidecar rows": "LOCAL_ONLY",
    "device_id preference": "LOCAL_ONLY",
}


@dataclass(frozen=True)
class SyncRevision:
    origin_device_id: str
    counter: int
    parent_hash: str | None
    ancestor_hash: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "counter": self.counter,
            "ancestor_hash": self.ancestor_hash,
            "origin_device_id": self.origin_device_id,
            "parent_hash": self.parent_hash,
        }


@dataclass(frozen=True)
class SyncRecord:
    persona_id: str
    entity_kind: str
    entity_id: str
    origin_device_id: str
    canonical_payload: dict[str, Any] | None
    content_hash: str
    revision: SyncRevision
    tombstone: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "canonical_payload": self.canonical_payload,
            "content_hash": self.content_hash,
            "entity_id": self.entity_id,
            "entity_kind": self.entity_kind,
            "origin_device_id": self.origin_device_id,
            "persona_id": self.persona_id,
            "protocol_version": PROTOCOL_VERSION,
            "revision": self.revision.as_dict(),
            "tombstone": self.tombstone,
        }


@dataclass(frozen=True)
class SyncManifest:
    persona_id: str
    device_id: str
    records: tuple[SyncRecord, ...]
    schema_version: int = SCHEMA_VERSION
    protocol_version: int = PROTOCOL_VERSION

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "device_id": self.device_id,
            "persona_id": self.persona_id,
            "protocol_version": self.protocol_version,
            "records": [record.as_dict() for record in sorted(
                self.records, key=lambda item: (item.entity_kind, item.entity_id))],
            "schema_version": self.schema_version,
        }
        value["manifest_hash"] = canonical_hash(value)
        return value


@dataclass(frozen=True)
class DeltaEntry:
    change: str
    record: SyncRecord

    def as_dict(self) -> dict[str, Any]:
        return {"change": self.change, "record": self.record.as_dict()}


@dataclass(frozen=True)
class SyncConflict:
    code: str
    entity_kind: str
    entity_id: str
    local_hash: str
    remote_hash: str

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "entity_id": self.entity_id,
            "entity_kind": self.entity_kind,
            "local_hash": self.local_hash,
            "remote_hash": self.remote_hash,
        }


@dataclass(frozen=True)
class SyncDelta:
    persona_id: str
    source_device_id: str
    entries: tuple[DeltaEntry, ...]
    conflicts: tuple[SyncConflict, ...] = ()
    protocol_version: int = PROTOCOL_VERSION

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "conflicts": [item.as_dict() for item in sorted(
                self.conflicts, key=lambda item: (item.entity_kind, item.entity_id, item.code))],
            "entries": [item.as_dict() for item in sorted(
                self.entries, key=lambda item: (item.record.entity_kind, item.record.entity_id))],
            "persona_id": self.persona_id,
            "protocol_version": self.protocol_version,
            "source_device_id": self.source_device_id,
        }
        value["delta_hash"] = canonical_hash(value)
        return value


@dataclass(frozen=True)
class SyncScan:
    manifest: SyncManifest
    delta: SyncDelta
    metadata_recovered: bool = False


def canonical_json(value: Any) -> str:
    """Canonical UTF-8 JSON: sorted keys, compact separators, no NaN/Infinity."""
    return json.dumps(_canonical_value(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _canonical_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SyncError("NON_FINITE_NUMBER")
        return 0.0 if value == 0.0 else value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"$binary_base64": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise SyncError("NAIVE_TIMESTAMP")
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise SyncError("NON_STRING_OBJECT_KEY")
        return {key: _canonical_value(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    raise SyncError("UNSUPPORTED_CANONICAL_VALUE")


def _canonical_uuid(value: str, code: str) -> str:
    if not isinstance(value, str) or len(value) != 36:
        raise SyncError(code)
    try:
        if str(uuid.UUID(value)) != value:
            raise SyncError(code)
    except (ValueError, AttributeError, TypeError) as error:
        raise SyncError(code) from error
    return value


def _baseline_columns() -> dict[str, tuple[str, ...]]:
    return {table: tuple(sorted(REQUIRED_COLUMNS[table]))
            for table in SYNC_ENTITY_POLICIES if table != "persona.identity"
            and table in REQUIRED_COLUMNS}


_BASELINE_COLUMNS = _baseline_columns()


def _validate_persona_database(connection: sqlite3.Connection, persona_id: str) -> None:
    """Validate schema 24 and honor an Android owner marker when one exists.

    Desktop's released schema has no per-Persona owner column because profiles
    bind separate database URLs. The CLI resolves that binding explicitly;
    Android-owned portable DBs additionally retain their strict owner marker.
    """
    schema = connection.execute(
        "SELECT value FROM schema_metadata WHERE key='turso_baseline_version'").fetchone()
    if schema is None or str(schema[0]) != str(SCHEMA_VERSION):
        raise SyncError("SCHEMA_24_REQUIRED")
    try:
        owner = connection.execute(
            "SELECT value FROM schema_metadata WHERE key='android_persona_id'").fetchone()
    except sqlite3.Error:
        owner = None
    if owner is not None and owner[0] != persona_id:
        raise SyncError("WRONG_PERSONA")


def _record_key(record: SyncRecord | Mapping[str, Any]) -> tuple[str, str]:
    if isinstance(record, SyncRecord):
        return record.entity_kind, record.entity_id
    return str(record["entity_kind"]), str(record["entity_id"])


def manifest_semantically_equal(left: SyncManifest, right: SyncManifest) -> bool:
    """Compare Persona state and payloads while ignoring per-device revision labels."""
    if left.protocol_version != right.protocol_version or left.persona_id != right.persona_id:
        return False
    left_records = {_record_key(item): (item.content_hash, item.tombstone) for item in left.records}
    right_records = {_record_key(item): (item.content_hash, item.tombstone) for item in right.records}
    return left_records == right_records


def _tombstone_hash(persona_id: str, entity_kind: str, entity_id: str) -> str:
    return canonical_hash({"entity_id": entity_id, "entity_kind": entity_kind,
                          "persona_id": persona_id, "tombstone": True})


def _from_dict(record: Mapping[str, Any]) -> SyncRecord:
    if set(record) != {"canonical_payload", "content_hash", "entity_id", "entity_kind",
                       "origin_device_id", "persona_id", "protocol_version", "revision", "tombstone"}:
        raise SyncError("RECORD_FIELDS_UNSUPPORTED")
    revision_value = record["revision"]
    if not isinstance(revision_value, Mapping) or set(revision_value) != {
            "ancestor_hash", "counter", "origin_device_id", "parent_hash"}:
        raise SyncError("REVISION_INVALID")
    if type(revision_value["counter"]) is not int \
            or not isinstance(revision_value["origin_device_id"], str) \
            or (revision_value["parent_hash"] is not None
                and not isinstance(revision_value["parent_hash"], str)) \
            or (revision_value["ancestor_hash"] is not None
                and not isinstance(revision_value["ancestor_hash"], str)):
        raise SyncError("REVISION_INVALID")
    if type(record["tombstone"]) is not bool:
        raise SyncError("RECORD_INVALID")
    for key in ("entity_id", "entity_kind", "origin_device_id", "persona_id", "content_hash"):
        if not isinstance(record[key], str):
            raise SyncError("RECORD_INVALID")
    revision = SyncRevision(str(revision_value["origin_device_id"]),
                            revision_value["counter"], revision_value["parent_hash"],
                            revision_value["ancestor_hash"])
    return SyncRecord(
        persona_id=str(record["persona_id"]), entity_kind=str(record["entity_kind"]),
        entity_id=str(record["entity_id"]), origin_device_id=str(record["origin_device_id"]),
        canonical_payload=record.get("canonical_payload"), content_hash=str(record["content_hash"]),
        revision=revision, tombstone=bool(record["tombstone"]),
    )


def validate_manifest(manifest: SyncManifest | Mapping[str, Any],
                      expected_persona_id: str | None = None) -> SyncManifest:
    value = manifest.as_dict() if isinstance(manifest, SyncManifest) else dict(manifest)
    if set(value) != {"device_id", "manifest_hash", "persona_id", "protocol_version",
                      "records", "schema_version"}:
        raise SyncError("MANIFEST_FIELDS_UNSUPPORTED")
    if type(value.get("protocol_version")) is not int or type(value.get("schema_version")) is not int \
            or value.get("protocol_version") != PROTOCOL_VERSION \
            or value.get("schema_version") != SCHEMA_VERSION:
        raise SyncError("UNSUPPORTED_PROTOCOL_OR_SCHEMA")
    persona_id = _canonical_uuid(value.get("persona_id"), "PERSONA_ID_INVALID")
    device_id = _canonical_uuid(value.get("device_id"), "DEVICE_ID_INVALID")
    if expected_persona_id is not None and persona_id != _canonical_uuid(
            expected_persona_id, "PERSONA_ID_INVALID"):
        raise SyncError("WRONG_PERSONA")
    raw_records = value.get("records")
    if not isinstance(raw_records, list) or len(raw_records) > MAX_RECORDS:
        raise SyncError("MANIFEST_RECORD_COUNT_INVALID")
    records: list[SyncRecord] = []
    seen: set[tuple[str, str]] = set()
    total_bytes = 0
    for raw in raw_records:
        if not isinstance(raw, Mapping):
            raise SyncError("RECORD_INVALID")
        try:
            record = _from_dict(raw)
        except (KeyError, TypeError, ValueError) as error:
            if isinstance(error, SyncError):
                raise
            raise SyncError("RECORD_INVALID") from error
        if type(raw.get("protocol_version")) is not int \
                or raw.get("protocol_version") != PROTOCOL_VERSION or record.persona_id != persona_id:
            raise SyncError("RECORD_PERSONA_OR_PROTOCOL_MISMATCH")
        if record.entity_kind not in SYNC_ENTITY_POLICIES:
            raise SyncError("UNSUPPORTED_ENTITY_KIND")
        if not record.entity_id or len(record.entity_id.encode("utf-8")) > MAX_ID_BYTES:
            raise SyncError("ENTITY_ID_INVALID")
        if record.origin_device_id != _canonical_uuid(record.origin_device_id, "DEVICE_ID_INVALID") \
                or record.revision.origin_device_id != record.origin_device_id:
            raise SyncError("REVISION_ORIGIN_INVALID")
        if record.revision.counter < 0 or any(value is not None and (
                len(value) != 64 or any(char not in "0123456789abcdef" for char in value))
                for value in (record.revision.parent_hash, record.revision.ancestor_hash)):
            raise SyncError("REVISION_INVALID")
        key = _record_key(record)
        if key in seen:
            raise SyncError("DUPLICATE_ENTITY")
        seen.add(key)
        if record.tombstone:
            if record.canonical_payload is not None or record.content_hash != _tombstone_hash(
                    persona_id, record.entity_kind, record.entity_id):
                raise SyncError("TOMBSTONE_HASH_INVALID")
        else:
            if not isinstance(record.canonical_payload, dict) \
                    or record.content_hash != canonical_hash(record.canonical_payload):
                raise SyncError("CONTENT_HASH_INVALID")
            payload_size = len(canonical_json(record.canonical_payload).encode("utf-8"))
            if payload_size > MAX_RECORD_BYTES:
                raise SyncError("RECORD_SIZE_LIMIT")
            total_bytes += payload_size
        records.append(record)
    if tuple(records) != tuple(sorted(records, key=lambda item: (item.entity_kind, item.entity_id))):
        raise SyncError("RECORD_ORDER_INVALID")
    if total_bytes > MAX_MANIFEST_BYTES:
        raise SyncError("MANIFEST_SIZE_LIMIT")
    computed = canonical_hash({key: value[key] for key in sorted(value) if key != "manifest_hash"})
    if value.get("manifest_hash") != computed:
        raise SyncError("MANIFEST_HASH_INVALID")
    return SyncManifest(persona_id, device_id, tuple(records))


def validate_delta(delta: SyncDelta | Mapping[str, Any], expected_persona_id: str) -> dict[str, Any]:
    value = delta.as_dict() if isinstance(delta, SyncDelta) else dict(delta)
    if set(value) != {"conflicts", "delta_hash", "entries", "persona_id",
                      "protocol_version", "source_device_id"}:
        raise SyncError("DELTA_FIELDS_UNSUPPORTED")
    persona_id = _canonical_uuid(value.get("persona_id"), "PERSONA_ID_INVALID")
    if persona_id != _canonical_uuid(expected_persona_id, "PERSONA_ID_INVALID"):
        raise SyncError("WRONG_PERSONA")
    if type(value.get("protocol_version")) is not int or value.get("protocol_version") != PROTOCOL_VERSION:
        raise SyncError("UNSUPPORTED_PROTOCOL")
    _canonical_uuid(value.get("source_device_id"), "DEVICE_ID_INVALID")
    entries = value.get("entries")
    conflicts = value.get("conflicts")
    if not isinstance(entries, list) or not isinstance(conflicts, list) \
            or len(entries) > MAX_RECORDS or len(conflicts) > MAX_RECORDS:
        raise SyncError("DELTA_SIZE_LIMIT")
    total_bytes = 0
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        if not isinstance(entry, Mapping) or set(entry) != {"change", "record"} \
                or entry.get("change") not in {"created", "updated", "deleted"}:
            raise SyncError("DELTA_ENTRY_INVALID")
        raw_record = entry.get("record")
        if not isinstance(raw_record, Mapping):
            raise SyncError("DELTA_ENTRY_INVALID")
        try:
            record = _from_dict(raw_record)
        except (KeyError, TypeError, ValueError) as error:
            if isinstance(error, SyncError):
                raise
            raise SyncError("RECORD_INVALID") from error
        if record.persona_id != persona_id:
            raise SyncError("WRONG_PERSONA")
        if raw_record.get("protocol_version") != PROTOCOL_VERSION:
            raise SyncError("UNSUPPORTED_PROTOCOL")
        if (entry["change"] == "deleted") != record.tombstone:
            raise SyncError("DELTA_TOMBSTONE_STATE_INVALID")
        if record.entity_kind not in SYNC_ENTITY_POLICIES:
            raise SyncError("UNSUPPORTED_ENTITY_KIND")
        if not record.entity_id or len(record.entity_id.encode("utf-8")) > MAX_ID_BYTES:
            raise SyncError("ENTITY_ID_INVALID")
        if record.origin_device_id != _canonical_uuid(record.origin_device_id, "DEVICE_ID_INVALID") \
                or record.revision.origin_device_id != record.origin_device_id:
            raise SyncError("REVISION_ORIGIN_INVALID")
        if any(value is not None and (len(value) != 64
                or any(char not in "0123456789abcdef" for char in value))
                for value in (record.revision.parent_hash, record.revision.ancestor_hash)):
            raise SyncError("REVISION_INVALID")
        if record.tombstone:
            if record.content_hash != _tombstone_hash(persona_id, record.entity_kind, record.entity_id):
                raise SyncError("TOMBSTONE_HASH_INVALID")
        else:
            if not isinstance(record.canonical_payload, dict) \
                    or canonical_hash(record.canonical_payload) != record.content_hash:
                raise SyncError("CONTENT_HASH_INVALID")
            payload_size = len(canonical_json(record.canonical_payload).encode("utf-8"))
            if payload_size > MAX_RECORD_BYTES:
                raise SyncError("RECORD_SIZE_LIMIT")
            total_bytes += payload_size
        if record.revision.counter < 0 or record.revision.origin_device_id != record.origin_device_id:
            raise SyncError("REVISION_INVALID")
        key = _record_key(record)
        if key in seen:
            raise SyncError("DUPLICATE_ENTITY")
        seen.add(key)
    for conflict in conflicts:
        if not isinstance(conflict, Mapping) or set(conflict) != {
                "code", "entity_id", "entity_kind", "local_hash", "remote_hash"}:
            raise SyncError("CONFLICT_STATE_INVALID")
        if not isinstance(conflict, Mapping) or conflict.get("code") not in {
                "IDENTITY_PAYLOAD_CONFLICT", "CONCURRENT_MUTATION",
                "CONCURRENT_DELETE_MUTATION", "TOMBSTONE_RESURRECTION", "TOMBSTONE_ID_REUSE"}:
            raise SyncError("CONFLICT_STATE_INVALID")
        if conflict.get("entity_kind") not in SYNC_ENTITY_POLICIES:
            raise SyncError("UNSUPPORTED_ENTITY_KIND")
        if not isinstance(conflict.get("entity_id"), str) or not conflict["entity_id"] \
                or len(conflict["entity_id"].encode("utf-8")) > MAX_ID_BYTES:
            raise SyncError("ENTITY_ID_INVALID")
        for hash_key in ("local_hash", "remote_hash"):
            hash_value = conflict.get(hash_key)
            if not isinstance(hash_value, str) or len(hash_value) != 64 \
                    or any(char not in "0123456789abcdef" for char in hash_value):
                raise SyncError("CONFLICT_HASH_INVALID")
    entry_order = [(entry["record"]["entity_kind"], entry["record"]["entity_id"])
                   for entry in entries]
    conflict_order = [(item["entity_kind"], item["entity_id"], item["code"]) for item in conflicts]
    if entry_order != sorted(entry_order) or conflict_order != sorted(conflict_order):
        raise SyncError("DELTA_ORDER_INVALID")
    if total_bytes > MAX_MANIFEST_BYTES:
        raise SyncError("DELTA_SIZE_LIMIT")
    computed = canonical_hash({key: value[key] for key in sorted(value) if key != "delta_hash"})
    if value.get("delta_hash") != computed:
        raise SyncError("DELTA_HASH_INVALID")
    return value


def _conflict_code(left: SyncRecord, right: SyncRecord) -> str | None:
    if left.content_hash == right.content_hash and left.tombstone == right.tombstone:
        return None
    policy = SYNC_ENTITY_POLICIES[left.entity_kind]
    if left.tombstone or right.tombstone:
        tomb, live = (left, right) if left.tombstone else (right, left)
        if tomb.revision.parent_hash == live.content_hash \
                or tomb.revision.ancestor_hash == live.content_hash:
            return None
        return "CONCURRENT_DELETE_MUTATION"
    if policy.policy == "SYNC_APPEND_ONLY":
        return "IDENTITY_PAYLOAD_CONFLICT"
    if (left.revision.parent_hash == right.content_hash
            or right.revision.parent_hash == left.content_hash
            or left.revision.ancestor_hash == right.content_hash
            or right.revision.ancestor_hash == left.content_hash):
        return None
    if left.revision.parent_hash == right.revision.parent_hash \
            and left.origin_device_id != right.origin_device_id:
        return "CONCURRENT_MUTATION"
    return "CONCURRENT_MUTATION"


def detect_conflicts(left: SyncManifest, right: SyncManifest) -> tuple[SyncConflict, ...]:
    left = validate_manifest(left)
    right = validate_manifest(right)
    if left.persona_id != right.persona_id:
        raise SyncError("WRONG_PERSONA")
    left_map = {_record_key(item): item for item in left.records}
    right_map = {_record_key(item): item for item in right.records}
    conflicts: list[SyncConflict] = []
    for key in sorted(left_map.keys() & right_map.keys()):
        a, b = left_map[key], right_map[key]
        code = _conflict_code(a, b)
        if code:
            conflicts.append(SyncConflict(code, key[0], key[1], a.content_hash, b.content_hash))
    return tuple(conflicts)


def generate_delta(base: SyncManifest, current: SyncManifest,
                   expected_persona_id: str | None = None) -> SyncDelta:
    base = validate_manifest(base, expected_persona_id)
    current = validate_manifest(current, expected_persona_id)
    if base.persona_id != current.persona_id:
        raise SyncError("WRONG_PERSONA")
    base_map = {_record_key(item): item for item in base.records}
    current_map = {_record_key(item): item for item in current.records}
    conflicts: list[SyncConflict] = []
    entries: list[DeltaEntry] = []
    for key in sorted(base_map.keys() | current_map.keys()):
        previous, latest = base_map.get(key), current_map.get(key)
        if latest is None:
            if previous and not previous.tombstone:
                raise SyncError("DELETE_WITHOUT_TOMBSTONE")
            continue
        if previous is None:
            entries.append(DeltaEntry("deleted" if latest.tombstone else "created", latest))
            continue
        if previous.content_hash == latest.content_hash and previous.tombstone == latest.tombstone:
            continue
        conflict_code = _conflict_code(latest, previous)
        if conflict_code:
            conflicts.append(SyncConflict(conflict_code, key[0], key[1],
                                           latest.content_hash, previous.content_hash))
            continue
        if latest.tombstone:
            change = "deleted"
        elif previous.tombstone:
            conflict = SyncConflict("TOMBSTONE_RESURRECTION", key[0], key[1],
                                    latest.content_hash, previous.content_hash)
            conflicts.append(conflict)
            continue
        else:
            change = "updated"
        entries.append(DeltaEntry(change, latest))
    return SyncDelta(current.persona_id, current.device_id, tuple(entries), tuple(conflicts))


class SyncMetadataStore:
    """WAL/FULL, device-local metadata sidecar; no cognition payload is stored."""

    def __init__(self, path: str | Path, device_id: str):
        self.path = Path(path)
        self.device_id = _canonical_uuid(device_id, "DEVICE_ID_INVALID")
        self.metadata_recovered = False
        self.connection: sqlite3.Connection | None = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._open()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=5.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        journal_mode = str(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]).casefold()
        if journal_mode != "wal":
            connection.close()
            raise sqlite3.DatabaseError("metadata WAL unavailable")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _archive_corrupt_store(self) -> None:
        suffix = uuid.uuid4().hex
        for candidate in (self.path, Path(str(self.path) + "-wal"), Path(str(self.path) + "-shm")):
            if candidate.exists():
                target = Path(str(candidate) + ".corrupt-" + suffix)
                candidate.replace(target)

    def _open(self) -> None:
        try:
            self.connection = self._connect()
            check = self.connection.execute("PRAGMA integrity_check").fetchone()
            if check is None or check[0] != "ok":
                raise sqlite3.DatabaseError("metadata integrity check failed")
            version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
            if version == 1:
                # Version 1 was an internal pre-acceptance sidecar that did not
                # retain ancestry hashes. Rebuild only this derived metadata;
                # the source Persona database is always opened read-only.
                raise sqlite3.DatabaseError("legacy metadata schema")
            if version not in (0, 2, 3):
                raise SyncError("SYNC_METADATA_VERSION_UNSUPPORTED")
            tables = {row[0] for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            expected = {"sync_store_meta", "persona_baselines", "sync_record_state",
                        "persona_revision_counters", "remote_seen"}
            if version == 2 and tables != expected:
                raise sqlite3.DatabaseError("metadata schema mismatch")
            if version == 3 and tables != expected | {"sync_conflicts"}:
                raise sqlite3.DatabaseError("metadata schema mismatch")
            if version == 0 and tables:
                raise sqlite3.DatabaseError("unversioned metadata tables")
            if version == 2:
                for table, columns in {
                        "sync_record_state": ("persona_id", "entity_kind", "entity_id", "content_hash",
                            "tombstone", "origin_device_id", "logical_counter", "parent_hash", "ancestor_hash"),
                        "remote_seen": ("persona_id", "remote_device_id", "entity_kind", "entity_id",
                            "content_hash", "tombstone", "origin_device_id", "logical_counter",
                            "parent_hash", "ancestor_hash"),
                }.items():
                    actual = tuple(row[1] for row in self.connection.execute(f'PRAGMA table_info("{table}")'))
                    if actual != columns:
                        raise sqlite3.DatabaseError("metadata column mismatch")
            if version == 0:
                self._create_schema()
            elif version == 2:
                self._upgrade_conflict_schema()
            stored_id = self.connection.execute(
                "SELECT value FROM sync_store_meta WHERE key='device_id'").fetchone()[0]
            if stored_id != self.device_id:
                raise SyncMetadataDeviceMismatch("SYNC_METADATA_DEVICE_MISMATCH")
        except SyncMetadataDeviceMismatch:
            self.close()
            raise
        except sqlite3.DatabaseError:
            self.close()
            self._archive_corrupt_store()
            self.metadata_recovered = True
            self.connection = self._connect()
            self._create_schema()

    def _create_schema(self) -> None:
        assert self.connection is not None
        self.connection.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE sync_store_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE persona_baselines(
                persona_id TEXT PRIMARY KEY,
                baseline_complete INTEGER NOT NULL CHECK (baseline_complete IN (0,1))
            );
            CREATE TABLE sync_record_state(
                persona_id TEXT NOT NULL,
                entity_kind TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                tombstone INTEGER NOT NULL CHECK (tombstone IN (0,1)),
                origin_device_id TEXT NOT NULL,
                logical_counter INTEGER NOT NULL CHECK (logical_counter >= 0),
                parent_hash TEXT,
                ancestor_hash TEXT,
                PRIMARY KEY(persona_id,entity_kind,entity_id)
            );
            CREATE TABLE persona_revision_counters(
                persona_id TEXT PRIMARY KEY,
                counter INTEGER NOT NULL CHECK (counter >= 0)
            );
            CREATE TABLE remote_seen(
                persona_id TEXT NOT NULL,
                remote_device_id TEXT NOT NULL,
                entity_kind TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                tombstone INTEGER NOT NULL CHECK (tombstone IN (0,1)),
                origin_device_id TEXT NOT NULL,
                logical_counter INTEGER NOT NULL CHECK (logical_counter >= 0),
                parent_hash TEXT,
                ancestor_hash TEXT,
                PRIMARY KEY(persona_id,remote_device_id,entity_kind,entity_id)
            );
            CREATE TABLE sync_conflicts(
                conflict_id TEXT PRIMARY KEY,
                persona_id TEXT NOT NULL,
                entity_kind TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                conflict_type TEXT NOT NULL,
                local_revision TEXT,
                remote_revision TEXT,
                local_hash TEXT,
                remote_hash TEXT,
                peer_device_id TEXT NOT NULL,
                first_seen_ms INTEGER NOT NULL,
                last_seen_ms INTEGER NOT NULL,
                resolution_state TEXT NOT NULL CHECK (resolution_state IN ('UNRESOLVED','RESOLVED'))
            );
            CREATE INDEX sync_conflicts_persona_state
                ON sync_conflicts(persona_id,resolution_state,last_seen_ms);
            INSERT INTO sync_store_meta(key,value) VALUES('device_id', '""');
            PRAGMA user_version=3;
            COMMIT;
        """)
        self.connection.execute("UPDATE sync_store_meta SET value=? WHERE key='device_id'", (self.device_id,))

    def _upgrade_conflict_schema(self) -> None:
        assert self.connection is not None
        self.connection.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE sync_conflicts(
                conflict_id TEXT PRIMARY KEY,
                persona_id TEXT NOT NULL,
                entity_kind TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                conflict_type TEXT NOT NULL,
                local_revision TEXT,
                remote_revision TEXT,
                local_hash TEXT,
                remote_hash TEXT,
                peer_device_id TEXT NOT NULL,
                first_seen_ms INTEGER NOT NULL,
                last_seen_ms INTEGER NOT NULL,
                resolution_state TEXT NOT NULL CHECK (resolution_state IN ('UNRESOLVED','RESOLVED'))
            );
            CREATE INDEX sync_conflicts_persona_state
                ON sync_conflicts(persona_id,resolution_state,last_seen_ms);
            PRAGMA user_version=3;
            COMMIT;
        """)

    def record_conflict(self, *, persona_id: str, entity_kind: str, entity_id: str,
                        conflict_type: str, peer_device_id: str,
                        local_hash: str | None, remote_hash: str | None,
                        local_revision: str | None, remote_revision: str | None) -> str:
        """Persist safe conflict identity/revision metadata, never payload content."""
        persona_id = _canonical_uuid(persona_id, "PERSONA_ID_INVALID")
        peer_device_id = _canonical_uuid(peer_device_id, "DEVICE_ID_INVALID")
        if conflict_type == "CONCURRENT_DELETE_MUTATION":
            conflict_type = "TOMBSTONE_CONFLICT"
        if not entity_kind or not entity_id or len(entity_id.encode("utf-8")) > MAX_ID_BYTES:
            raise SyncError("CONFLICT_IDENTITY_INVALID")
        identity = canonical_json({
            "entity_id": entity_id, "entity_kind": entity_kind,
            "local_hash": local_hash, "local_revision": local_revision,
            "peer_device_id": peer_device_id, "persona_id": persona_id,
            "remote_hash": remote_hash, "remote_revision": remote_revision,
            "type": conflict_type,
        })
        conflict_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        assert self.connection is not None
        self.connection.execute(
            "INSERT INTO sync_conflicts(conflict_id,persona_id,entity_kind,entity_id,conflict_type,"
            "local_revision,remote_revision,local_hash,remote_hash,peer_device_id,first_seen_ms,"
            "last_seen_ms,resolution_state) VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'UNRESOLVED') "
            "ON CONFLICT(conflict_id) DO UPDATE SET last_seen_ms=excluded.last_seen_ms",
            (conflict_id, persona_id, entity_kind, entity_id, conflict_type, local_revision,
             remote_revision, local_hash, remote_hash, peer_device_id, now_ms, now_ms))
        return conflict_id

    def local_revision(self, persona_id: str, entity_kind: str, entity_id: str) -> tuple[str | None, str | None]:
        persona_id = _canonical_uuid(persona_id, "PERSONA_ID_INVALID")
        assert self.connection is not None
        row = self.connection.execute(
            "SELECT content_hash,origin_device_id,logical_counter,parent_hash,ancestor_hash "
            "FROM sync_record_state WHERE persona_id=? AND entity_kind=? AND entity_id=?",
            (persona_id, entity_kind, entity_id)).fetchone()
        if row is None:
            return None, None
        revision = canonical_json({"origin_device_id": row["origin_device_id"],
                                   "counter": int(row["logical_counter"]),
                                   "parent_hash": row["parent_hash"],
                                   "ancestor_hash": row["ancestor_hash"]})
        return str(row["content_hash"]), revision

    def local_tombstone(self, persona_id: str, entity_kind: str, entity_id: str) -> bool:
        """Return whether local sync metadata remembers this entity as deleted."""
        persona_id = _canonical_uuid(persona_id, "PERSONA_ID_INVALID")
        assert self.connection is not None
        row = self.connection.execute(
            "SELECT tombstone FROM sync_record_state WHERE persona_id=? AND entity_kind=? AND entity_id=?",
            (persona_id, entity_kind, entity_id)).fetchone()
        return bool(row[0]) if row is not None else False

    def list_conflicts(self, persona_id: str, *, unresolved_only: bool = True) -> list[dict[str, Any]]:
        persona_id = _canonical_uuid(persona_id, "PERSONA_ID_INVALID")
        assert self.connection is not None
        query = "SELECT conflict_id,persona_id,entity_kind,entity_id,conflict_type,local_revision,"
        query += "remote_revision,local_hash,remote_hash,peer_device_id,first_seen_ms,last_seen_ms,"
        query += "resolution_state FROM sync_conflicts WHERE persona_id=?"
        parameters: tuple[Any, ...] = (persona_id,)
        if unresolved_only:
            query += " AND resolution_state='UNRESOLVED'"
        query += " ORDER BY first_seen_ms,conflict_id"
        return [dict(row) for row in self.connection.execute(query, parameters)]

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def __enter__(self) -> "SyncMetadataStore":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def reconcile(self, persona_id: str,
                  candidates: Mapping[tuple[str, str], tuple[dict[str, Any], str]]) -> tuple[
                      tuple[SyncRecord, ...], tuple[DeltaEntry, ...], tuple[SyncConflict, ...]]:
        persona_id = _canonical_uuid(persona_id, "PERSONA_ID_INVALID")
        assert self.connection is not None
        connection = self.connection
        changes: list[DeltaEntry] = []
        conflicts: list[SyncConflict] = []
        connection.execute("BEGIN IMMEDIATE")
        try:
            baseline = connection.execute(
                "SELECT baseline_complete FROM persona_baselines WHERE persona_id=?", (persona_id,)
            ).fetchone()
            first_observation = baseline is None
            previous = {
                (row["entity_kind"], row["entity_id"]): row
                for row in connection.execute(
                    "SELECT entity_kind,entity_id,content_hash,tombstone,origin_device_id,"
                    "logical_counter,parent_hash,ancestor_hash FROM sync_record_state WHERE persona_id=?",
                    (persona_id,))
            }

            def next_revision(parent_hash: str | None, ancestor_hash: str | None) -> SyncRevision:
                row = connection.execute(
                    "SELECT counter FROM persona_revision_counters WHERE persona_id=?", (persona_id,)
                ).fetchone()
                counter = int(row[0]) + 1 if row else 1
                connection.execute(
                    "INSERT INTO persona_revision_counters(persona_id,counter) VALUES(?,?) "
                    "ON CONFLICT(persona_id) DO UPDATE SET counter=excluded.counter",
                    (persona_id, counter))
                return SyncRevision(self.device_id, counter, parent_hash, ancestor_hash)

            def save(kind: str, entity_id: str, content_hash: str, tombstone: bool,
                     revision: SyncRevision) -> None:
                connection.execute(
                    "INSERT INTO sync_record_state(persona_id,entity_kind,entity_id,content_hash,"
                    "tombstone,origin_device_id,logical_counter,parent_hash,ancestor_hash) "
                    "VALUES(?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(persona_id,entity_kind,entity_id) DO UPDATE SET "
                    "content_hash=excluded.content_hash,tombstone=excluded.tombstone,"
                    "origin_device_id=excluded.origin_device_id,logical_counter=excluded.logical_counter,"
                    "parent_hash=excluded.parent_hash,ancestor_hash=excluded.ancestor_hash",
                    (persona_id, kind, entity_id, content_hash, int(tombstone),
                     revision.origin_device_id, revision.counter, revision.parent_hash,
                     revision.ancestor_hash))

            for key in sorted(candidates):
                kind, entity_id = key
                payload, content_hash = candidates[key]
                old = previous.get(key)
                if old and old["tombstone"]:
                    # An explicit tombstone is metadata-only until a future
                    # apply phase. The source row can still exist unchanged.
                    if old["parent_hash"] == content_hash:
                        continue
                    conflicts.append(SyncConflict("TOMBSTONE_ID_REUSE", kind, entity_id,
                                                  content_hash, old["content_hash"]))
                    continue
                if first_observation:
                    revision = SyncRevision(BASELINE_ORIGIN_DEVICE_ID, 0, None, content_hash)
                    save(kind, entity_id, content_hash, False, revision)
                    continue
                if old is None:
                    revision = next_revision(None, None)
                    save(kind, entity_id, content_hash, False, revision)
                    changes.append(DeltaEntry("created", SyncRecord(
                        persona_id, kind, entity_id, self.device_id, payload, content_hash, revision)))
                elif old["content_hash"] != content_hash:
                    revision = next_revision(str(old["content_hash"]),
                                             old["ancestor_hash"] or str(old["content_hash"]))
                    save(kind, entity_id, content_hash, False, revision)
                    changes.append(DeltaEntry("updated", SyncRecord(
                        persona_id, kind, entity_id, self.device_id, payload, content_hash, revision)))

            if not first_observation:
                for key in sorted(previous):
                    old = previous[key]
                    if key in candidates or old["tombstone"]:
                        continue
                    kind, entity_id = key
                    revision = next_revision(str(old["content_hash"]),
                                             old["ancestor_hash"] or str(old["content_hash"]))
                    deleted_hash = _tombstone_hash(persona_id, kind, entity_id)
                    save(kind, entity_id, deleted_hash, True, revision)
                    changes.append(DeltaEntry("deleted", SyncRecord(
                        persona_id, kind, entity_id, self.device_id, None, deleted_hash, revision, True)))

            if first_observation:
                connection.execute(
                    "INSERT INTO persona_baselines(persona_id,baseline_complete) VALUES(?,1)",
                    (persona_id,))
            state = connection.execute(
                "SELECT entity_kind,entity_id,content_hash,tombstone,origin_device_id,"
                "logical_counter,parent_hash,ancestor_hash FROM sync_record_state WHERE persona_id=? "
                "ORDER BY entity_kind,entity_id", (persona_id,)).fetchall()
            records: list[SyncRecord] = []
            for row in state:
                key = (str(row["entity_kind"]), str(row["entity_id"]))
                tombstone = bool(row["tombstone"])
                candidate = candidates.get(key)
                payload = candidate[0] if candidate is not None and not tombstone else None
                records.append(SyncRecord(
                    persona_id, key[0], key[1], str(row["origin_device_id"]), payload,
                    str(row["content_hash"]), SyncRevision(
                        str(row["origin_device_id"]), int(row["logical_counter"]),
                        row["parent_hash"], row["ancestor_hash"]),
                    tombstone))
            connection.execute("COMMIT")
            changes.sort(key=lambda item: (item.record.entity_kind, item.record.entity_id))
            conflicts.sort(key=lambda item: (item.entity_kind, item.entity_id, item.code))
            return tuple(records), tuple(changes), tuple(conflicts)
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def record_remote_seen(self, manifest: SyncManifest, expected_persona_id: str) -> None:
        manifest = validate_manifest(manifest, expected_persona_id)
        assert self.connection is not None
        connection = self.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            for record in manifest.records:
                connection.execute(
                    "INSERT INTO remote_seen(persona_id,remote_device_id,entity_kind,entity_id,"
                    "content_hash,tombstone,origin_device_id,logical_counter,parent_hash,ancestor_hash) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(persona_id,remote_device_id,entity_kind,entity_id) "
                    "DO UPDATE SET content_hash=excluded.content_hash,tombstone=excluded.tombstone,"
                    "origin_device_id=excluded.origin_device_id,logical_counter=excluded.logical_counter,"
                    "parent_hash=excluded.parent_hash,ancestor_hash=excluded.ancestor_hash",
                    (manifest.persona_id, manifest.device_id, record.entity_kind, record.entity_id,
                     record.content_hash, int(record.tombstone), record.origin_device_id,
                     record.revision.counter, record.revision.parent_hash,
                     record.revision.ancestor_hash))
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def checkpoint_remote_records(self, records: list[SyncRecord], peer_device_id: str) -> None:
        """Commit accepted remote revisions only after the Persona DB transaction."""
        assert self.connection is not None
        connection = self.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            for record in records:
                connection.execute(
                    "INSERT INTO sync_record_state(persona_id,entity_kind,entity_id,content_hash,"
                    "tombstone,origin_device_id,logical_counter,parent_hash,ancestor_hash) "
                    "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(persona_id,entity_kind,entity_id) "
                    "DO UPDATE SET content_hash=excluded.content_hash,tombstone=excluded.tombstone,"
                    "origin_device_id=excluded.origin_device_id,logical_counter=excluded.logical_counter,"
                    "parent_hash=excluded.parent_hash,ancestor_hash=excluded.ancestor_hash",
                    (record.persona_id, record.entity_kind, record.entity_id, record.content_hash,
                     int(record.tombstone), record.origin_device_id, record.revision.counter,
                     record.revision.parent_hash, record.revision.ancestor_hash))
                connection.execute(
                    "INSERT INTO remote_seen(persona_id,remote_device_id,entity_kind,entity_id,"
                    "content_hash,tombstone,origin_device_id,logical_counter,parent_hash,ancestor_hash) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(persona_id,remote_device_id,entity_kind,entity_id) "
                    "DO UPDATE SET content_hash=excluded.content_hash,tombstone=excluded.tombstone,"
                    "origin_device_id=excluded.origin_device_id,logical_counter=excluded.logical_counter,"
                    "parent_hash=excluded.parent_hash,ancestor_hash=excluded.ancestor_hash",
                    (record.persona_id, peer_device_id, record.entity_kind, record.entity_id,
                     record.content_hash, int(record.tombstone), record.origin_device_id,
                     record.revision.counter, record.revision.parent_hash,
                     record.revision.ancestor_hash))
            connection.execute("COMMIT")
            _debug_crash_barrier("D_CHECKPOINT_COMMITTED")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def create_tombstone(self, persona_id: str, entity_kind: str,
                         entity_id: str) -> SyncRecord:
        persona_id = _canonical_uuid(persona_id, "PERSONA_ID_INVALID")
        if entity_kind not in SYNC_ENTITY_POLICIES or not entity_id \
                or len(entity_id.encode("utf-8")) > MAX_ID_BYTES:
            raise SyncError("ENTITY_ID_INVALID")
        assert self.connection is not None
        connection = self.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                "SELECT content_hash,tombstone,ancestor_hash FROM sync_record_state WHERE persona_id=? "
                "AND entity_kind=? AND entity_id=?", (persona_id, entity_kind, entity_id)
            ).fetchone()
            if row is None:
                raise SyncError("ENTITY_NOT_OBSERVED")
            if row["tombstone"]:
                previous_hash = connection.execute(
                    "SELECT parent_hash FROM sync_record_state WHERE persona_id=? "
                    "AND entity_kind=? AND entity_id=?", (persona_id, entity_kind, entity_id)
                ).fetchone()[0]
                revision_row = connection.execute(
                    "SELECT origin_device_id,logical_counter,parent_hash,ancestor_hash FROM sync_record_state "
                    "WHERE persona_id=? AND entity_kind=? AND entity_id=?",
                    (persona_id, entity_kind, entity_id)).fetchone()
                connection.execute("COMMIT")
                return SyncRecord(persona_id, entity_kind, entity_id,
                    str(revision_row["origin_device_id"]), None,
                    _tombstone_hash(persona_id, entity_kind, entity_id),
                    SyncRevision(str(revision_row["origin_device_id"]),
                                 int(revision_row["logical_counter"]), previous_hash,
                                 revision_row["ancestor_hash"]), True)
            counter_row = connection.execute(
                "SELECT counter FROM persona_revision_counters WHERE persona_id=?", (persona_id,)
            ).fetchone()
            counter = int(counter_row[0]) + 1 if counter_row else 1
            connection.execute(
                "INSERT INTO persona_revision_counters(persona_id,counter) VALUES(?,?) "
                "ON CONFLICT(persona_id) DO UPDATE SET counter=excluded.counter",
                (persona_id, counter))
            deleted_hash = _tombstone_hash(persona_id, entity_kind, entity_id)
            connection.execute(
                "UPDATE sync_record_state SET content_hash=?,tombstone=1,origin_device_id=?,"
                "logical_counter=?,parent_hash=?,ancestor_hash=? "
                "WHERE persona_id=? AND entity_kind=? AND entity_id=?",
                (deleted_hash, self.device_id, counter, str(row["content_hash"]),
                 row["ancestor_hash"] or str(row["content_hash"]),
                 persona_id, entity_kind, entity_id))
            connection.execute("COMMIT")
            revision = SyncRevision(self.device_id, counter, str(row["content_hash"]),
                                    row["ancestor_hash"] or str(row["content_hash"]))
            return SyncRecord(persona_id, entity_kind, entity_id, self.device_id,
                              None, deleted_hash, revision, True)
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise


class PersonaSyncEngine:
    """Read an allowlisted Persona snapshot and reconcile local sync metadata."""

    def __init__(self, database_path: str | Path, metadata_path: str | Path, device_id: str,
                 identity_path: str | Path | None = None):
        self.database_path = Path(database_path)
        self.metadata_path = Path(metadata_path)
        self.device_id = _canonical_uuid(device_id, "DEVICE_ID_INVALID")
        if identity_path is None and str(self.database_path) != ":memory:":
            identity_path = self.database_path.parent / "identity.json"
        self.identity_path = Path(identity_path) if identity_path is not None else None

    def _read_candidates(self, persona_id: str) -> dict[tuple[str, str], tuple[dict[str, Any], str]]:
        persona_id = _canonical_uuid(persona_id, "PERSONA_ID_INVALID")
        if not self.database_path.is_file():
            raise SyncError("PERSONA_DATABASE_UNAVAILABLE")
        uri = self.database_path.resolve().as_uri() + "?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True, timeout=5.0, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN")
            _validate_persona_database(connection, persona_id)
            candidates: dict[tuple[str, str], tuple[dict[str, Any], str]] = {}
            record_count = 0
            total_bytes = 0
            for kind in sorted(table for table in SYNC_ENTITY_POLICIES if table != "persona.identity"):
                expected_columns = _BASELINE_COLUMNS.get(kind)
                if not expected_columns:
                    raise SyncError("SYNC_SCHEMA_UNSUPPORTED")
                info = connection.execute(f'PRAGMA table_info("{kind}")').fetchall()
                columns = tuple(sorted(str(row["name"]) for row in info))
                primary = tuple(str(row["name"]) for row in sorted(info, key=lambda row: row["pk"])
                                if int(row["pk"]) > 0)
                policy = SYNC_ENTITY_POLICIES[kind]
                if columns != expected_columns or primary != REQUIRED_PRIMARY_KEYS.get(kind):
                    raise SyncError("SYNC_SCHEMA_UNSUPPORTED")
                select_columns = ",".join('"' + name + '"' for name in expected_columns)
                cursor = connection.execute(
                    f'SELECT {select_columns} FROM "{kind}" ORDER BY "{policy.identity_column}"')
                while True:
                    rows = cursor.fetchmany(_FETCH_BATCH)
                    if not rows:
                        break
                    for row in rows:
                        payload = {column: row[column] for column in expected_columns}
                        entity_id = str(payload[policy.identity_column])
                        if not entity_id or len(entity_id.encode("utf-8")) > MAX_ID_BYTES:
                            raise SyncError("ENTITY_ID_INVALID")
                        if (kind, entity_id) in candidates:
                            raise SyncError("DUPLICATE_ENTITY")
                        payload_json = canonical_json(payload)
                        payload_size = len(payload_json.encode("utf-8"))
                        if payload_size > MAX_RECORD_BYTES:
                            raise SyncError("RECORD_SIZE_LIMIT")
                        total_bytes += payload_size
                        record_count += 1
                        if record_count > MAX_RECORDS or total_bytes > MAX_MANIFEST_BYTES:
                            raise SyncError("MANIFEST_SIZE_LIMIT")
                        candidates[(kind, entity_id)] = (payload, hashlib.sha256(
                            payload_json.encode("utf-8")).hexdigest())
            if self.identity_path is not None and self.identity_path.exists():
                if self.identity_path.stat().st_size > MAX_IDENTITY_FILE_BYTES:
                    raise SyncError("IDENTITY_FILE_SIZE_LIMIT")
                try:
                    raw_identity = self.identity_path.read_text(encoding="utf-8")
                    if self.identity_path.suffix.lower() == ".json":
                        identity = json.loads(raw_identity)
                        if not isinstance(identity, dict) or set(identity) != {"persona_id", "identity_text"} \
                                or identity["persona_id"] != persona_id \
                                or not isinstance(identity["identity_text"], str):
                            raise SyncError("WRONG_PERSONA")
                        identity_text = identity["identity_text"]
                    else:
                        # Desktop profiles traditionally point to a Markdown/text
                        # identity file; the protocol record normalizes it to the
                        # same identity_text payload used by Android packages.
                        identity_text = raw_identity
                except SyncError:
                    raise
                except (UnicodeError, json.JSONDecodeError, OSError) as error:
                    raise SyncError("PERSONA_IDENTITY_FILE_INVALID") from error
                payload = {"identity_text": identity_text, "persona_id": persona_id}
                payload_json = canonical_json(payload)
                size = len(payload_json.encode("utf-8"))
                if size > MAX_RECORD_BYTES or record_count + 1 > MAX_RECORDS \
                        or total_bytes + size > MAX_MANIFEST_BYTES:
                    raise SyncError("MANIFEST_SIZE_LIMIT")
                candidates[("persona.identity", persona_id)] = (payload, hashlib.sha256(
                    payload_json.encode("utf-8")).hexdigest())
            connection.execute("COMMIT")
            connection.close()
            return candidates
        except SyncError:
            try:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                connection.close()
            except (UnboundLocalError, sqlite3.Error):
                pass
            raise
        except sqlite3.Error as error:
            try:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                connection.close()
            except (UnboundLocalError, sqlite3.Error):
                pass
            raise SyncError("PERSONA_DATABASE_INVALID") from error

    def scan(self, persona_id: str) -> SyncScan:
        candidates = self._read_candidates(persona_id)
        with SyncMetadataStore(self.metadata_path, self.device_id) as store:
            records, entries, conflicts = store.reconcile(persona_id, candidates)
            recovered = store.metadata_recovered
        manifest = SyncManifest(_canonical_uuid(persona_id, "PERSONA_ID_INVALID"),
                                self.device_id, records)
        validate_manifest(manifest, persona_id)
        delta = SyncDelta(manifest.persona_id, self.device_id, entries, conflicts)
        validate_delta(delta, persona_id)
        return SyncScan(manifest, delta, recovered)

    def record_remote_seen(self, manifest: SyncManifest, expected_persona_id: str) -> None:
        with SyncMetadataStore(self.metadata_path, self.device_id) as store:
            store.record_remote_seen(manifest, expected_persona_id)

    def create_tombstone(self, persona_id: str, entity_kind: str, entity_id: str) -> SyncRecord:
        # Establish the local baseline before a caller marks an existing entity
        # deleted. This observes the DB read-only and never deletes domain data.
        self.scan(persona_id)
        with SyncMetadataStore(self.metadata_path, self.device_id) as store:
            return store.create_tombstone(persona_id, entity_kind, entity_id)


def observe_persona_json(database_path: str | Path, private_root: str | Path,
                         persona_id: str, device_id: str) -> str:
    """Fixed bridge entry point; result data is returned without content logging."""
    engine = PersonaSyncEngine(database_path, sync_metadata_path(private_root), device_id)
    result = engine.scan(persona_id)
    return canonical_json({
        "delta": result.delta.as_dict(),
        "manifest": result.manifest.as_dict(),
        "metadata_recovered": result.metadata_recovered,
    })


def sync_metadata_path(private_root: str | Path) -> Path:
    """Canonical app-private sidecar path, outside every Persona package directory."""
    root = Path(private_root)
    return root / "sync" / "metadata.sqlite"


def _decode_canonical_sql_value(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {"$binary_base64"} and isinstance(value["$binary_base64"], str):
            try:
                return base64.b64decode(value["$binary_base64"], validate=True)
            except (ValueError, base64.binascii.Error) as error:
                raise SyncError("BINARY_VALUE_INVALID") from error
        return {key: _decode_canonical_sql_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode_canonical_sql_value(item) for item in value]
    return value


def apply_remote_delta(database_path: str | Path, private_root: str | Path,
                       device_id: str, expected_persona_id: str,
                       trusted_peer_device_id: str,
                       delta: SyncDelta | Mapping[str, Any]) -> str:
    """Validate and transactionally apply a trusted peer delta to one schema-24 DB.

    Domain rows commit atomically first. The sidecar checkpoint follows. If a
    process stops between those commits, retry sees identical domain hashes,
    performs no second mutation, and can safely complete the checkpoint.
    """
    persona_id = _canonical_uuid(expected_persona_id, "PERSONA_ID_INVALID")
    local_device_id = _canonical_uuid(device_id, "DEVICE_ID_INVALID")
    peer_id = _canonical_uuid(trusted_peer_device_id, "DEVICE_ID_INVALID")

    def persist_conflict(error: SyncApplyConflict) -> None:
        if not error.entity_kind or not error.entity_id:
            return
        with SyncMetadataStore(sync_metadata_path(private_root), local_device_id) as store:
            local_hash, local_revision = store.local_revision(
                persona_id, error.entity_kind, error.entity_id)
            store.record_conflict(
                persona_id=persona_id, entity_kind=error.entity_kind, entity_id=error.entity_id,
                conflict_type=error.code, peer_device_id=peer_id,
                local_hash=error.local_hash or local_hash,
                remote_hash=error.remote_hash,
                local_revision=error.local_revision or local_revision,
                remote_revision=error.remote_revision)

    validated = validate_delta(delta, persona_id)
    if validated["source_device_id"] != peer_id:
        raise SyncError("SENDER_MISMATCH")
    if validated["conflicts"]:
        item = validated["conflicts"][0]
        error = SyncApplyConflict(item["code"], item["entity_kind"], item["entity_id"],
                                  local_hash=item["local_hash"], remote_hash=item["remote_hash"])
        persist_conflict(error)
        raise error
    records = [_from_dict(entry["record"]) for entry in validated["entries"]]
    if any(record.entity_kind == "persona.identity" for record in records):
        item = next(record for record in records if record.entity_kind == "persona.identity")
        error = SyncApplyConflict("ENTITY_STORAGE_NOT_TRANSACTIONAL", item.entity_kind, item.entity_id,
                                  remote_hash=item.content_hash,
                                  remote_revision=canonical_json(item.revision.as_dict()))
        persist_conflict(error)
        raise error

    database = Path(database_path)
    if not database.is_file():
        raise SyncError("PERSONA_DATABASE_UNAVAILABLE")
    uri = database.resolve().as_uri() + "?mode=rw"
    connection: sqlite3.Connection | None = None
    applied = 0
    idempotent = 0
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=5.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("PRAGMA defer_foreign_keys=ON")
        _validate_persona_database(connection, persona_id)

        plans: list[tuple[SyncRecord, str, tuple[str, ...], Any]] = []
        for entry, record in zip(validated["entries"], records):
            policy = SYNC_ENTITY_POLICIES[record.entity_kind]
            columns = _BASELINE_COLUMNS.get(record.entity_kind)
            if not columns:
                raise SyncError("SYNC_SCHEMA_UNSUPPORTED")
            info = connection.execute(f'PRAGMA table_info("{record.entity_kind}")').fetchall()
            if tuple(sorted(str(row["name"]) for row in info)) != columns:
                raise SyncError("SYNC_SCHEMA_UNSUPPORTED")
            primary = tuple(str(row["name"]) for row in sorted(info, key=lambda row: row["pk"])
                            if int(row["pk"]) > 0)
            if primary != REQUIRED_PRIMARY_KEYS.get(record.entity_kind):
                raise SyncError("SYNC_SCHEMA_UNSUPPORTED")
            current_row = connection.execute(
                f'SELECT {",".join(chr(34)+column+chr(34) for column in columns)} '
                f'FROM "{record.entity_kind}" WHERE "{policy.identity_column}"=?',
                (record.entity_id,)).fetchone()
            current_payload = dict(current_row) if current_row is not None else None
            current_hash = canonical_hash(current_payload) if current_payload is not None else None

            if record.tombstone:
                if current_hash is None:
                    # Already absent: duplicate tombstone effect is a no-op.
                    idempotent += 1
                elif current_hash in (record.revision.parent_hash, record.revision.ancestor_hash):
                    # Keep cognition rows and their independent FK descendants. The
                    # checkpoint suppresses this unchanged source row in sync scans.
                    idempotent += 1
                else:
                    raise SyncApplyConflict("TOMBSTONE_CONFLICT", record.entity_kind, record.entity_id,
                                            local_hash=current_hash, remote_hash=record.content_hash,
                                            remote_revision=canonical_json(record.revision.as_dict()))
                plans.append((record, "deleted", columns, current_payload))
                continue

            payload = record.canonical_payload
            if not isinstance(payload, dict) or set(payload) != set(columns):
                raise SyncError("REMOTE_PAYLOAD_COLUMNS_INVALID")
            if str(payload.get(policy.identity_column)) != record.entity_id:
                raise SyncError("REMOTE_ENTITY_ID_MISMATCH")
            if current_hash == record.content_hash:
                idempotent += 1
                plans.append((record, "noop", columns, current_payload))
                continue
            if current_payload is None:
                with SyncMetadataStore(sync_metadata_path(private_root), local_device_id) as store:
                    local_tombstone = store.local_tombstone(
                        persona_id, record.entity_kind, record.entity_id)
                    local_hash, local_revision = store.local_revision(
                        persona_id, record.entity_kind, record.entity_id)
                if local_tombstone and not record.tombstone:
                    raise SyncApplyConflict(
                        "CONCURRENT_DELETE_MUTATION", record.entity_kind, record.entity_id,
                        local_hash=local_hash, remote_hash=record.content_hash,
                        local_revision=local_revision,
                        remote_revision=canonical_json(record.revision.as_dict()))
                if entry["change"] != "created" or record.revision.parent_hash is not None:
                    raise SyncApplyConflict("REMOTE_ANCESTRY_UNKNOWN",
                                            record.entity_kind, record.entity_id,
                                            remote_hash=record.content_hash,
                                            remote_revision=canonical_json(record.revision.as_dict()))
                plans.append((record, "insert", columns, payload))
                continue
            if entry["change"] != "updated" or current_hash not in (
                    record.revision.parent_hash, record.revision.ancestor_hash):
                conflict = ("IDENTITY_PAYLOAD_CONFLICT"
                            if policy.policy == "SYNC_APPEND_ONLY"
                            else "CONCURRENT_MUTATION")
                raise SyncApplyConflict(conflict, record.entity_kind, record.entity_id,
                                        local_hash=current_hash, remote_hash=record.content_hash,
                                        remote_revision=canonical_json(record.revision.as_dict()))
            if policy.policy == "SYNC_APPEND_ONLY":
                raise SyncApplyConflict("IDENTITY_PAYLOAD_CONFLICT",
                                        record.entity_kind, record.entity_id,
                                        local_hash=current_hash, remote_hash=record.content_hash,
                                        remote_revision=canonical_json(record.revision.as_dict()))
            plans.append((record, "update", columns, payload))

        # Classification completes before the first mutation. The entire delta
        # is then applied in one SQLite transaction; no partial-success mode.
        for record, action, columns, payload in plans:
            if action == "insert":
                values = [_decode_canonical_sql_value(payload[column]) for column in columns]
                names = ",".join('"' + column + '"' for column in columns)
                placeholders = ",".join("?" for _ in columns)
                connection.execute(
                    f'INSERT INTO "{record.entity_kind}"({names}) VALUES({placeholders})', values)
                applied += 1
            elif action == "update":
                mutable_columns = tuple(column for column in columns
                                        if column != SYNC_ENTITY_POLICIES[record.entity_kind].identity_column)
                assignments = ",".join('"' + column + '"=?' for column in mutable_columns)
                values = [_decode_canonical_sql_value(payload[column]) for column in mutable_columns]
                connection.execute(
                    f'UPDATE "{record.entity_kind}" SET {assignments} '
                    f'WHERE "{SYNC_ENTITY_POLICIES[record.entity_kind].identity_column}"=?',
                    values + [record.entity_id])
                applied += 1
        connection.execute("COMMIT")
        _debug_crash_barrier("B_DOMAIN_COMMITTED")
        connection.close()
        connection = None
    except BaseException as error:
        if connection is not None:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            connection.close()
        if isinstance(error, SyncApplyConflict):
            persist_conflict(error)
        raise

    # A failure here is recoverable: retry classifies committed rows as identical.
    _debug_crash_barrier("C_BEFORE_CHECKPOINT")
    with SyncMetadataStore(sync_metadata_path(private_root), local_device_id) as store:
        store.checkpoint_remote_records(records, peer_id)
    return canonical_json({
        "applied": applied,
        "idempotent": idempotent,
        "persona_id": persona_id,
        "source_device_id": peer_id,
        "status": "APPLIED",
    })


def apply_remote_delta_json(database_path: str | Path, private_root: str | Path,
                            device_id: str, expected_persona_id: str,
                            trusted_peer_device_id: str, delta_json: str) -> str:
    """Fixed Android bridge; parse bounded authenticated plaintext then validate fully."""
    if not isinstance(delta_json, str) or len(delta_json.encode("utf-8")) > MAX_MANIFEST_BYTES:
        raise SyncError("DELTA_SIZE_LIMIT")
    try:
        delta = json.loads(delta_json)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise SyncError("DELTA_MALFORMED") from error
    if not isinstance(delta, dict):
        raise SyncError("DELTA_MALFORMED")
    return apply_remote_delta(database_path, private_root, device_id,
                              expected_persona_id, trusted_peer_device_id, delta)


def list_remote_conflicts_json(private_root: str | Path, device_id: str,
                               persona_id: str, unresolved_only: bool = True) -> str:
    """Return content-free durable conflicts from the device-local sync sidecar."""
    with SyncMetadataStore(sync_metadata_path(private_root), device_id) as store:
        return canonical_json({"conflicts": store.list_conflicts(
            persona_id, unresolved_only=bool(unresolved_only))})
