-- 20260331_internship_alerts.sql
-- Add Supabase migration for internship alerts tables and matching RPC

-- =============================
-- Extensions
-- =============================
create extension if not exists pgcrypto;
-- =============================
-- Helper trigger for updated_at
-- =============================
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;
-- =============================
-- Profiles enhancements
-- =============================
alter table public.profiles
  add column if not exists target_roles text[] not null default '{}',
  add column if not exists preferred_locations text[] not null default '{}',
  add column if not exists remote_only boolean not null default false,
  add column if not exists alert_frequency text not null default 'daily';
do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'profiles_alert_frequency_check'
  ) then
    alter table public.profiles
      add constraint profiles_alert_frequency_check
      check (alert_frequency in ('daily', 'instant'));
  end if;
end$$;
-- =============================
-- Internship listings
-- =============================
create table if not exists public.internship_listings (
  id uuid primary key default gen_random_uuid(),
  external_id text,
  source text not null default 'unknown',
  source_url text,
  canonical_url text not null,
  title text not null,
  company text,
  location text,
  is_remote boolean not null default false,
  employment_type text, -- internship/full-time/etc.
  role_category text,
  role_tags text[] not null default '{}',
  summary text,
  description text,
  posted_at timestamptz,
  match_score integer not null default 0,
  is_saved boolean not null default false,
  is_applied boolean not null default false,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create unique index if not exists internship_listings_canonical_url_key
  on public.internship_listings (canonical_url);
create index if not exists internship_listings_posted_at_idx
  on public.internship_listings (posted_at desc);
create index if not exists internship_listings_source_idx
  on public.internship_listings (source);
create index if not exists internship_listings_role_category_idx
  on public.internship_listings (role_category);
create index if not exists internship_listings_title_trgm_idx
  on public.internship_listings using gin (to_tsvector('english', coalesce(title, '') || ' ' || coalesce(company, '') || ' ' || coalesce(summary, '')));
create trigger trg_internship_listings_updated_at
before update on public.internship_listings
for each row
execute procedure public.set_updated_at();
-- =============================
-- Optional dedicated preferences table
-- (kept for extensibility and alert enable/disable)
-- =============================
create table if not exists public.internship_alert_preferences (
  user_id uuid primary key references auth.users(id) on delete cascade,
  roles text[] not null default '{}',
  preferred_locations text[] not null default '{}',
  remote_only boolean not null default false,
  alert_frequency text not null default 'daily',
  internships_only boolean not null default false,
  enabled boolean not null default true,
  last_crawled_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'internship_alert_preferences_alert_frequency_check'
  ) then
    alter table public.internship_alert_preferences
      add constraint internship_alert_preferences_alert_frequency_check
      check (alert_frequency in ('daily', 'instant'));
  end if;
end$$;
create trigger trg_internship_alert_preferences_updated_at
before update on public.internship_alert_preferences
for each row
execute procedure public.set_updated_at();
-- =============================
-- Per-user listing state
-- =============================
create table if not exists public.user_internship_listing_state (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  listing_id uuid not null references public.internship_listings(id) on delete cascade,
  is_saved boolean not null default false,
  is_applied boolean not null default false,
  viewed_at timestamptz,
  applied_at timestamptz,
  dismissed_at timestamptz,
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, listing_id)
);
create index if not exists user_internship_listing_state_user_idx
  on public.user_internship_listing_state (user_id);
create index if not exists user_internship_listing_state_listing_idx
  on public.user_internship_listing_state (listing_id);
