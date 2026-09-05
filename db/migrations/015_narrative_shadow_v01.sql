-- Narrative v0.1 is an additive, read-only shadow layer.  IDs and UTC
-- timestamps are supplied by the application so this works on Turso/libSQL.
create table if not exists diana_narratives (
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

create table if not exists diana_narrative_evidence (
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

create index if not exists idx_diana_narratives_status_updated on diana_narratives(status, updated_at desc);
create index if not exists idx_diana_narratives_subject on diana_narratives(subject_key, category);
create index if not exists idx_diana_narrative_evidence_narrative on diana_narrative_evidence(narrative_id, created_at desc);
create index if not exists idx_diana_narrative_evidence_episode on diana_narrative_evidence(episode_id);
