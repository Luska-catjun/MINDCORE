-- Keep v0.2 scalar fields as the compatibility primary emotion while storing
-- the independent v0.3 channels in one atomic singleton JSONB value.
alter table diana_state
    add column if not exists emotion_vector jsonb not null default '{}'::jsonb;

alter table state_log
    add column if not exists emotion_vector jsonb not null default '{}'::jsonb;

update diana_state
set emotion_vector = jsonb_build_object(emotion, emotion_intensity)
where emotion_vector = '{}'::jsonb
  and emotion is not null
  and emotion <> 'neutral'
  and coalesce(emotion_intensity, 0) > 0;

update state_log
set emotion_vector = jsonb_build_object(emotion, emotion_intensity)
where emotion_vector = '{}'::jsonb
  and emotion is not null
  and emotion <> 'neutral'
  and coalesce(emotion_intensity, 0) > 0;

alter table emotion_attributions
    drop constraint if exists emotion_attributions_emotion_check;

alter table emotion_attributions
    add constraint emotion_attributions_emotion_check check (emotion in (
        'neutral', 'joy', 'excitement', 'interest', 'curiosity', 'delight',
        'amusement', 'comfort', 'affection', 'pride', 'bashfulness',
        'embarrassment', 'surprise', 'confusion', 'sadness', 'disappointment',
        'frustration', 'concern', 'anger'
    ));

alter table emotion_attributions
    drop constraint if exists emotion_attributions_cause_type_check;

alter table emotion_attributions
    add constraint emotion_attributions_cause_type_check check (cause_type in (
        'praise', 'positive_user_event', 'user_difficulty',
        'interpersonal_negative', 'inquiry', 'time_decay', 'unknown',
        'interesting_subject', 'humor', 'warm_interaction',
        'unexpected_information', 'disappointment', 'frustration',
        'reassurance', 'success', 'conflict'
    ));