create trigger trg_user_internship_listing_state_updated_at
before update on public.user_internship_listing_state
for each row
execute procedure public.set_updated_at();
-- =============================
-- Alert audit / dedupe table
-- =============================
create table if not exists public.internship_alerts_sent (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  listing_id uuid not null references public.internship_listings(id) on delete cascade,
  channel text not null default 'in_app', -- in_app | email | push
  sent_at timestamptz not null default now(),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index if not exists internship_alerts_sent_user_idx
  on public.internship_alerts_sent (user_id, sent_at desc);
create unique index if not exists internship_alerts_sent_dedupe_idx
  on public.internship_alerts_sent (user_id, listing_id, channel);
-- =============================
-- RLS
-- =============================
alter table public.internship_listings enable row level security;
alter table public.internship_alert_preferences enable row level security;
alter table public.user_internship_listing_state enable row level security;
alter table public.internship_alerts_sent enable row level security;
-- internship_listings: authenticated users can read
drop policy if exists "internship_listings_select_authenticated" on public.internship_listings;
create policy "internship_listings_select_authenticated"
on public.internship_listings
for select
to authenticated
using (true);
-- internship_listings: restrict write to service role (no policy for authenticated writes)

-- internship_alert_preferences: users manage own row
drop policy if exists "internship_alert_preferences_select_own" on public.internship_alert_preferences;
create policy "internship_alert_preferences_select_own"
on public.internship_alert_preferences
for select
to authenticated
using (auth.uid() = user_id);
drop policy if exists "internship_alert_preferences_insert_own" on public.internship_alert_preferences;
create policy "internship_alert_preferences_insert_own"
on public.internship_alert_preferences
for insert
to authenticated
with check (auth.uid() = user_id);
drop policy if exists "internship_alert_preferences_update_own" on public.internship_alert_preferences;
create policy "internship_alert_preferences_update_own"
on public.internship_alert_preferences
for update
to authenticated
using (auth.uid() = user_id)
with check (auth.uid() = user_id);
drop policy if exists "internship_alert_preferences_delete_own" on public.internship_alert_preferences;
create policy "internship_alert_preferences_delete_own"
on public.internship_alert_preferences
for delete
to authenticated
using (auth.uid() = user_id);
-- user_internship_listing_state: users manage own state
drop policy if exists "user_internship_listing_state_select_own" on public.user_internship_listing_state;
create policy "user_internship_listing_state_select_own"
on public.user_internship_listing_state
for select
to authenticated
using (auth.uid() = user_id);
drop policy if exists "user_internship_listing_state_insert_own" on public.user_internship_listing_state;
create policy "user_internship_listing_state_insert_own"
on public.user_internship_listing_state
for insert
to authenticated
with check (auth.uid() = user_id);
drop policy if exists "user_internship_listing_state_update_own" on public.user_internship_listing_state;
create policy "user_internship_listing_state_update_own"
on public.user_internship_listing_state
for update
to authenticated
using (auth.uid() = user_id)
with check (auth.uid() = user_id);
drop policy if exists "user_internship_listing_state_delete_own" on public.user_internship_listing_state;
create policy "user_internship_listing_state_delete_own"
on public.user_internship_listing_state
for delete
to authenticated
using (auth.uid() = user_id);
-- internship_alerts_sent: users can read their own audit trail
drop policy if exists "internship_alerts_sent_select_own" on public.internship_alerts_sent;
create policy "internship_alerts_sent_select_own"
on public.internship_alerts_sent
for select
to authenticated
using (auth.uid() = user_id);
-- =============================
-- Matching helpers
-- =============================
create or replace function public.compute_internship_match_score(
  p_title text,
  p_company text,
  p_location text,
  p_description text,
  p_role_tags text[],
  p_roles text[],
  p_preferred_locations text[],
  p_remote_only boolean,
  p_is_remote boolean,
  p_internships_only boolean,
  p_employment_type text
)
returns integer
language plpgsql
as $$
declare
  v_haystack text := lower(
    coalesce(p_title, '') || ' ' ||
    coalesce(p_company, '') || ' ' ||
    coalesce(p_location, '') || ' ' ||
    coalesce(p_description, '') || ' ' ||
    coalesce(array_to_string(p_role_tags, ' '), '')
  );
  v_score integer := 0;
  v_role text;
  v_loc text;
begin
  -- Hard filters
  if p_remote_only and not coalesce(p_is_remote, false) then
    return -1000;
  end if;

  if p_internships_only and position('intern' in lower(coalesce(p_employment_type, ''))) = 0 then
    return -1000;
  end if;

  -- Base keyword boosts
  if position('intern' in v_haystack) > 0 then
    v_score := v_score + 10;
  end if;
  if position('internship' in v_haystack) > 0 then
    v_score := v_score + 8;
  end if;
  if position('new grad' in v_haystack) > 0 then
    v_score := v_score + 4;
  end if;

  -- Role scoring
  if p_roles is not null then
    foreach v_role in array p_roles loop
      if v_role is null then
        continue;
      end if;

      -- exact role string
      if position(lower(v_role) in v_haystack) > 0 then
        v_score := v_score + 16;
      end if;

      -- lightweight taxonomy expansion
      if lower(v_role) in ('software engineering', 'software engineer', 'swe') then
        if v_haystack ~ '(software engineer|swe|developer|engineering intern)' then
          v_score := v_score + 14;
        end if;
      elsif lower(v_role) in ('frontend', 'frontend engineering', 'front-end') then
        if v_haystack ~ '(frontend|front-end|react|ui engineer)' then
          v_score := v_score + 14;
        end if;
      elsif lower(v_role) in ('backend', 'backend engineering', 'back-end') then
        if v_haystack ~ '(backend|back-end|api|distributed systems)' then
          v_score := v_score + 14;
        end if;
      elsif lower(v_role) in ('full stack', 'full-stack', 'full stack engineering') then
        if v_haystack ~ '(full stack|full-stack|frontend|backend)' then
          v_score := v_score + 14;
        end if;
      elsif lower(v_role) in ('data science') then
        if v_haystack ~ '(data science|data scientist|analytics|statistics)' then
          v_score := v_score + 14;
        end if;
      elsif lower(v_role) in ('machine learning', 'ml') then
        if v_haystack ~ '(machine learning|ml|deep learning|model)' then
          v_score := v_score + 14;
        end if;
      elsif lower(v_role) in ('devops', 'sre', 'devops/sre') then
        if v_haystack ~ '(devops|sre|site reliability|kubernetes|terraform)' then
          v_score := v_score + 14;
        end if;
      elsif lower(v_role) in ('cloud', 'cloud engineering') then
        if v_haystack ~ '(cloud|aws|gcp|azure|platform engineer)' then
          v_score := v_score + 14;
        end if;
      elsif lower(v_role) in ('cybersecurity', 'security') then
        if v_haystack ~ '(security|cyber|infosec|application security)' then
          v_score := v_score + 14;
        end if;
      end if;
    end loop;
  end if;

  -- Preferred location boosts
  if p_preferred_locations is not null and array_length(p_preferred_locations, 1) > 0 then
    foreach v_loc in array p_preferred_locations loop
      if v_loc is not null and position(lower(v_loc) in v_haystack) > 0 then
        v_score := v_score + 10;
      end if;
    end loop;
  end if;

  -- Remote bonus if preferred and present
  if coalesce(p_is_remote, false) then
    v_score := v_score + 2;
  end if;

  return greatest(v_score, 0);
end;
$$;
-- =============================
-- Matching RPC
-- =============================
create or replace function public.match_internship_listings_for_user(
  p_user_id uuid,
  p_limit integer default 40
)
returns table (
  id uuid,
  external_id text,
  source text,
  source_url text,
  canonical_url text,
  title text,
  company text,
  location text,
  is_remote boolean,
  employment_type text,
  role_category text,
  role_tags text[],
  summary text,
  description text,
  posted_at timestamptz,
  metadata jsonb,
  is_saved boolean,
  is_applied boolean,
  match_score integer
)
language sql
security definer
set search_path = public
as $$
  with prefs as (
    select
      iap.user_id,
      iap.roles,
      iap.preferred_locations,
      iap.remote_only,
      iap.internships_only,
      iap.enabled
    from public.internship_alert_preferences iap
    where iap.user_id = p_user_id
    union all
    select
      p.id as user_id,
      p.target_roles as roles,
      p.preferred_locations,
      p.remote_only,
      (false)::boolean as internships_only,
      (true)::boolean as enabled
    from public.profiles p
    where p.id = p_user_id
      and not exists (
        select 1
        from public.internship_alert_preferences iap2
        where iap2.user_id = p_user_id
      )
    limit 1
  ),
  scored as (
    select
      l.*,
      public.compute_internship_match_score(
        l.title,
        l.company,
        l.location,
        coalesce(l.summary, l.description),
        l.role_tags,
        coalesce(pr.roles, '{}'),
        coalesce(pr.preferred_locations, '{}'),
        coalesce(pr.remote_only, false),
        coalesce(l.is_remote, false),
        coalesce(pr.internships_only, false),
        coalesce(l.employment_type, '')
      ) as computed_score,
      uls.is_saved as user_saved,
      uls.is_applied as user_applied
    from public.internship_listings l
    join prefs pr on pr.enabled = true
    left join public.user_internship_listing_state uls
      on uls.user_id = p_user_id
     and uls.listing_id = l.id
  )
  select
    s.id,
    s.external_id,
    s.source,
    s.source_url,
    s.canonical_url,
    s.title,
    s.company,
    s.location,
    s.is_remote,
    s.employment_type,
    s.role_category,
    s.role_tags,
    s.summary,
    s.description,
    s.posted_at,
    s.metadata,
    coalesce(s.user_saved, s.is_saved, false) as is_saved,
    coalesce(s.user_applied, s.is_applied, false) as is_applied,
    s.computed_score as match_score
  from scored s
  where s.computed_score > 0
  order by
    s.computed_score desc,
    s.posted_at desc nulls last,
    s.created_at desc
  limit greatest(coalesce(p_limit, 40), 1);
$$;
revoke all on function public.match_internship_listings_for_user(uuid, integer) from public;
grant execute on function public.match_internship_listings_for_user(uuid, integer) to authenticated, service_role;
-- =============================
-- Helpful view (optional)
-- =============================
create or replace view public.internship_listing_public as
select
  l.id,
  l.source,
  l.canonical_url,
  l.title,
  l.company,
  l.location,
  l.is_remote,
  l.employment_type,
  l.role_category,
  l.role_tags,
  l.summary,
  l.posted_at,
  l.metadata
from public.internship_listings l;
grant select on public.internship_listing_public to authenticated, anon;
