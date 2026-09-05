-- Persistent long-term memories are intentionally separate from conversations.
-- Deleting a source conversation or message keeps the memory while clearing
-- only the optional provenance reference.
create table if not exists memories (
    memory_id uuid primary key default gen_random_uuid(),
    content text not null,
    normalized_content text not null unique,
    memory_type text not null check (memory_type in (
        'user_fact',
        'shared_event',
        'preference',
        'relationship',
        'diana_learning'
    )),
    importance double precision not null check (importance between 0 and 1),
    source_conversation_id uuid references conversations(conversation_id) on delete set null,
    source_message_id uuid references messages(id) on delete set null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_memories_importance_updated
    on memories (importance desc, updated_at desc);

create index if not exists idx_memories_memory_type
    on memories (memory_type);
