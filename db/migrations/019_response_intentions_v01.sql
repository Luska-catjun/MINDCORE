create table if not exists diana_response_intentions (
 id text primary key, conversation_id text not null references conversations(conversation_id) on delete cascade,
 user_message_id text references messages(id) on delete set null, assistant_message_id text references messages(id) on delete set null,
 action text not null check(action in ('answer','fulfill_request','clarify','choose','acknowledge','ask_followup')),
 target text, reason_code text not null, confidence real not null check(confidence between 0 and 1),
 source_refs text not null default '{}', constraints text not null default '[]', created_at text not null
);
create index if not exists idx_response_intentions_conversation on diana_response_intentions(conversation_id,created_at desc);
