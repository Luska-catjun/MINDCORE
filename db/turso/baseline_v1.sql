-- Turso fresh baseline v1 (current production schema after schema-drift repair).
-- Historical db/migrations/001..021 are not a fresh-Turso bootstrap chain.
-- Future Turso forward migrations start at 022.
CREATE TABLE schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO schema_metadata(key,value) VALUES ('turso_baseline_version','21');

-- OBJECT table conversations
CREATE TABLE "conversations" ("conversation_id" TEXT NOT NULL, "source_device" TEXT NOT NULL, "started_at" TEXT NOT NULL, "ended_at" TEXT, PRIMARY KEY ("conversation_id"));
-- OBJECT table decision_log
CREATE TABLE "decision_log" ("id" TEXT NOT NULL, "target" TEXT NOT NULL, "old_value" TEXT, "new_value" TEXT, "reason" TEXT, "source_episode_ids" TEXT NOT NULL, "created_at" TEXT NOT NULL, conversation_id text, decision_domain text, status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'executed', 'superseded', 'cancelled', 'expired')), updated_at text, resolved_at text, PRIMARY KEY ("id"));
-- OBJECT table diana_goals
CREATE TABLE diana_goals (
  id text primary key, goal_key text not null unique, goal_type text not null, summary text not null,
  origin_need text not null references diana_needs(need_key), priority real not null check(priority between 0 and 1),
  status text not null check(status in ('candidate','active','satisfied','abandoned','expired')), progress real not null check(progress between 0 and 1), confidence real not null check(confidence between 0 and 1),
  conversation_id text references conversations(conversation_id) on delete cascade, source_type text not null, source_id text,
  created_at text not null, updated_at text not null, expires_at text, metadata text not null default '{}'
);
-- OBJECT table diana_identity
CREATE TABLE "diana_identity" ("id" TEXT NOT NULL, "core_identity" TEXT NOT NULL, "personality" TEXT NOT NULL, "speech_style" TEXT, "preferences" TEXT, "initial_relationship" TEXT, "is_active" INTEGER NOT NULL, "created_at" TEXT NOT NULL, PRIMARY KEY ("id"));
-- OBJECT table diana_knowledge
CREATE TABLE "diana_knowledge" ("knowledge_id" TEXT NOT NULL, "subject_key" TEXT NOT NULL, "canonical_name" TEXT NOT NULL, "aliases" TEXT NOT NULL, "knowledge_type" TEXT NOT NULL, "summary" TEXT NOT NULL, "confidence" REAL NOT NULL, "status" TEXT NOT NULL, "source_type" TEXT NOT NULL, "source_id" TEXT, "source_episode_id" TEXT, "first_learned_at" TEXT NOT NULL, "last_reinforced_at" TEXT NOT NULL, "reinforcement_count" INTEGER NOT NULL, "created_at" TEXT NOT NULL, "updated_at" TEXT NOT NULL, "learning_session_count" INTEGER NOT NULL, "last_learning_session_id" TEXT, PRIMARY KEY ("knowledge_id"), UNIQUE ("subject_key"), FOREIGN KEY ("source_episode_id") REFERENCES "episodes" ("episode_id"));
-- OBJECT table diana_knowledge_facts
CREATE TABLE "diana_knowledge_facts" ("knowledge_fact_id" TEXT NOT NULL, "knowledge_id" TEXT NOT NULL, "fact_key" TEXT NOT NULL, "fact_text" TEXT NOT NULL, "knowledge_scope" TEXT NOT NULL, "source_type" TEXT NOT NULL, "source_message_id" TEXT, "source_episode_id" TEXT, "confidence" REAL NOT NULL, "reinforcement_count" INTEGER NOT NULL, "contradiction_count" INTEGER NOT NULL, "first_learned_at" TEXT NOT NULL, "last_reinforced_at" TEXT NOT NULL, "last_contradicted_at" TEXT, "created_at" TEXT NOT NULL, "updated_at" TEXT NOT NULL, PRIMARY KEY ("knowledge_fact_id"), UNIQUE ("knowledge_id", "fact_key"), FOREIGN KEY ("knowledge_id") REFERENCES "diana_knowledge" ("knowledge_id"), FOREIGN KEY ("source_message_id") REFERENCES "messages" ("id") ON DELETE SET NULL, FOREIGN KEY ("source_episode_id") REFERENCES "episodes" ("episode_id"));
-- OBJECT table diana_narrative_evidence
CREATE TABLE diana_narrative_evidence (
    id text primary key,
    evidence_key text not null unique,
    narrative_id text not null references diana_narratives(id) on delete cascade,
    episode_id text references episodes(episode_id) on delete set null,
    decision_id text references decision_log(id) on delete set null,
    preference_evidence_id text references diana_preference_evidence(diana_preference_evidence_id) on delete set null,
    emotion_attribution_id text references emotion_attributions(emotion_attribution_id) on delete set null,
    memory_id text references memories(memory_id) on delete set null,
    relationship_log_id text references relationship_log(relationship_log_id) on delete set null,
    knowledge_id text references diana_knowledge(knowledge_id) on delete set null,
    evidence_type text not null,
    signal_value real not null,
    created_at text not null
);
-- OBJECT table diana_narratives
CREATE TABLE diana_narratives (
    id text primary key,
    narrative_key text not null unique,
    subject_key text not null,
    category text not null check (category in ('activity_pattern','choice_pattern','interest_pattern','learning_pattern','social_pattern','emotional_pattern','relationship_pattern','routine_pattern')),
    summary text not null,
    status text not null check (status in ('candidate','emerging','established')),
    confidence real not null check (confidence between 0 and 1),
    evidence_count integer not null default 0,
    distinct_episode_count integer not null default 0,
    distinct_conversation_count integer not null default 0,
    first_observed_at text not null,
    last_observed_at text not null,
    created_at text not null,
    updated_at text not null
);
-- OBJECT table diana_need_events
CREATE TABLE diana_need_events (
  id text primary key, need_key text not null references diana_needs(need_key) on delete cascade,
  delta real not null, before_value real not null, after_value real not null, reason text not null,
  source_type text not null, source_id text, conversation_id text references conversations(conversation_id) on delete set null,
  fingerprint text not null unique, created_at text not null
);
-- OBJECT table diana_needs
CREATE TABLE diana_needs (
  need_key text primary key check(need_key in ('curiosity','understanding','social_connection','activity','helpfulness','autonomy')),
  value real not null check(value between 0 and 1), baseline real not null check(baseline between 0 and 1),
  updated_at text not null, last_triggered_at text, metadata text not null default '{}'
);
-- OBJECT table diana_preference_evidence
CREATE TABLE "diana_preference_evidence" ("diana_preference_evidence_id" TEXT NOT NULL, "diana_preference_id" TEXT NOT NULL, "subject_key" TEXT NOT NULL, "signal_type" TEXT NOT NULL, "signal_value" REAL NOT NULL, "source_emotion_attribution_id" TEXT, "source_experience_id" TEXT NOT NULL, "source_message_id" TEXT, "created_at" TEXT NOT NULL, "episode_id" TEXT, PRIMARY KEY ("diana_preference_evidence_id"), UNIQUE ("source_experience_id", "diana_preference_id"), FOREIGN KEY ("diana_preference_id") REFERENCES "diana_preferences" ("diana_preference_id"), FOREIGN KEY ("source_emotion_attribution_id") REFERENCES "emotion_attributions" ("emotion_attribution_id"), FOREIGN KEY ("source_experience_id") REFERENCES "experiences" ("experience_id") ON DELETE CASCADE, FOREIGN KEY ("source_message_id") REFERENCES "messages" ("id") ON DELETE SET NULL, FOREIGN KEY ("episode_id") REFERENCES "episodes" ("episode_id"));
-- OBJECT table diana_preferences
CREATE TABLE "diana_preferences" ("diana_preference_id" TEXT NOT NULL, "subject_key" TEXT NOT NULL, "display_name" TEXT NOT NULL, "status" TEXT NOT NULL, "affinity" REAL NOT NULL, "confidence" REAL NOT NULL, "evidence_count" INTEGER NOT NULL, "positive_evidence" INTEGER NOT NULL, "negative_evidence" INTEGER NOT NULL, "curiosity_evidence" INTEGER NOT NULL, "first_observed_at" TEXT NOT NULL, "last_observed_at" TEXT NOT NULL, "stabilized_at" TEXT, "created_at" TEXT NOT NULL, "updated_at" TEXT NOT NULL, PRIMARY KEY ("diana_preference_id"), UNIQUE ("subject_key"));
-- OBJECT table diana_response_intentions
CREATE TABLE diana_response_intentions (
 id text primary key, conversation_id text not null references conversations(conversation_id) on delete cascade,
 user_message_id text references messages(id) on delete set null, assistant_message_id text references messages(id) on delete set null,
 action text not null check(action in ('answer','fulfill_request','clarify','choose','acknowledge','ask_followup')),
 target text, reason_code text not null, confidence real not null check(confidence between 0 and 1),
 source_refs text not null default '{}', constraints text not null default '[]', created_at text not null
);
-- OBJECT table diana_self_model
CREATE TABLE diana_self_model (
    id text primary key,
    claim_key text not null unique,
    category text not null check (category in ('preference_self','decision_tendency','narrative_theme')),
    subject text not null,
    summary text not null,
    confidence real not null check (confidence between 0 and 1),
    status text not null check (status in ('candidate','emerging','established')),
    support_count integer not null default 0,
    contradiction_count integer not null default 0,
    conversation_count integer not null default 0,
    first_observed_at text not null,
    last_reinforced_at text not null,
    created_at text not null,
    updated_at text not null
);
-- OBJECT table diana_self_model_evidence
CREATE TABLE "diana_self_model_evidence" (
                    id text primary key, fingerprint text not null unique,
                    self_model_id text not null references diana_self_model(id) on delete cascade,
                    evidence_type text not null, direction text not null check (direction in ('support','contradict')),
                    weight real not null check (weight > 0 and weight <= 1),
                    source_narrative_id text references diana_narratives(id) on delete set null,
                    source_preference_id text references diana_preferences(diana_preference_id) on delete set null,
                    source_decision_id text references decision_log(id) on delete set null,
                    source_episode_id text references episodes(episode_id) on delete set null,
                    source_conversation_id text references conversations(conversation_id) on delete set null,
                    created_at text not null);
