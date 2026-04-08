-- Behavior settings: per-user configuration for chatbot behavior.
-- Starts with response_style; more aspects will be added later.

create table if not exists behavior_settings (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,

  -- Response style
  response_tone text not null default 'friendly'
    check (response_tone in ('professional', 'friendly', 'casual', 'academic')),
  response_length text not null default 'balanced'
    check (response_length in ('concise', 'balanced', 'detailed')),
  response_format text not null default 'markdown'
    check (response_format in ('plain', 'markdown', 'bullet-heavy')),
  emoji_usage text not null default 'occasional'
    check (emoji_usage in ('none', 'occasional', 'frequent')),

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  constraint uq_behavior_settings_user unique (user_id)
);

create index if not exists idx_behavior_settings_user
on behavior_settings(user_id);

-- RLS: users can only access their own behavior settings
alter table behavior_settings enable row level security;

create policy "Users can read own behavior settings"
  on behavior_settings for select
  using (auth.uid() = user_id);

create policy "Users can insert own behavior settings"
  on behavior_settings for insert
  with check (auth.uid() = user_id);

create policy "Users can update own behavior settings"
  on behavior_settings for update
  using (auth.uid() = user_id);

-- Auto-create default behavior settings when a new profile is created
create or replace function create_default_behavior_settings()
returns trigger as $$
begin
  insert into behavior_settings (user_id)
  values (new.id)
  on conflict (user_id) do nothing;
  return new;
end;
$$ language plpgsql security definer;

create trigger trg_create_default_behavior_settings
  after insert on profiles
  for each row
  execute function create_default_behavior_settings();
