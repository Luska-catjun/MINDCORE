"""Read-only classification of the Turso schema used by setup entry points.

The contract deliberately describes required capabilities rather than hashing
the complete DDL. Extra tables, columns, constraints, and indexes are allowed.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


CURRENT_TURSO_BASELINE_VERSION = "22"
SCHEMA_VERSION_KEY = "turso_baseline_version"


class SchemaState(str, Enum):
    EMPTY = "EMPTY"
    CURRENT = "CURRENT"
    COMPATIBLE_LEGACY = "COMPATIBLE_LEGACY"
    PARTIAL_OR_UNKNOWN = "PARTIAL_OR_UNKNOWN"


def _columns(value: str) -> frozenset[str]:
    return frozenset(value.split())


# Every listed table is used by a current runtime owner. Columns are the fields
# read or written by those owners; additive schema evolution remains valid.
REQUIRED_COLUMNS: dict[str, frozenset[str]] = {
    "chat_turn_stages": _columns(
        "turn_id stage_name status retry_policy attempt_count started_at completed_at "
        "last_error_category"
    ),
    "chat_turns": _columns(
        "turn_id conversation_id user_message_id assistant_message_id status created_at "
        "updated_at core_completed_at completed_at last_failed_stage safe_error_category"
    ),
    "conversations": _columns("conversation_id source_device started_at ended_at"),
    "decision_log": _columns(
        "id target old_value new_value reason source_episode_ids created_at "
        "conversation_id decision_domain status updated_at resolved_at"
    ),
    "diana_goals": _columns(
        "id goal_key goal_type summary origin_need priority status progress confidence "
        "conversation_id source_type source_id created_at updated_at expires_at metadata"
    ),
    "diana_identity": _columns(
        "id core_identity personality speech_style preferences initial_relationship is_active created_at"
    ),
    "diana_knowledge": _columns(
        "knowledge_id subject_key canonical_name aliases knowledge_type summary confidence status "
        "source_type source_id source_episode_id first_learned_at last_reinforced_at "
        "reinforcement_count created_at updated_at learning_session_count last_learning_session_id"
    ),
    "diana_knowledge_facts": _columns(
        "knowledge_fact_id knowledge_id fact_key fact_text knowledge_scope source_type "
        "source_message_id source_episode_id confidence reinforcement_count contradiction_count "
        "first_learned_at last_reinforced_at last_contradicted_at created_at updated_at"
    ),
    "diana_narrative_evidence": _columns(
        "id evidence_key narrative_id episode_id decision_id preference_evidence_id "
        "emotion_attribution_id memory_id relationship_log_id knowledge_id evidence_type "
        "signal_value created_at"
    ),
    "diana_narratives": _columns(
        "id narrative_key subject_key category summary status confidence evidence_count "
        "distinct_episode_count distinct_conversation_count first_observed_at last_observed_at "
        "created_at updated_at"
    ),
    "diana_need_events": _columns(
        "id need_key delta before_value after_value reason source_type source_id conversation_id "
        "fingerprint created_at"
    ),
    "diana_needs": _columns("need_key value baseline updated_at last_triggered_at metadata"),
    "diana_preference_evidence": _columns(
        "diana_preference_evidence_id diana_preference_id subject_key signal_type signal_value "
        "source_emotion_attribution_id source_experience_id source_message_id created_at episode_id"
    ),
    "diana_preferences": _columns(
        "diana_preference_id subject_key display_name status affinity confidence evidence_count "
        "positive_evidence negative_evidence curiosity_evidence first_observed_at last_observed_at "
        "stabilized_at created_at updated_at"
    ),
    "diana_response_intentions": _columns(
        "id conversation_id user_message_id assistant_message_id action target reason_code "
        "confidence source_refs constraints created_at"
    ),
    "diana_self_model": _columns(
        "id claim_key category subject summary confidence status support_count contradiction_count "
        "conversation_count first_observed_at last_reinforced_at created_at updated_at"
    ),
    "diana_self_model_evidence": _columns(
        "id fingerprint self_model_id evidence_type direction weight source_narrative_id "
        "source_preference_id source_decision_id source_episode_id source_conversation_id created_at"
    ),
    "diana_state": _columns(
        "id mood_valence emotion energy curiosity stress updated_at source_device "
        "emotion_intensity emotion_vector"
    ),
    "diana_working_memory_items": _columns(
        "id conversation_id slot_type item_key summary source_type source_id salience status "
        "created_at last_touched_at expires_at metadata"
    ),
    "emotion_attributions": _columns(
        "emotion_attribution_id emotion delta resulting_value cause_type cause_summary source_type "
        "source_id source_experience_id confidence created_at episode_id"
    ),
    "episodes": _columns(
        "episode_id conversation_id sequence summary embedding importance emotional_impact "
        "personal_relevance relationship_impact novelty confidence recall_frequency memory_strength "
        "decay source_device created_at user_message_id assistant_message_id experience_id "
        "started_at ended_at episode_type topic_key provenance is_grounded updated_at"
    ),
    "experiences": _columns(
        "experience_id conversation_id user_message_id assistant_message_id current_focus "
        "activated_memory_ids state_before state_after state_changed outcome_type created_at"
    ),
    "memories": _columns(
        "memory_id content normalized_content memory_type importance source_conversation_id "
        "source_message_id created_at updated_at recall_frequency memory_strength last_recalled_at "
        "source_episode_id"
    ),
    "messages": _columns("id conversation_id sequence role content source_device created_at"),
    "preference_evidence": _columns(
        "evidence_id preference_id experience_id message_id evidence_type direction strength created_at"
    ),
    "preferences": _columns(
        "preference_id owner_type subject value preference_type status confidence evidence_count "
        "first_seen_at last_seen_at created_at updated_at"
    ),
    "relationship": _columns(
        "id familiarity trust affection shared_experience conflict_history updated_at conflict"
    ),
    "relationship_log": _columns(
        "relationship_log_id source_experience_id previous_state delta new_state reason created_at episode_id"
    ),
    "semantic_facts": _columns(
        "fact_id content embedding confidence source_episode_ids created_at updated_at"
    ),
    "state_log": _columns(
        "id mood_valence emotion energy curiosity stress source_device episode_id created_at "
        "emotion_intensity emotion_vector"
    ),
}


# Unique constraints are idempotence/concurrency authorities, not merely query
# optimizations. Named indexes below cover the current high-volume read paths.
REQUIRED_UNIQUE_CONSTRAINTS: dict[str, tuple[tuple[str, ...], ...]] = {
    "chat_turn_stages": (("turn_id", "stage_name"),),
    "diana_goals": (("goal_key",),),
    "diana_knowledge": (("subject_key",),),
    "diana_knowledge_facts": (("knowledge_id", "fact_key"),),
    "diana_narrative_evidence": (("evidence_key",),),
    "diana_narratives": (("narrative_key",),),
    "diana_need_events": (("fingerprint",),),
    "diana_preference_evidence": (("source_experience_id", "diana_preference_id"),),
    "diana_preferences": (("subject_key",),),
    "diana_self_model": (("claim_key",),),
    "diana_self_model_evidence": (("fingerprint",),),
    "diana_working_memory_items": (("conversation_id", "slot_type", "item_key"),),
    "experiences": (("user_message_id", "assistant_message_id"),),
    "memories": (("normalized_content",),),
    "messages": (("conversation_id", "sequence"),),
    "preference_evidence": (("experience_id", "preference_id"),),
    "preferences": (("owner_type", "subject", "value", "preference_type"),),
    "relationship_log": (("source_experience_id",),),
}

REQUIRED_PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "chat_turn_stages": ("turn_id", "stage_name"),
    "chat_turns": ("turn_id",),
    "conversations": ("conversation_id",),
    "decision_log": ("id",),
    "diana_goals": ("id",),
    "diana_identity": ("id",),
    "diana_knowledge": ("knowledge_id",),
    "diana_knowledge_facts": ("knowledge_fact_id",),
    "diana_narrative_evidence": ("id",),
    "diana_narratives": ("id",),
    "diana_need_events": ("id",),
    "diana_needs": ("need_key",),
    "diana_preference_evidence": ("diana_preference_evidence_id",),
    "diana_preferences": ("diana_preference_id",),
    "diana_response_intentions": ("id",),
    "diana_self_model": ("id",),
    "diana_self_model_evidence": ("id",),
    "diana_state": ("id",),
    "diana_working_memory_items": ("id",),
    "emotion_attributions": ("emotion_attribution_id",),
    "episodes": ("episode_id",),
    "experiences": ("experience_id",),
    "memories": ("memory_id",),
    "messages": ("id",),
    "preference_evidence": ("evidence_id",),
    "preferences": ("preference_id",),
    "relationship": ("id",),
    "relationship_log": ("relationship_log_id",),
    "semantic_facts": ("fact_id",),
    "state_log": ("id",),
}

REQUIRED_READ_INDEXES: dict[str, tuple[tuple[str, ...], ...]] = {
    "chat_turn_stages": (("status", "retry_policy", "attempt_count"),),
    "chat_turns": (("status", "updated_at"),),
    "decision_log": (("conversation_id", "decision_domain", "status", "updated_at"),),
    "diana_goals": (("status", "priority"),),
    "diana_narrative_evidence": (("episode_id",), ("narrative_id", "created_at")),
    "diana_narratives": (("status", "updated_at"), ("subject_key", "category")),
    "diana_response_intentions": (("conversation_id", "created_at"),),
    "diana_self_model": (("status", "updated_at"),),
    "diana_self_model_evidence": (("self_model_id", "created_at"), ("source_episode_id",)),
    "diana_working_memory_items": (("conversation_id", "status", "salience"),),
}


# (child table, child column, parent table, parent column, on-delete action)
REQUIRED_FOREIGN_KEYS: tuple[tuple[str, str, str, str, str], ...] = (
    ("chat_turn_stages", "turn_id", "chat_turns", "turn_id", "CASCADE"),
    ("chat_turns", "conversation_id", "conversations", "conversation_id", "CASCADE"),
    ("chat_turns", "user_message_id", "messages", "id", "SET NULL"),
    ("chat_turns", "assistant_message_id", "messages", "id", "SET NULL"),
    ("diana_goals", "conversation_id", "conversations", "conversation_id", "CASCADE"),
    ("diana_goals", "origin_need", "diana_needs", "need_key", "NO ACTION"),
    ("diana_knowledge", "source_episode_id", "episodes", "episode_id", "NO ACTION"),
    ("diana_knowledge_facts", "knowledge_id", "diana_knowledge", "knowledge_id", "NO ACTION"),
    ("diana_knowledge_facts", "source_episode_id", "episodes", "episode_id", "NO ACTION"),
    ("diana_knowledge_facts", "source_message_id", "messages", "id", "SET NULL"),
    ("diana_narrative_evidence", "narrative_id", "diana_narratives", "id", "CASCADE"),
    ("diana_narrative_evidence", "episode_id", "episodes", "episode_id", "SET NULL"),
    ("diana_narrative_evidence", "decision_id", "decision_log", "id", "SET NULL"),
    (
        "diana_narrative_evidence",
        "preference_evidence_id",
        "diana_preference_evidence",
        "diana_preference_evidence_id",
        "SET NULL",
    ),
    (
        "diana_narrative_evidence",
        "emotion_attribution_id",
        "emotion_attributions",
        "emotion_attribution_id",
        "SET NULL",
    ),
    ("diana_narrative_evidence", "memory_id", "memories", "memory_id", "SET NULL"),
    (
        "diana_narrative_evidence",
        "relationship_log_id",
        "relationship_log",
        "relationship_log_id",
        "SET NULL",
    ),
    ("diana_narrative_evidence", "knowledge_id", "diana_knowledge", "knowledge_id", "SET NULL"),
    ("diana_need_events", "need_key", "diana_needs", "need_key", "CASCADE"),
    ("diana_need_events", "conversation_id", "conversations", "conversation_id", "SET NULL"),
    (
        "diana_preference_evidence",
        "diana_preference_id",
        "diana_preferences",
        "diana_preference_id",
        "NO ACTION",
    ),
    (
        "diana_preference_evidence",
        "source_emotion_attribution_id",
        "emotion_attributions",
        "emotion_attribution_id",
        "NO ACTION",
    ),
    (
        "diana_preference_evidence",
        "source_experience_id",
        "experiences",
        "experience_id",
        "CASCADE",
    ),
    ("diana_preference_evidence", "source_message_id", "messages", "id", "SET NULL"),
    ("diana_preference_evidence", "episode_id", "episodes", "episode_id", "NO ACTION"),
    ("diana_response_intentions", "conversation_id", "conversations", "conversation_id", "CASCADE"),
    ("diana_response_intentions", "user_message_id", "messages", "id", "SET NULL"),
    ("diana_response_intentions", "assistant_message_id", "messages", "id", "SET NULL"),
    ("diana_self_model_evidence", "self_model_id", "diana_self_model", "id", "CASCADE"),
    (
        "diana_self_model_evidence",
        "source_narrative_id",
        "diana_narratives",
        "id",
        "SET NULL",
    ),
    (
        "diana_self_model_evidence",
        "source_preference_id",
        "diana_preferences",
        "diana_preference_id",
        "SET NULL",
    ),
    ("diana_self_model_evidence", "source_decision_id", "decision_log", "id", "SET NULL"),
    ("diana_self_model_evidence", "source_episode_id", "episodes", "episode_id", "SET NULL"),
    (
        "diana_self_model_evidence",
        "source_conversation_id",
        "conversations",
        "conversation_id",
        "SET NULL",
    ),
    ("diana_working_memory_items", "conversation_id", "conversations", "conversation_id", "CASCADE"),
    ("emotion_attributions", "source_experience_id", "experiences", "experience_id", "SET NULL"),
    ("emotion_attributions", "episode_id", "episodes", "episode_id", "NO ACTION"),
    ("messages", "conversation_id", "conversations", "conversation_id", "CASCADE"),
    ("experiences", "conversation_id", "conversations", "conversation_id", "NO ACTION"),
    ("experiences", "user_message_id", "messages", "id", "CASCADE"),
    ("experiences", "assistant_message_id", "messages", "id", "CASCADE"),
    ("episodes", "conversation_id", "conversations", "conversation_id", "SET NULL"),
    ("episodes", "user_message_id", "messages", "id", "SET NULL"),
    ("episodes", "assistant_message_id", "messages", "id", "SET NULL"),
    ("episodes", "experience_id", "experiences", "experience_id", "SET NULL"),
    ("memories", "source_conversation_id", "conversations", "conversation_id", "NO ACTION"),
    ("memories", "source_message_id", "messages", "id", "SET NULL"),
    ("memories", "source_episode_id", "episodes", "episode_id", "NO ACTION"),
    ("preference_evidence", "preference_id", "preferences", "preference_id", "NO ACTION"),
    ("preference_evidence", "experience_id", "experiences", "experience_id", "CASCADE"),
    ("preference_evidence", "message_id", "messages", "id", "CASCADE"),
    ("relationship_log", "source_experience_id", "experiences", "experience_id", "SET NULL"),
    ("relationship_log", "episode_id", "episodes", "episode_id", "NO ACTION"),
    ("state_log", "episode_id", "episodes", "episode_id", "NO ACTION"),
)


@dataclass(frozen=True)
class SchemaReport:
    state: SchemaState
    version: str | None = None
    version_issue: str | None = None
    missing_tables: tuple[str, ...] = ()
    missing_columns: tuple[str, ...] = ()
    missing_constraints: tuple[str, ...] = ()
    invariant_errors: tuple[str, ...] = ()

    def details(self) -> str:
        """Return identifiers only; never include rows, values, or credentials."""
        parts = [self.state.value]
        if self.version_issue:
            parts.append(f"version={self.version_issue}")
        if self.missing_tables:
            parts.append(f"missing_tables={','.join(self.missing_tables)}")
        if self.missing_columns:
            parts.append(f"missing_columns={','.join(self.missing_columns)}")
        if self.missing_constraints:
            parts.append(f"missing_constraints={','.join(self.missing_constraints)}")
        if self.invariant_errors:
            parts.append(f"invariants={','.join(self.invariant_errors)}")
        return " ".join(parts)


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


async def _index_signatures(connection: Any, table: str) -> tuple[set[tuple[str, ...]], set[tuple[str, ...]]]:
    indexes: set[tuple[str, ...]] = set()
    unique_indexes: set[tuple[str, ...]] = set()
    for item in await connection.fetch(f"pragma index_list({_quote_identifier(table)})"):
        if int(item["partial"]):
            continue
        name = str(item["name"])
        columns = tuple(
            str(column["name"])
            for column in sorted(
                await connection.fetch(f"pragma index_info({_quote_identifier(name)})"),
                key=lambda column: int(column["seqno"]),
            )
        )
        indexes.add(columns)
        if int(item["unique"]):
            unique_indexes.add(columns)
    return indexes, unique_indexes


async def classify_turso_schema(connection: Any) -> SchemaReport:
    """Classify a connected libSQL database without changing it."""
    table_rows = await connection.fetch(
        "select name from sqlite_master where type='table' and name not like 'sqlite_%'"
    )
    tables = {str(row["name"]) for row in table_rows}
    if not tables:
        return SchemaReport(SchemaState.EMPTY)

    required_tables = set(REQUIRED_COLUMNS)
    missing_tables = tuple(sorted(required_tables - tables))
    missing_columns: list[str] = []
    missing_constraints: list[str] = []
    invariant_errors: list[str] = []
    for table in sorted(required_tables & tables):
        info = await connection.fetch(f"pragma table_info({_quote_identifier(table)})")
        columns = {str(item["name"]): int(item["notnull"]) for item in info}
        missing_columns.extend(
            f"{table}.{column}" for column in sorted(REQUIRED_COLUMNS[table] - columns.keys())
        )
        primary_key = tuple(
            str(item["name"])
            for item in sorted(
                (item for item in info if int(item["pk"])),
                key=lambda item: int(item["pk"]),
            )
        )
        expected_primary_key = REQUIRED_PRIMARY_KEYS[table]
        if primary_key != expected_primary_key:
            missing_constraints.append(f"{table} PRIMARY KEY({','.join(expected_primary_key)})")

        indexes, unique_indexes = await _index_signatures(connection, table)
        for signature in REQUIRED_UNIQUE_CONSTRAINTS.get(table, ()):
            if signature not in unique_indexes:
                missing_constraints.append(f"{table} UNIQUE({','.join(signature)})")
        for signature in REQUIRED_READ_INDEXES.get(table, ()):
            if signature not in indexes:
                missing_constraints.append(f"{table} INDEX({','.join(signature)})")

    for table, child, parent, parent_column, action in REQUIRED_FOREIGN_KEYS:
        if table not in tables:
            continue
        actual = {
            (
                str(item["from"]),
                str(item["table"]),
                str(item["to"]),
                str(item["on_delete"]).upper(),
            )
            for item in await connection.fetch(f"pragma foreign_key_list({_quote_identifier(table)})")
        }
        expected = (child, parent, parent_column, action)
        if expected not in actual:
            missing_constraints.append(
                f"{table}.{child} FK({parent}.{parent_column}) ON DELETE {action}"
            )

    # Every SET NULL link must target a nullable child, including additive
    # future tables not yet named by this contract.
    for table in sorted(tables):
        if table.endswith(("__cascade", "_new", "_old")):
            invariant_errors.append(f"temporary_table:{table}")

    for table in sorted(tables):
        info = await connection.fetch(f"pragma table_info({_quote_identifier(table)})")
        nullable = {str(item["name"]): not bool(item["notnull"]) for item in info}
        for item in await connection.fetch(f"pragma foreign_key_list({_quote_identifier(table)})"):
            child = str(item["from"])
            if str(item["on_delete"]).upper() == "SET NULL" and not nullable.get(child, False):
                invariant_errors.append(f"set_null_not_nullable:{table}.{child}")

    try:
        if await connection.fetch("pragma foreign_key_check"):
            invariant_errors.append("foreign_key_check")
    except Exception:
        invariant_errors.append("foreign_key_check_error")
    try:
        integrity = await connection.fetchval("pragma integrity_check")
        if integrity != "ok":
            invariant_errors.append("integrity_check")
    except Exception:
        invariant_errors.append("integrity_check_error")

    version: str | None = None
    version_issue: str | None = None
    has_metadata = "schema_metadata" in tables
    if has_metadata:
        metadata_info = await connection.fetch('pragma table_info("schema_metadata")')
        metadata_columns = {str(item["name"]) for item in metadata_info}
        missing_columns.extend(
            f"schema_metadata.{column}"
            for column in sorted({"key", "value"} - metadata_columns)
        )
        metadata_primary_key = tuple(
            str(item["name"])
            for item in sorted(
                (item for item in metadata_info if int(item["pk"])),
                key=lambda item: int(item["pk"]),
            )
        )
        if metadata_primary_key != ("key",):
            missing_constraints.append("schema_metadata PRIMARY KEY(key)")
        if not {"key", "value"}.issubset(metadata_columns):
            version_issue = "invalid_metadata_table"
        else:
            version_value = await connection.fetchval(
                "select value from schema_metadata where key=$1", SCHEMA_VERSION_KEY
            )
            version = str(version_value) if version_value is not None else None
            if version is None:
                version_issue = "missing"
            elif version != CURRENT_TURSO_BASELINE_VERSION:
                version_issue = f"unsupported:{version}"

    contract_errors = bool(
        missing_tables or missing_columns or missing_constraints or invariant_errors or version_issue
    )
    if contract_errors:
        state = SchemaState.PARTIAL_OR_UNKNOWN
    elif has_metadata:
        state = SchemaState.CURRENT
    else:
        state = SchemaState.COMPATIBLE_LEGACY

    return SchemaReport(
        state=state,
        version=version,
        version_issue=version_issue,
        missing_tables=missing_tables,
        missing_columns=tuple(sorted(missing_columns)),
        missing_constraints=tuple(sorted(missing_constraints)),
        invariant_errors=tuple(sorted(set(invariant_errors))),
    )
