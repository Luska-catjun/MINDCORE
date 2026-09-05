-- Diana's own evidence-based preferences. These are intentionally separate
-- from the existing user-owned preferences and preference_evidence tables.
create table if not exists diana_preferences (
    diana_preference_id uuid primary key default gen_random_uuid(),
    subject_key text not null unique,
    display_name text not null,
    status text not null check (status in ('curious', 'tentative', 'stable')),
    affinity double precision not null default 0 check (affinity between -1 and 1),
    confidence double precision not null default 0 check (confidence between 0 and 1),
    evidence_count integer not null default 0 check (evidence_count >= 0),
    positive_evidence integer not null default 0 check (positive_evidence >= 0),
    negative_evidence integer not null default 0 check (negative_evidence >= 0),
    curiosity_evidence integer not null default 0 check (curiosity_evidence >= 0),
    first_observed_at timestamptz not null default now(),
    last_observed_at timestamptz not null default now(),
    stabilized_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists diana_preference_evidence (
    diana_preference_evidence_id uuid primary key default gen_random_uuid(),
    diana_preference_id uuid not null references diana_preferences(diana_preference_id) on delete cascade,
    subject_key text not null,
    signal_type text not null check (signal_type in ('curiosity', 'positive', 'negative')),
    signal_value double precision not null check (signal_value between -1 and 1 and signal_value <> 0),
    source_emotion_attribution_id uuid references emotion_attributions(emotion_attribution_id) on delete set null,
    source_experience_id uuid not null references experiences(experience_id) on delete cascade,
    source_message_id uuid references messages(id) on delete set null,
    created_at timestamptz not null default now(),
    unique (source_experience_id, diana_preference_id)
);

create index if not exists idx_diana_preferences_status_updated
    on diana_preferences (status, updated_at desc);
create index if not exists idx_diana_preference_evidence_preference
    on diana_preference_evidence (diana_preference_id, created_at desc);
create index if not exists idx_diana_preference_evidence_experience
    on diana_preference_evidence (source_experience_id);