-- OBJECT table diana_state
CREATE TABLE "diana_state" ("id" INTEGER NOT NULL, "mood_valence" REAL, "emotion" TEXT, "energy" REAL, "curiosity" REAL, "stress" REAL, "updated_at" TEXT NOT NULL, "source_device" TEXT, "emotion_intensity" REAL NOT NULL, "emotion_vector" TEXT NOT NULL, PRIMARY KEY ("id"));
-- OBJECT table diana_working_memory_items
CREATE TABLE "diana_working_memory_items" (
 id text primary key, conversation_id text not null references conversations(conversation_id) on delete cascade,
 slot_type text not null check(slot_type in ('active_topic','open_loop','active_memory_ref','active_skill')), item_key text not null, summary text not null,
 source_type text not null, source_id text, salience real not null check(salience between 0 and 1), status text not null check(status in ('active','resolved','expired')),
 created_at text not null, last_touched_at text not null, expires_at text not null, metadata text not null default '{}', unique(conversation_id,slot_type,item_key)
);
-- OBJECT table emotion_attributions
CREATE TABLE "emotion_attributions" ("emotion_attribution_id" TEXT NOT NULL, "emotion" TEXT NOT NULL, "delta" REAL NOT NULL, "resulting_value" REAL NOT NULL, "cause_type" TEXT NOT NULL, "cause_summary" TEXT NOT NULL, "source_type" TEXT NOT NULL, "source_id" TEXT, "source_experience_id" TEXT, "confidence" REAL NOT NULL, "created_at" TEXT NOT NULL, "episode_id" TEXT, PRIMARY KEY ("emotion_attribution_id"), FOREIGN KEY ("source_experience_id") REFERENCES "experiences" ("experience_id") ON DELETE SET NULL ON DELETE SET NULL, FOREIGN KEY ("episode_id") REFERENCES "episodes" ("episode_id"));
-- OBJECT table episodes
CREATE TABLE "episodes" ("episode_id" TEXT NOT NULL, "conversation_id" TEXT, "sequence" INTEGER NOT NULL, "summary" TEXT NOT NULL, "embedding" F32_BLOB(1536), "importance" REAL, "emotional_impact" REAL, "personal_relevance" REAL, "relationship_impact" REAL, "novelty" REAL, "confidence" REAL, "recall_frequency" INTEGER NOT NULL, "memory_strength" REAL, "decay" REAL, "source_device" TEXT NOT NULL, "created_at" TEXT NOT NULL, "user_message_id" TEXT, "assistant_message_id" TEXT, "experience_id" TEXT, "started_at" TEXT, "ended_at" TEXT, "episode_type" TEXT NOT NULL, "topic_key" TEXT, "provenance" TEXT NOT NULL, "is_grounded" INTEGER NOT NULL, "updated_at" TEXT NOT NULL, PRIMARY KEY ("episode_id"), FOREIGN KEY ("conversation_id") REFERENCES "conversations" ("conversation_id") ON DELETE SET NULL, FOREIGN KEY ("user_message_id") REFERENCES "messages" ("id") ON DELETE SET NULL, FOREIGN KEY ("assistant_message_id") REFERENCES "messages" ("id") ON DELETE SET NULL, FOREIGN KEY ("experience_id") REFERENCES "experiences" ("experience_id") ON DELETE SET NULL);
-- OBJECT table experiences
CREATE TABLE "experiences" ("experience_id" TEXT NOT NULL, "conversation_id" TEXT NOT NULL, "user_message_id" TEXT NOT NULL, "assistant_message_id" TEXT NOT NULL, "current_focus" TEXT, "activated_memory_ids" TEXT NOT NULL, "state_before" TEXT, "state_after" TEXT, "state_changed" INTEGER NOT NULL, "outcome_type" TEXT NOT NULL, "created_at" TEXT NOT NULL, PRIMARY KEY ("experience_id"), UNIQUE ("user_message_id", "assistant_message_id"), FOREIGN KEY ("conversation_id") REFERENCES "conversations" ("conversation_id"), FOREIGN KEY ("user_message_id") REFERENCES "messages" ("id") ON DELETE CASCADE, FOREIGN KEY ("assistant_message_id") REFERENCES "messages" ("id") ON DELETE CASCADE);
-- OBJECT table memories
CREATE TABLE "memories" ("memory_id" TEXT NOT NULL, "content" TEXT NOT NULL, "normalized_content" TEXT NOT NULL, "memory_type" TEXT NOT NULL, "importance" REAL NOT NULL, "source_conversation_id" TEXT, "source_message_id" TEXT, "created_at" TEXT NOT NULL, "updated_at" TEXT NOT NULL, "recall_frequency" INTEGER NOT NULL, "memory_strength" REAL NOT NULL, "last_recalled_at" TEXT, "source_episode_id" TEXT, PRIMARY KEY ("memory_id"), UNIQUE ("normalized_content"), FOREIGN KEY ("source_conversation_id") REFERENCES "conversations" ("conversation_id"), FOREIGN KEY ("source_message_id") REFERENCES "messages" ("id") ON DELETE SET NULL, FOREIGN KEY ("source_episode_id") REFERENCES "episodes" ("episode_id"));
-- OBJECT table messages
CREATE TABLE "messages" ("id" TEXT NOT NULL, "conversation_id" TEXT NOT NULL, "sequence" INTEGER NOT NULL, "role" TEXT NOT NULL, "content" TEXT NOT NULL, "source_device" TEXT NOT NULL, "created_at" TEXT NOT NULL, PRIMARY KEY ("id"), UNIQUE ("conversation_id", "sequence"), FOREIGN KEY ("conversation_id") REFERENCES "conversations" ("conversation_id") ON DELETE CASCADE);
-- OBJECT table preference_evidence
CREATE TABLE "preference_evidence" ("evidence_id" TEXT NOT NULL, "preference_id" TEXT NOT NULL, "experience_id" TEXT NOT NULL, "message_id" TEXT NOT NULL, "evidence_type" TEXT NOT NULL, "direction" INTEGER NOT NULL, "strength" REAL NOT NULL, "created_at" TEXT NOT NULL, PRIMARY KEY ("evidence_id"), UNIQUE ("experience_id", "preference_id"), FOREIGN KEY ("preference_id") REFERENCES "preferences" ("preference_id"), FOREIGN KEY ("experience_id") REFERENCES "experiences" ("experience_id") ON DELETE CASCADE, FOREIGN KEY ("message_id") REFERENCES "messages" ("id") ON DELETE CASCADE);
-- OBJECT table preferences
CREATE TABLE "preferences" ("preference_id" TEXT NOT NULL, "owner_type" TEXT NOT NULL, "subject" TEXT NOT NULL, "value" TEXT NOT NULL, "preference_type" TEXT NOT NULL, "status" TEXT NOT NULL, "confidence" REAL NOT NULL, "evidence_count" INTEGER NOT NULL, "first_seen_at" TEXT NOT NULL, "last_seen_at" TEXT NOT NULL, "created_at" TEXT NOT NULL, "updated_at" TEXT NOT NULL, PRIMARY KEY ("preference_id"), UNIQUE ("owner_type", "subject", "value", "preference_type"));
-- OBJECT table relationship
CREATE TABLE "relationship" ("id" INTEGER NOT NULL, "familiarity" REAL, "trust" REAL, "affection" REAL, "shared_experience" REAL, "conflict_history" TEXT NOT NULL, "updated_at" TEXT NOT NULL, "conflict" REAL NOT NULL, PRIMARY KEY ("id"));
-- OBJECT table relationship_log
CREATE TABLE "relationship_log" ("relationship_log_id" TEXT NOT NULL, "source_experience_id" TEXT, "previous_state" TEXT NOT NULL, "delta" TEXT NOT NULL, "new_state" TEXT NOT NULL, "reason" TEXT NOT NULL, "created_at" TEXT NOT NULL, "episode_id" TEXT, PRIMARY KEY ("relationship_log_id"), UNIQUE ("source_experience_id"), FOREIGN KEY ("source_experience_id") REFERENCES "experiences" ("experience_id") ON DELETE SET NULL, FOREIGN KEY ("episode_id") REFERENCES "episodes" ("episode_id"));
-- OBJECT table semantic_facts
CREATE TABLE "semantic_facts" ("fact_id" TEXT NOT NULL, "content" TEXT NOT NULL, "embedding" F32_BLOB(1536), "confidence" REAL, "source_episode_ids" TEXT NOT NULL, "created_at" TEXT NOT NULL, "updated_at" TEXT NOT NULL, PRIMARY KEY ("fact_id"));
-- OBJECT table state_log
CREATE TABLE "state_log" ("id" TEXT NOT NULL, "mood_valence" REAL, "emotion" TEXT, "energy" REAL, "curiosity" REAL, "stress" REAL, "source_device" TEXT NOT NULL, "episode_id" TEXT, "created_at" TEXT NOT NULL, "emotion_intensity" REAL NOT NULL, "emotion_vector" TEXT NOT NULL, PRIMARY KEY ("id"), FOREIGN KEY ("episode_id") REFERENCES "episodes" ("episode_id"));
-- OBJECT index idx_decision_log_lifecycle
CREATE INDEX idx_decision_log_lifecycle ON decision_log (conversation_id, decision_domain, status, updated_at DESC);
-- OBJECT index idx_diana_narrative_evidence_episode
CREATE INDEX idx_diana_narrative_evidence_episode ON diana_narrative_evidence (episode_id);
-- OBJECT index idx_diana_narrative_evidence_narrative
CREATE INDEX idx_diana_narrative_evidence_narrative ON diana_narrative_evidence (narrative_id, created_at DESC);
-- OBJECT index idx_diana_narratives_status_updated
CREATE INDEX idx_diana_narratives_status_updated ON diana_narratives (status, updated_at DESC);
-- OBJECT index idx_diana_narratives_subject
CREATE INDEX idx_diana_narratives_subject ON diana_narratives (subject_key, category);
-- OBJECT index idx_diana_self_model_evidence_belief
CREATE INDEX idx_diana_self_model_evidence_belief ON diana_self_model_evidence (self_model_id, created_at DESC);
-- OBJECT index idx_diana_self_model_evidence_episode
CREATE INDEX idx_diana_self_model_evidence_episode ON diana_self_model_evidence (source_episode_id);
-- OBJECT index idx_diana_self_model_status_updated
CREATE INDEX idx_diana_self_model_status_updated ON diana_self_model (status, updated_at DESC);
-- OBJECT index idx_goals_active
CREATE INDEX idx_goals_active ON diana_goals (status, priority DESC);
-- OBJECT index idx_response_intentions_conversation
CREATE INDEX idx_response_intentions_conversation ON diana_response_intentions (conversation_id, created_at DESC);
-- OBJECT index idx_wm_conversation_active
CREATE INDEX idx_wm_conversation_active ON diana_working_memory_items (conversation_id, status, salience DESC);

