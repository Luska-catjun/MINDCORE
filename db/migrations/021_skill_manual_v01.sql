-- SQLite cannot alter a CHECK constraint in place. Preserve every existing row
-- while extending Working Memory with a durable activity-scoped skill slot.
create table diana_working_memory_items__skill_manual (
 id text primary key, conversation_id text not null references conversations(conversation_id) on delete cascade,
 slot_type text not null check(slot_type in ('active_topic','open_loop','active_memory_ref','active_skill')), item_key text not null, summary text not null,
 source_type text not null, source_id text, salience real not null check(salience between 0 and 1), status text not null check(status in ('active','resolved','expired')),
 created_at text not null, last_touched_at text not null, expires_at text not null, metadata text not null default '{}', unique(conversation_id,slot_type,item_key)
);
insert into diana_working_memory_items__skill_manual select * from diana_working_memory_items;
drop table diana_working_memory_items;
alter table diana_working_memory_items__skill_manual rename to diana_working_memory_items;
create index if not exists idx_wm_conversation_active on diana_working_memory_items(conversation_id,status,salience desc);
