-- Supabase Schema

-- 0. EXTENSIONS

create extension if not exists "pgcrypto";
create extension if not exists "vector";
-- pgvector for RAG



-- 1. PROFILES (linked to auth.users)

create table if not exists public.profiles (
  id            uuid primary key references auth.users(id) on delete cascade,
  email         text not null,
  full_name     text,
  role          text not null default 'student' check (role in ('student', 'advisor', 'admin')),
  university_id text,
  major         text,
  minor         text,
  department    text,
  graduation_year int,
  phone         text,
  gpa           numeric(3,2) check (gpa >= 0 and gpa <= 4),
  class_standing text check (class_standing in ('Freshman','Sophomore','Junior','Senior','Graduate')),
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create or replace function public.handle_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;
create trigger on_profiles_updated
  before update on public.profiles
  for each row execute function public.handle_updated_at();
-- 2. AUTO-CREATE PROFILE ON SIGNUP

create or replace function public.handle_new_user()
returns trigger as $$
begin
  insert into public.profiles (id, email, full_name)
  values (
    new.id,
    new.email,
    coalesce(new.raw_user_meta_data ->> 'full_name', '')
  );
  return new;
end;
$$ language plpgsql security definer;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();
-- 3. DOMAIN RESTRICTION — @sjsu.edu only

create or replace function public.enforce_sjsu_email()
returns trigger as $$
begin
  if new.email is null or not (new.email ilike '%@sjsu.edu') then
    raise exception 'Only @sjsu.edu email addresses are allowed.';
  end if;
  return new;
end;
$$ language plpgsql security definer;
-- Attempt to add the guard against non-SJSU emails at the database level.
do $$
begin
  create trigger enforce_sjsu_domain
    before insert on auth.users
    for each row execute function public.enforce_sjsu_email();
exception when others then
  raise notice 'Could not create trigger on auth.users — enforce domain in Edge Function instead.';
end;
$$;
-- 4. RLS ON PROFILES

alter table public.profiles enable row level security;
-- Users can read their own profile
create policy "Users can view own profile"
  on public.profiles for select
  using (auth.uid() = id);
-- Users can update their own profile
create policy "Users can update own profile"
  on public.profiles for update
  using (auth.uid() = id)
  with check (auth.uid() = id);
-- Insert is allowed so the app can self-create a profile row after Google OAuth
create policy "Users can insert own profile"
  on public.profiles for insert
  with check (auth.uid() = id);
-- 5. STUDENT ACADEMIC RECORDS

create table if not exists public.student_academic_records (
  id                uuid primary key default gen_random_uuid(),
  user_id           uuid not null references public.profiles(id) on delete cascade,
  major             text,
  minor             text,
  completed_credits int default 0,
  gpa               numeric(3,2) check (gpa >= 0 and gpa <= 4),
  class_standing    text check (class_standing in ('Freshman','Sophomore','Junior','Senior','Graduate')),
  catalog_year      int,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);
create trigger on_academic_records_updated
  before update on public.student_academic_records
  for each row execute function public.handle_updated_at();
alter table public.student_academic_records enable row level security;
create policy "Users can view own academic records"
  on public.student_academic_records for select
  using (auth.uid() = user_id);
create policy "Users can update own academic records"
  on public.student_academic_records for update
  using (auth.uid() = user_id);
create policy "Users can insert own academic records"
  on public.student_academic_records for insert
  with check (auth.uid() = user_id);
-- 6. SAVED CONVERSATIONS

create table if not exists public.saved_conversations (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references public.profiles(id) on delete cascade,
  title       text not null default 'New Conversation',
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create trigger on_conversations_updated
  before update on public.saved_conversations
  for each row execute function public.handle_updated_at();
alter table public.saved_conversations enable row level security;
create policy "Users can view own conversations"
  on public.saved_conversations for select
  using (auth.uid() = user_id);
create policy "Users can insert own conversations"
  on public.saved_conversations for insert
  with check (auth.uid() = user_id);
create policy "Users can update own conversations"
  on public.saved_conversations for update
  using (auth.uid() = user_id);
create policy "Users can delete own conversations"
  on public.saved_conversations for delete
  using (auth.uid() = user_id);
-- 7. CHAT MESSAGES

create table if not exists public.chat_messages (
  id                uuid primary key default gen_random_uuid(),
  conversation_id   uuid not null references public.saved_conversations(id) on delete cascade,
  user_id           uuid not null references public.profiles(id) on delete cascade,
  role              text not null check (role in ('user', 'assistant', 'system')),
  content           text not null,
  created_at        timestamptz not null default now()
);
alter table public.chat_messages enable row level security;
create policy "Users can view own messages"
  on public.chat_messages for select
  using (auth.uid() = user_id);
create policy "Users can insert own messages"
  on public.chat_messages for insert
  with check (auth.uid() = user_id);
-- 8. UPLOADED DOCUMENTS (student-owned files)

create table if not exists public.uploaded_documents (
  id            uuid primary key default gen_random_uuid(),
  owner_id      uuid not null references public.profiles(id) on delete cascade,
  file_name     text not null,
  storage_path  text not null,
  mime_type     text,
  size_bytes    bigint,
  created_at    timestamptz not null default now()
);
alter table public.uploaded_documents enable row level security;
create policy "Users can view own uploads"
  on public.uploaded_documents for select
  using (auth.uid() = owner_id);
create policy "Users can insert own uploads"
  on public.uploaded_documents for insert
  with check (auth.uid() = owner_id);
create policy "Users can delete own uploads"
  on public.uploaded_documents for delete
  using (auth.uid() = owner_id);
-- 9. RAG — DOCUMENTS & CHUNKS

-- Public university documents (policies, catalogs, FAQs, etc.)
-- These are NOT student-owned; they're ingested by admins/pipelines.

create table if not exists public.documents (
  id              uuid primary key default gen_random_uuid(),
  source          text,              -- e.g. 'sjsu.edu/catalog'
  title           text not null,
  url             text,
  document_type   text,              -- e.g. 'policy', 'catalog', 'faq'
  metadata        jsonb default '{}',
  created_at      timestamptz not null default now()
);
create table if not exists public.document_chunks (
  id              uuid primary key default gen_random_uuid(),
  document_id     uuid not null references public.documents(id) on delete cascade,
  chunk_index     int not null,
  content         text not null,
  embedding       vector(1536),      -- OpenAI text-embedding-ada-002 dimension
  metadata        jsonb default '{}',
  created_at      timestamptz not null default now()
);
-- Vector similarity index (IVFFlat — good default for most workloads)
create index if not exists idx_document_chunks_embedding
  on public.document_chunks
  using ivfflat (embedding vector_cosine_ops)
  with (lists = 100);
-- RLS: university docs are readable by any authenticated user
alter table public.documents enable row level security;
alter table public.document_chunks enable row level security;
create policy "Authenticated users can read documents"
  on public.documents for select
  using (auth.role() = 'authenticated');
create policy "Authenticated users can read document chunks"
  on public.document_chunks for select
  using (auth.role() = 'authenticated');
-- Only service_role (backend) can insert/update/delete RAG documents.
-- No explicit user-facing insert/update/delete policies.


-- 10. SEMANTIC SEARCH FUNCTION

create or replace function public.match_documents(
  query_embedding vector(1536),
  match_count     int default 5,
  match_threshold float default 0.78
)
returns table (
  id            uuid,
  document_id   uuid,
  content       text,
  metadata      jsonb,
  similarity    float
)
language plpgsql
stable
as $$
begin
  return query
    select
      dc.id,
      dc.document_id,
      dc.content,
      dc.metadata,
      1 - (dc.embedding <=> query_embedding) as similarity
    from public.document_chunks dc
    where 1 - (dc.embedding <=> query_embedding) > match_threshold
    order by dc.embedding <=> query_embedding
    limit match_count;
end;
$$;
