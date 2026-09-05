-- Diana's acquired conceptual knowledge. A missing row means unknown, not a
-- pre-populated universe of unknown concepts.
create table if not exists diana_knowledge (
    knowledge_id uuid primary key default gen_random_uuid(),
    subject_key text not null unique,
    canonical_name text not null,
    aliases jsonb not null default '[]'::jsonb,
    knowledge_type text not null default 'concept' check (knowledge_type in (
        'concept', 'fact', 'person', 'place', 'object', 'event', 'story',
        'game', 'animal', 'science', 'other'
    )),
    summary text not null,
    confidence double precision not null default 0.45 check (confidence between 0 and 1),
    status text not null default 'introduced' check (status in ('introduced', 'known', 'well_known')),
    source_type text not null check (source_type in ('user_message', 'trusted_system', 'approved_external')),
    source_id uuid,
    source_episode_id uuid references episodes(episode_id) on delete set null,
    first_learned_at timestamptz not null default now(),
    last_reinforced_at timestamptz not null default now(),
    reinforcement_count integer not null default 1 check (reinforcement_count >= 1),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_diana_knowledge_status_reinforced
    on diana_knowledge (status, last_reinforced_at desc);
create index if not exists idx_diana_knowledge_confidence
    on diana_knowledge (confidence desc, last_reinforced_at desc);
