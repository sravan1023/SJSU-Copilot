-- Add priority_stack to behavior_settings.
-- Stored as a JSONB array of priority keys, ordered by rank (index 0 = highest).

alter table behavior_settings
add column if not exists priority_stack jsonb
  not null default '["safety","accuracy","task_completion","clarity","speed","warmth"]'::jsonb;
