-- 20260401_intern_jobs_pipeline.sql
-- Intern jobs alert pipeline tables/state/runs for Airtable -> Resend flow.

create extension if not exists pgcrypto;

create table if not exists public.pipeline_runs (
  id uuid primary key default gen_random_uuid(),
  pipeline_key text not null default 'intern_jobs_alert',
  status text not null default 'running' check (status in ('running', 'success', 'failed')),
  top_count integer not null default 0 check (top_count >= 0),
  new_jobs_count integer not null default 0 check (new_jobs_count >= 0),
  email_sent boolean not null default false,
  error_message text,
  started_at timestamptz not null default now(),
  finished_at timestamptz
);

create index if not exists idx_pipeline_runs_pipeline_started
  on public.pipeline_runs (pipeline_key, started_at desc);

create index if not exists idx_pipeline_runs_status_started
  on public.pipeline_runs (status, started_at desc);

create table if not exists public.pipeline_state (
  pipeline_key text primary key,
  previous_top_url text,
  last_success_at timestamptz,
  last_run_id uuid references public.pipeline_runs(id) on delete set null,
  updated_at timestamptz not null default now()
);

create table if not exists public.jobs_snapshot (
  id bigint generated always as identity primary key,
  run_id uuid not null references public.pipeline_runs(id) on delete cascade,
  rank_position integer not null check (rank_position >= 1 and rank_position <= 100),
  job_url text not null,
  company text not null,
  title text not null,
  source_record_id text,
  raw_record jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique (run_id, rank_position),
  unique (run_id, job_url)
);

create index if not exists idx_jobs_snapshot_run_rank
  on public.jobs_snapshot (run_id, rank_position);

create index if not exists idx_jobs_snapshot_job_url
  on public.jobs_snapshot (job_url);

create or replace function public.pipeline_state_set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists trg_pipeline_state_set_updated_at on public.pipeline_state;
create trigger trg_pipeline_state_set_updated_at
before update on public.pipeline_state
for each row
execute procedure public.pipeline_state_set_updated_at();

create or replace function public.update_pipeline_marker_if_expected(
  p_pipeline_key text,
  p_expected_previous_top_url text,
  p_new_previous_top_url text,
  p_last_run_id uuid
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_current_previous_top_url text;
begin
  insert into public.pipeline_state (pipeline_key, previous_top_url)
  values (p_pipeline_key, null)
  on conflict (pipeline_key) do nothing;

  select previous_top_url
    into v_current_previous_top_url
    from public.pipeline_state
   where pipeline_key = p_pipeline_key
   for update;

  if v_current_previous_top_url is distinct from p_expected_previous_top_url then
    return false;
  end if;

  update public.pipeline_state
     set previous_top_url = p_new_previous_top_url,
         last_success_at = now(),
         last_run_id = p_last_run_id,
         updated_at = now()
   where pipeline_key = p_pipeline_key;

  return true;
end;
$$;

alter table public.pipeline_runs enable row level security;
alter table public.pipeline_state enable row level security;
alter table public.jobs_snapshot enable row level security;

-- Authenticated users can observe the pipeline.
create policy "pipeline_runs_select_authenticated"
  on public.pipeline_runs
  for select
  to authenticated
  using (true);

create policy "pipeline_state_select_authenticated"
  on public.pipeline_state
  for select
  to authenticated
  using (true);

create policy "jobs_snapshot_select_authenticated"
  on public.jobs_snapshot
  for select
  to authenticated
  using (true);

-- Writes are restricted to the service role (Edge Function).
create policy "pipeline_runs_service_write"
  on public.pipeline_runs
  for all
  to service_role
  using (auth.role() = 'service_role')
  with check (auth.role() = 'service_role');

create policy "pipeline_state_service_write"
  on public.pipeline_state
  for all
  to service_role
  using (auth.role() = 'service_role')
  with check (auth.role() = 'service_role');

create policy "jobs_snapshot_service_write"
  on public.jobs_snapshot
  for all
  to service_role
  using (auth.role() = 'service_role')
  with check (auth.role() = 'service_role');
