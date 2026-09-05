alter table relationship
    add column if not exists conflict double precision not null default 0
        check (conflict between 0 and 1);

create table if not exists relationship_log (
    relationship_log_id uuid primary key default gen_random_uuid(),
    source_experience_id uuid not null unique references experiences(experience_id) on delete cascade,
    previous_state jsonb not null,
    delta jsonb not null,
    new_state jsonb not null,
    reason text not null,
    created_at timestamptz not null default now()
);

create index if not exists idx_relationship_log_created
    on relationship_log (created_at desc);
