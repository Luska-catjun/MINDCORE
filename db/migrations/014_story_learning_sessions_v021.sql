-- Story breadth (fact count) and repeated learning are separate concepts.
alter table diana_knowledge
    add column if not exists learning_session_count integer not null default 1
        check (learning_session_count >= 1),
    add column if not exists last_learning_session_id uuid;

-- Existing rows predate session tracking.  They are conservatively treated as
-- having been learned in one session instead of inferring familiarity from
-- their number of stored facts.
update diana_knowledge
set learning_session_count = 1
where learning_session_count is null or learning_session_count < 1;
