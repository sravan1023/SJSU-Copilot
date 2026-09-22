-- Bring the migration history in line with the live database.
--
-- The live project (bsiubrodeyuzbmvmydfe) was built by hand in the SQL editor
-- and was never tracked by the CLI: supabase_migrations.schema_migrations was
-- empty on 2026-09-21. supabase/live_drift_report.md lists how live differed
-- from migrations 001..20260410 replayed on a fresh database. This migration
-- closes the gap in both directions, so a fresh replay up to here produces
-- exactly the live schema (supabase/live_schema_snapshot.sql):
--
--   1. Objects added to live by hand, never in a migration: captured here,
--      copied from the live dump.
--   2. Objects the repo creates that live never had: dropped here.
--      - chat_messages (001): superseded by conversations/messages (20260405).
--      - The 20260331_internship_alerts model: never applied to this project,
--        used by no code, and superseded by the intern jobs pipeline (20260401),
--        which is live. Retired on 2026-09-21.
--
-- On live this is recorded with `supabase migration repair --status applied`
-- rather than run, since live already looks like the result. Every statement is
-- idempotent regardless.

-- 1. Captured from live --------------------------------------------------------

alter table public.documents
  add column if not exists last_verified_at timestamptz,
  add column if not exists content_hash text;

create index if not exists idx_documents_last_verified_at
  on public.documents using btree (last_verified_at desc);

alter table public.messages
  add column if not exists citations jsonb not null default '[]'::jsonb,
  add column if not exists retrieval_meta jsonb not null default '{}'::jsonb;

-- Live's match_documents also returns each chunk's document fields. The return
-- type changed, which CREATE OR REPLACE cannot do, so drop it first.
drop function if exists public.match_documents(vector, integer, double precision);
create function public.match_documents(
  query_embedding vector,
  match_count integer default 5,
  match_threshold double precision default 0.78
)
returns table (
  id uuid,
  document_id uuid,
  chunk_index integer,
  content text,
  metadata jsonb,
  title text,
  url text,
  source text,
  document_type text,
  similarity double precision,
  last_verified_at timestamptz
)
language plpgsql stable
as $$
begin
  return query
    select
      dc.id,
      dc.document_id,
      dc.chunk_index,
      dc.content,
      dc.metadata,
      d.title,
      d.url,
      d.source,
      d.document_type,
      1 - (dc.embedding <=> query_embedding) as similarity,
      d.last_verified_at
    from public.document_chunks dc
    join public.documents d on d.id = dc.document_id
    where 1 - (dc.embedding <=> query_embedding) > match_threshold
    order by dc.embedding <=> query_embedding
    limit match_count;
end;
$$;

create or replace function public.search_documents_fts(
  query_text text,
  match_count integer default 5
)
returns table (
  id uuid,
  document_id uuid,
  chunk_index integer,
  content text,
  metadata jsonb,
  title text,
  url text,
  source text,
  document_type text,
  rank double precision,
  last_verified_at timestamptz
)
language plpgsql stable
as $$
begin
  return query
    select
      dc.id,
      dc.document_id,
      dc.chunk_index,
      dc.content,
      dc.metadata,
      d.title,
      d.url,
      d.source,
      d.document_type,
      ts_rank_cd(
        to_tsvector('english', coalesce(dc.content, '') || ' ' || coalesce(d.title, '') || ' ' || coalesce(d.source, '')),
        websearch_to_tsquery('english', query_text)
      ) as rank,
      d.last_verified_at
    from public.document_chunks dc
    join public.documents d on d.id = dc.document_id
    where to_tsvector('english', coalesce(dc.content, '') || ' ' || coalesce(d.title, '') || ' ' || coalesce(d.source, ''))
      @@ websearch_to_tsquery('english', query_text)
    order by rank desc, d.last_verified_at desc nulls last
    limit match_count;
end;
$$;

-- 2. Retired ---------------------------------------------------------------------

drop table if exists public.chat_messages;

-- 20260331_internship_alerts.sql. Its shared pieces stay: set_updated_at() and
-- pgcrypto are used by other tables. Dependents first.
drop view if exists public.internship_listing_public;
drop function if exists public.match_internship_listings_for_user(uuid, integer);
drop function if exists public.compute_internship_match_score(
  text, text, text, text, text[], text[], text[], boolean, boolean, boolean, text
);
drop table if exists public.internship_alerts_sent;
drop table if exists public.user_internship_listing_state;
drop table if exists public.internship_alert_preferences;
drop table if exists public.internship_listings;

alter table public.profiles
  drop column if exists target_roles,
  drop column if exists preferred_locations,
  drop column if exists remote_only,
  drop column if exists alert_frequency;
