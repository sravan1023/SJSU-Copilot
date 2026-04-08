-- Hierarchical behavior scoping.
-- Allows behavior settings to be overridden at project or conversation scope.
-- Scope chain: conversation > project > user (global)

-- Add nullable scope columns
alter table behavior_settings
  add column if not exists project_id uuid references projects(id) on delete cascade default null,
  add column if not exists conversation_id uuid references conversations(id) on delete cascade default null;

-- Drop the old user-only unique constraint and replace with a scope-aware one.
-- COALESCE is used so that NULLs in project_id / conversation_id don't defeat uniqueness.
alter table behavior_settings
  drop constraint if exists uq_behavior_settings_user;

alter table behavior_settings
  add constraint uq_behavior_settings_scope
  unique (user_id, project_id, conversation_id);

-- Index for fast scope resolution lookups
create index if not exists idx_behavior_settings_scope
  on behavior_settings(user_id, project_id, conversation_id);

-- Make style/priority columns nullable so scoped rows can omit fields
-- (missing fields fall through to the parent scope during resolution)
alter table behavior_settings
  alter column response_tone     drop not null,
  alter column response_length   drop not null,
  alter column response_format   drop not null,
  alter column emoji_usage       drop not null,
  alter column priority_stack    drop not null;

-- Keep defaults for the global (user-level) rows only; scoped rows default to null
-- (no change needed — defaults still apply when inserting without explicit values)

-- RLS: existing policies already guard by user_id, which covers all scopes
