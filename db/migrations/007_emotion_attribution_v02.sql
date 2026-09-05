-- Immutable, deterministic causes for the existing diana_state singleton.
-- This migration deliberately keeps the v0.1 scalar state columns unchanged.
create table if not exists emotion_attributions (
    emotion_attribution_id uuid primary key default gen_random_uuid(),
    emotion text not null check (emotion in (
        'neutral', 'joy', 'interest', 'concern', 'sadness', 'frustration', 'surprise'
    )),
    delta double precision not null check (delta between -1 and 1),
    resulting_value double precision not null check (resulting_value between 0 and 1),
    cause_type text not null check (cause_type in (
        'praise', 'positive_user_event', 'user_difficulty',
        'interpersonal_negative', 'inquiry', 'time_decay', 'unknown'
    )),
    cause_summary text not null,
    source_type text not null check (source_type in (
        'message', 'experience', 'relationship_event', 'memory_recall', 'system', 'unknown'
    )),
    source_id uuid,
    source_experience_id uuid references experiences(experience_id) on delete set null,
    confidence double precision not null check (confidence between 0 and 1),
    created_at timestamptz not null default now()
);

create index if not exists idx_emotion_attributions_created
    on emotion_attributions (created_at desc);

create index if not exists idx_emotion_attributions_emotion_created
    on emotion_attributions (emotion, created_at desc);

create unique index if not exists uq_emotion_attributions_message_event
    on emotion_attributions (source_type, source_id, emotion, cause_type)
    where source_type = 'message' and source_id is not null;
