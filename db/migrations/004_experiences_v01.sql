-- An immutable interaction-evidence layer. It intentionally stores message
-- references rather than duplicating user and assistant text.
create table if not exists experiences (
    experience_id uuid primary key default gen_random_uuid(),
    conversation_id uuid not null references conversations(conversation_id) on delete cascade,
    user_message_id uuid not null references messages(id) on delete cascade,
    assistant_message_id uuid not null references messages(id) on delete cascade,
    current_focus text,
    activated_memory_ids jsonb not null default '[]'::jsonb,
    state_before jsonb,
    state_after jsonb,
    state_changed boolean not null default false,
    outcome_type text not null default 'neutral' check (outcome_type in (
        'neutral', 'information', 'task_progress', 'success', 'failure',
        'support', 'correction', 'planning'
    )),
    created_at timestamptz not null default now(),
    unique (user_message_id, assistant_message_id)
);

create index if not exists idx_experiences_conversation_created
    on experiences (conversation_id, created_at desc);

create index if not exists idx_experiences_created
    on experiences (created_at desc);
