-- Supports chronological hard-filtered Episode retrieval by time range.
create index if not exists idx_episodes_created_at
    on episodes (created_at asc);
