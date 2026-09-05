create table if not exists preferences (
 preference_id uuid primary key default gen_random_uuid(), owner_type text not null check(owner_type in ('user','diana')),
 subject text not null, value text not null, preference_type text not null check(preference_type in ('like','dislike')),
 status text not null default 'candidate' check(status in ('candidate','stable','contradicted','superseded')),
 confidence double precision not null default 0 check(confidence between 0 and 1), evidence_count integer not null default 0 check(evidence_count>=0),
 first_seen_at timestamptz not null default now(), last_seen_at timestamptz not null default now(), created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
 unique(owner_type,subject,value,preference_type)
);
create table if not exists preference_evidence (
 evidence_id uuid primary key default gen_random_uuid(), preference_id uuid not null references preferences(preference_id) on delete cascade,
 experience_id uuid not null references experiences(experience_id) on delete cascade, message_id uuid not null references messages(id) on delete cascade,
 evidence_type text not null check(evidence_type in ('explicit_positive','explicit_negative','contradiction')),
 direction integer not null check(direction in (-1,1)), strength double precision not null check(strength between 0 and 1), created_at timestamptz not null default now(),
 unique(experience_id,preference_id)
);
