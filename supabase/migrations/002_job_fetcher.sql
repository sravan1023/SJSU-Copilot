-- Job Fetcher feature tables and policies

create table if not exists public.job_sources (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  source_type text not null check (source_type in ('api', 'rss', 'html', 'mock')),
  source_url text,
  config jsonb not null default '{}'::jsonb,
  enabled boolean not null default true,
  fetch_interval_minutes int not null default 360 check (fetch_interval_minutes > 0),
  last_fetched_at timestamptz,
  next_fetch_at timestamptz not null default now(),
  last_error text,
  created_by uuid references public.profiles(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create trigger on_job_sources_updated
  before update on public.job_sources
  for each row execute function public.handle_updated_at();

create or replace function public.generate_job_dedupe_hash(input_title text, input_company text)
returns text
language sql
immutable
as $$
  select md5(lower(trim(coalesce(input_title, ''))) || '|' || lower(trim(coalesce(input_company, ''))));
$$;

create table if not exists public.job_listings (
  id uuid primary key default gen_random_uuid(),
  external_id text,
  title text not null,
  company text not null,
  location text,
  remote_status text not null default 'unknown' check (remote_status in ('remote', 'hybrid', 'onsite', 'unknown')),
  job_type text not null default 'other' check (job_type in ('internship', 'new_grad', 'full_time', 'part_time', 'contract', 'other')),
  salary text,
  experience_level text,
  tags text[] not null default '{}'::text[],
  description text,
  apply_url text not null,
  source_name text,
  source_url text,
  posted_date date,
  fetched_at timestamptz not null default now(),
  dedupe_hash text generated always as (public.generate_job_dedupe_hash(title, company)) stored
);

create unique index if not exists idx_job_listings_apply_url_unique on public.job_listings (apply_url);
create unique index if not exists idx_job_listings_dedupe_hash_unique on public.job_listings (dedupe_hash);
create index if not exists idx_job_listings_posted_date on public.job_listings (posted_date desc nulls last);
create index if not exists idx_job_listings_job_type on public.job_listings (job_type);
create index if not exists idx_job_listings_remote_status on public.job_listings (remote_status);
create index if not exists idx_job_listings_tags_gin on public.job_listings using gin (tags);

create table if not exists public.user_saved_jobs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  job_id uuid not null references public.job_listings(id) on delete cascade,
  notes text,
  created_at timestamptz not null default now(),
  unique (user_id, job_id)
);

create table if not exists public.user_job_applications (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  job_id uuid not null references public.job_listings(id) on delete cascade,
  status text not null default 'saved' check (status in ('saved', 'applied', 'interview', 'offer', 'rejected', 'withdrawn')),
  applied_at date,
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, job_id)
);

create trigger on_user_job_applications_updated
  before update on public.user_job_applications
  for each row execute function public.handle_updated_at();

create table if not exists public.job_fetch_runs (
  id uuid primary key default gen_random_uuid(),
  source_id uuid references public.job_sources(id) on delete cascade,
  status text not null check (status in ('success', 'failed', 'partial')),
  fetched_count int not null default 0,
  inserted_count int not null default 0,
  deduped_count int not null default 0,
  error_message text,
  started_at timestamptz not null default now(),
  completed_at timestamptz
);

create index if not exists idx_job_fetch_runs_started_at on public.job_fetch_runs (started_at desc);

alter table public.job_sources enable row level security;
alter table public.job_listings enable row level security;
alter table public.user_saved_jobs enable row level security;
alter table public.user_job_applications enable row level security;
alter table public.job_fetch_runs enable row level security;

create policy "Authenticated users can read job sources"
  on public.job_sources for select
  using (auth.role() = 'authenticated');

create policy "Authenticated users can manage job sources"
  on public.job_sources for all
  using (auth.role() = 'authenticated')
  with check (auth.role() = 'authenticated');

create policy "Authenticated users can read jobs"
  on public.job_listings for select
  using (auth.role() = 'authenticated');

create policy "Authenticated users can insert jobs"
  on public.job_listings for insert
  with check (auth.role() = 'authenticated');

create policy "Users can read own saved jobs"
  on public.user_saved_jobs for select
  using (auth.uid() = user_id);

create policy "Users can manage own saved jobs"
  on public.user_saved_jobs for all
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

create policy "Users can read own applications"
  on public.user_job_applications for select
  using (auth.uid() = user_id);

create policy "Users can manage own applications"
  on public.user_job_applications for all
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

create policy "Authenticated users can read fetch logs"
  on public.job_fetch_runs for select
  using (auth.role() = 'authenticated');

create policy "Authenticated users can insert fetch logs"
  on public.job_fetch_runs for insert
  with check (auth.role() = 'authenticated');

-- Seed minimal starter sources. HTML source uses mock jobs in config to avoid scraping/CORS constraints in-browser.
insert into public.job_sources (name, source_type, source_url, config, enabled, fetch_interval_minutes)
values
  (
    'RemoteOK RSS',
    'rss',
    'https://remoteok.com/remote-dev-jobs.rss',
    '{"tag_map": ["remote", "engineering"]}'::jsonb,
    false,
    240
  ),
  (
    'SJSU Career Mock Feed',
    'mock',
    null,
    '{
      "mock_jobs": [
        {
          "title": "Software Engineer Intern",
          "company": "Spartan Tech Labs",
          "location": "San Jose, CA",
          "remote_status": "hybrid",
          "job_type": "internship",
          "experience_level": "student",
          "tags": ["react", "node", "sql"],
          "description": "Build student-facing tools and dashboards.",
          "apply_url": "https://example.com/jobs/spartan-tech-intern",
          "posted_date": "2026-03-25"
        },
        {
          "title": "New Grad Backend Engineer",
          "company": "Valley Cloud",
          "location": "Santa Clara, CA",
          "remote_status": "onsite",
          "job_type": "new_grad",
          "experience_level": "entry-level",
          "tags": ["python", "api", "postgres"],
          "description": "Work on APIs and data services.",
          "apply_url": "https://example.com/jobs/valley-cloud-new-grad",
          "posted_date": "2026-03-28"
        }
      ]
    }'::jsonb,
    true,
    720
  )
on conflict do nothing;
