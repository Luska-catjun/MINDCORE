-- Fact-level, user-taught fictional story knowledge.  The parent row names
-- the work; each child row is one explicitly supplied detail.
alter table diana_knowledge
    drop constraint if exists diana_knowledge_source_type_check;

alter table diana_knowledge
    add constraint diana_knowledge_source_type_check check (source_type in (
        'user_message', 'user_story', 'trusted_system', 'approved_external'
    ));

create table if not exists diana_knowledge_facts (
    knowledge_fact_id uuid primary key default gen_random_uuid(),
    knowledge_id uuid not null references diana_knowledge(knowledge_id) on delete cascade,
    fact_key text not null,
    fact_text text not null,
    knowledge_scope text not null default 'fictional_story'
        check (knowledge_scope in ('fictional_story')),
    source_type text not null default 'user_story'
        check (source_type in ('user_story')),
    source_message_id uuid not null references messages(id) on delete cascade,
    source_episode_id uuid references episodes(episode_id) on delete set null,
    confidence double precision not null default 0.45 check (confidence between 0 and 1),
    reinforcement_count integer not null default 1 check (reinforcement_count >= 1),
    contradiction_count integer not null default 0 check (contradiction_count >= 0),
    first_learned_at timestamptz not null default now(),
    last_reinforced_at timestamptz not null default now(),
    last_contradicted_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (knowledge_id, fact_key)
);

create index if not exists idx_diana_knowledge_facts_knowledge_recent
    on diana_knowledge_facts (knowledge_id, last_reinforced_at desc);
