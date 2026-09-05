create extension if not exists pgcrypto;
create extension if not exists vector;

create or replace function set_updated_at()
returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;

create table if not exists diana_identity (
    id uuid primary key default gen_random_uuid(),
    singleton_key text not null default 'diana' unique,
    display_name text not null default 'Diana',
    description text,
    traits jsonb not null default '{}'::jsonb,
    system_notes jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists conversations (
    id uuid primary key default gen_random_uuid(),
    title text,
    source_device text,
    status text not null default 'active' check (status in ('active', 'archived')),
    metadata jsonb not null default '{}'::jsonb,
    started_at timestamptz not null default now(),
    last_message_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists messages (
    id uuid primary key default gen_random_uuid(),
    conversation_id uuid not null references conversations(id) on delete restrict,
    role text not null check (role in ('user', 'diana', 'system', 'tool')),
    source_device text,
    sequence integer not null check (sequence > 0),
    content text not null,
    metadata jsonb not null default '{}'::jsonb,
    timestamp timestamptz not null default now(),
    created_at timestamptz not null default now(),
    unique (conversation_id, sequence)
);

create table if not exists episodes (
    id uuid primary key default gen_random_uuid(),
    conversation_id uuid references conversations(id) on delete set null,
    message_id uuid references messages(id) on delete set null,
    title text,
    content text not null,
    source_device text,
    sequence integer check (sequence is null or sequence > 0),
    embedding vector(1536),
    importance numeric(5,4) not null default 0 check (importance between 0 and 1),
    emotional_impact numeric(5,4) not null default 0 check (emotional_impact between 0 and 1),
    valence numeric(5,4) not null default 0 check (valence between 0 and 1),
    novelty numeric(5,4) not null default 0 check (novelty between 0 and 1),
    confidence numeric(5,4) not null default 0 check (confidence between 0 and 1),
    relationship_impact numeric(5,4) not null default 0 check (relationship_impact between 0 and 1),
    personal_relevance numeric(5,4) not null default 0 check (personal_relevance between 0 and 1),
    recall_frequency numeric(5,4) not null default 0 check (recall_frequency between 0 and 1),
    context_relevance numeric(5,4) not null default 0 check (context_relevance between 0 and 1),
    memory_strength numeric(5,4) not null default 0 check (memory_strength between 0 and 1),
    decay numeric(5,4) not null default 0 check (decay between 0 and 1),
    metadata jsonb not null default '{}'::jsonb,
    timestamp timestamptz not null default now(),
    created_at timestamptz not null default now()
);

create table if not exists semantic_facts (
    id uuid primary key default gen_random_uuid(),
    fact text not null,
    source_episode_id uuid references episodes(id) on delete set null,
    confidence numeric(5,4) not null default 0 check (confidence between 0 and 1),
    embedding vector(1536),
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists state (
    id uuid primary key default gen_random_uuid(),
    singleton_key text not null default 'current' unique,
    mood text,
    energy numeric(5,4) check (energy is null or energy between 0 and 1),
    focus text,
    state_data jsonb not null default '{}'::jsonb,
    source_device text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists state_log (
    id uuid primary key default gen_random_uuid(),
    state_id uuid references state(id) on delete set null,
    previous_state jsonb,
    new_state jsonb not null,
    source_device text,
    created_at timestamptz not null default now()
);

create table if not exists relationship (
    id uuid primary key default gen_random_uuid(),
    user_label text not null default 'primary_user' unique,
    closeness numeric(5,4) not null default 0 check (closeness between 0 and 1),
    trust numeric(5,4) not null default 0 check (trust between 0 and 1),
    familiarity numeric(5,4) not null default 0 check (familiarity between 0 and 1),
    notes text,
    metadata jsonb not null default '{}'::jsonb,
    source_device text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists decision_log (
    id uuid primary key default gen_random_uuid(),
    conversation_id uuid references conversations(id) on delete set null,
    episode_id uuid references episodes(id) on delete set null,
    decision_type text,
    input_summary text,
    decision text not null,
    rationale text,
    confidence numeric(5,4) not null default 0 check (confidence between 0 and 1),
    status text not null default 'active' check (status in ('active','executed','superseded','cancelled','expired')),
    decision_domain text,
    updated_at timestamptz not null default now(),
    resolved_at timestamptz,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists idx_conversations_started_at on conversations (started_at desc);
create index if not exists idx_messages_conversation_sequence on messages (conversation_id, sequence);
create index if not exists idx_episodes_timestamp on episodes (timestamp desc);
create index if not exists idx_episodes_conversation on episodes (conversation_id);
create index if not exists idx_semantic_facts_source_episode on semantic_facts (source_episode_id);
create index if not exists idx_decision_log_created_at on decision_log (created_at desc);
create index if not exists idx_decision_log_lifecycle on decision_log (conversation_id, decision_domain, status, updated_at desc);

create index if not exists idx_episodes_embedding
on episodes using ivfflat (embedding vector_cosine_ops)
where embedding is not null;

create index if not exists idx_semantic_facts_embedding
on semantic_facts using ivfflat (embedding vector_cosine_ops)
where embedding is not null;

drop trigger if exists trg_diana_identity_updated_at on diana_identity;
create trigger trg_diana_identity_updated_at
before update on diana_identity
for each row execute function set_updated_at();

drop trigger if exists trg_conversations_updated_at on conversations;
create trigger trg_conversations_updated_at
before update on conversations
for each row execute function set_updated_at();

drop trigger if exists trg_semantic_facts_updated_at on semantic_facts;
create trigger trg_semantic_facts_updated_at
before update on semantic_facts
for each row execute function set_updated_at();

drop trigger if exists trg_state_updated_at on state;
create trigger trg_state_updated_at
before update on state
for each row execute function set_updated_at();

drop trigger if exists trg_relationship_updated_at on relationship;
create trigger trg_relationship_updated_at
before update on relationship
for each row execute function set_updated_at();

create or replace function update_conversation_last_message_at()
returns trigger as $$
begin
    update conversations
    set last_message_at = new.timestamp,
        updated_at = now()
    where id = new.conversation_id;
    return new;
end;
$$ language plpgsql;

drop trigger if exists trg_messages_update_conversation on messages;
create trigger trg_messages_update_conversation
after insert on messages
for each row execute function update_conversation_last_message_at();

create or replace function write_state_log()
returns trigger as $$
begin
    insert into state_log (state_id, previous_state, new_state, source_device)
    values (
        new.id,
        case when tg_op = 'UPDATE' then to_jsonb(old) else null end,
        to_jsonb(new),
        new.source_device
    );
    return new;
end;
$$ language plpgsql;

drop trigger if exists trg_state_write_log on state;
create trigger trg_state_write_log
after insert or update on state
for each row execute function write_state_log();
