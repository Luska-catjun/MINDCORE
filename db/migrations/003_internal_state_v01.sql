alter table diana_state add column if not exists emotion_intensity double precision not null default 0 check (emotion_intensity between 0 and 1);
alter table state_log add column if not exists emotion_intensity double precision not null default 0 check (emotion_intensity between 0 and 1);
