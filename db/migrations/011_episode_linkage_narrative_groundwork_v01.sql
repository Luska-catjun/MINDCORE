-- Additive linkage for one completed user/Diana turn. Existing episode rows stay valid.
alter table episodes
    add column if not exists user_message_id uuid references messages(id) on delete set null,
    add column if not exists assistant_message_id uuid references messages(id) on delete set null,
    add column if not exists experience_id uuid references experiences(experience_id) on delete set null,
    add column if not exists started_at timestamptz,
    add column if not exists ended_at timestamptz,
    add column if not exists episode_type text not null default 'conversation',
    add column if not exists topic_key text,
    add column if not exists provenance text not null default 'grounded_event',
    add column if not exists is_grounded boolean not null default true,
    add column if not exists updated_at timestamptz not null default now();

alter table emotion_attributions
    add column if not exists episode_id uuid references episodes(episode_id) on delete set null;
alter table relationship_log
    add column if not exists episode_id uuid references episodes(episode_id) on delete set null;
alter table diana_preference_evidence
    add column if not exists episode_id uuid references episodes(episode_id) on delete set null;
alter table memories
    add column if not exists source_episode_id uuid references episodes(episode_id) on delete set null;

create unique index if not exists uq_episodes_user_message
    on episodes (user_message_id) where user_message_id is not null;
create index if not exists idx_episodes_linkage_conversation_started
    on episodes (conversation_id, started_at desc);
create index if not exists idx_episodes_topic_key on episodes (topic_key) where topic_key is not null;
create index if not exists idx_episodes_type on episodes (episode_type, created_at desc);
create index if not exists idx_emotion_attributions_episode on emotion_attributions (episode_id);
