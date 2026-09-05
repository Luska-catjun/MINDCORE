alter table memories
    add column if not exists recall_frequency integer not null default 0 check (recall_frequency >= 0),
    add column if not exists memory_strength double precision not null default 0 check (memory_strength between 0 and 1),
    add column if not exists last_recalled_at timestamptz;

-- Preserve existing memories by deriving their initial strength from importance.
update memories
set memory_strength = greatest(0.0, least(1.0, importance))
where memory_strength = 0 and importance > 0;

create index if not exists idx_memories_recall_weight
    on memories (memory_strength desc, recall_frequency desc, updated_at desc);
