-- Self Model v0.1 is an additive observation-only shadow layer.  The source
-- links deliberately outlive their source records (SET NULL), while a belief
-- owns and cascades its evidence.
create table if not exists diana_self_model (
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

create table if not exists diana_self_model_evidence (
    id text primary key,
    fingerprint text not null unique,
    self_model_id text not null references diana_self_model(id) on delete cascade,
    evidence_type text not null,
    direction text not null check (direction in ('support','contradict')),
    weight real not null check (weight > 0 and weight <= 1),
    source_narrative_id text references diana_narratives(id) on delete set null,
    source_preference_id text references diana_preferences(diana_preference_id) on delete set null,
    source_decision_id text references decision_log(id) on delete set null,
    source_episode_id text references episodes(episode_id) on delete set null,
    source_conversation_id text references conversations(conversation_id) on delete set null,
    created_at text not null
);

create index if not exists idx_diana_self_model_status_updated on diana_self_model(status, updated_at desc);
create index if not exists idx_diana_self_model_evidence_belief on diana_self_model_evidence(self_model_id, created_at desc);
create index if not exists idx_diana_self_model_evidence_episode on diana_self_model_evidence(source_episode_id);
