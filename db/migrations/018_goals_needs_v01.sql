create table if not exists diana_needs (
  need_key text primary key check(need_key in ('curiosity','understanding','social_connection','activity','helpfulness','autonomy')),
  value real not null check(value between 0 and 1), baseline real not null check(baseline between 0 and 1),
  updated_at text not null, last_triggered_at text, metadata text not null default '{}'
);
create table if not exists diana_need_events (
  id text primary key, need_key text not null references diana_needs(need_key) on delete cascade,
  delta real not null, before_value real not null, after_value real not null, reason text not null,
  source_type text not null, source_id text, conversation_id text references conversations(conversation_id) on delete set null,
  fingerprint text not null unique, created_at text not null
);
create table if not exists diana_goals (
  id text primary key, goal_key text not null unique, goal_type text not null, summary text not null,
  origin_need text not null references diana_needs(need_key), priority real not null check(priority between 0 and 1),
  status text not null check(status in ('candidate','active','satisfied','abandoned','expired')), progress real not null check(progress between 0 and 1), confidence real not null check(confidence between 0 and 1),
  conversation_id text references conversations(conversation_id) on delete cascade, source_type text not null, source_id text,
  created_at text not null, updated_at text not null, expires_at text, metadata text not null default '{}'
);
create index if not exists idx_goals_active on diana_goals(status,priority desc);
