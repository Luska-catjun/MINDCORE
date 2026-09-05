-- Applied conditionally by scripts/apply_turso_decision_lifecycle_migration.py.
-- SQLite/libSQL cannot add the same column twice idempotently, so the script
-- inspects PRAGMA table_info before executing each statement.
alter table decision_log add column conversation_id text;
alter table decision_log add column decision_domain text;
alter table decision_log add column status text not null default 'active'
    check(status in ('active','executed','superseded','cancelled','expired'));
alter table decision_log add column updated_at text;
alter table decision_log add column resolved_at text;
update decision_log set updated_at=created_at where updated_at is null;
create index if not exists idx_decision_log_lifecycle
    on decision_log(conversation_id, decision_domain, status, updated_at desc);
