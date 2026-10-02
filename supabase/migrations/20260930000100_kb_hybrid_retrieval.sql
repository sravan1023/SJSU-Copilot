-- Knowledge base: a stored corpus, and hybrid retrieval over it.
--
-- Until now every answer was grounded by live web search: rewrite the query at
-- the provider, ask DDGS twice, crawl up to five pages, stuff them into the
-- prompt. `documents` and `document_chunks` have existed since 001 and have
-- always been empty; `match_documents` has never had a caller outside SQL.
--
-- Three reasons that is worth replacing, in priority order:
--
--   1. Grounding has no floor. A 40-question bench run on 2026-09-23 produced
--      ~33 DDGS failures and 13 questions that returned nothing at all -- from a
--      home IP with no concurrency.
--   2. The token ceiling. Groq's free tier is 8,000 tokens/minute for the whole
--      organisation and one grounded answer costs 4,000-5,500, so roughly 1.5
--      answers a minute. Whole crawled pages are most of that; a targeted chunk
--      is a fraction.
--   3. Latency. A turn pays a rewrite round trip, two searches and five crawls.
--
-- This migration only makes the schema able to hold and serve a corpus. Nothing
-- writes to it yet (backend/kb/, stage 4B) and nothing reads from it yet
-- (services/kb_retrieval.py behind KB_RETRIEVAL_ENABLED, stage 4C).
--
-- ---------------------------------------------------------------------------
-- Two things about this migration that are easy to get wrong
--
-- * A new function in `public` is not executable by anyone, including
--   `service_role`. 20260918000100 set `alter default privileges ... revoke
--   execute on functions from public`, and service_role reaches functions
--   *through* the PUBLIC grant -- it is not a superuser. Every function below is
--   therefore granted explicitly in section 12. Without that the backend fails
--   at runtime with `permission denied for function`, which is the most likely
--   silent failure in this change.
--
-- * A new table gets CRUD for anon and authenticated by default.
--   20260918000100 left `alter default privileges ... grant select, insert,
--   update, delete on tables to anon` in place. So each new table below enables
--   RLS explicitly and revokes those grants. A new table without both is
--   world-writable.
-- ---------------------------------------------------------------------------


-- 1. documents: provenance, freshness and access ------------------------------

-- `source_url` and `owner_department` from the original plan are deliberately
-- absent: the first duplicates `url`, and the second has no producer or consumer
-- anywhere in backend/, UI/ or supabase/.
alter table public.documents
  add column if not exists collection     text,
  add column if not exists audience_tags  text[] not null default '{}',
  add column if not exists visibility     text not null default 'public',
  add column if not exists fetched_at     timestamptz,
  add column if not exists etag           text,
  add column if not exists last_modified  text,
  add column if not exists version        int not null default 1,
  add column if not exists valid_from     date,
  add column if not exists valid_until    date;

-- `add constraint` has no `if not exists`; drop-then-add is the idiom the rest
-- of this set uses for constraints, policies and triggers.
alter table public.documents
  drop constraint if exists documents_visibility_check;
alter table public.documents
  add constraint documents_visibility_check
  check (visibility in ('public', 'authenticated', 'restricted'));

-- Separating relevance from access control is the point: `audience_tags` is a
-- ranking signal in the retrieval functions below, `visibility` is enforced in
-- RLS and in the function bodies. A public faculty page stays useful to a
-- student; a restricted one does not become visible because the audience
-- happens to match.
comment on column public.documents.audience_tags is
  'Relevance only -- a soft ranking boost. Never an access control decision.';
comment on column public.documents.visibility is
  'Access control. Enforced in RLS and in search_kb_chunks/match_kb_hybrid.';


-- 2. documents.url becomes the idempotency key --------------------------------

-- Re-ingestion needs somewhere to conflict. `url` is nullable in 001:192 and has
-- no unique constraint, so today a second crawl of the same page inserts a
-- second row.
--
-- The guard raises rather than warns: this is expected to run against an empty
-- table, and if it does not, failing loudly beats silently discarding half a
-- corpus. `content_hash` is left non-unique -- two URLs may legitimately serve
-- identical text.
do $guard$
begin
  if exists (select 1 from public.documents where url is null) then
    raise exception
      'public.documents has rows with a null url; resolve them before applying this migration (url becomes the upsert conflict target)';
  end if;
end $guard$;

alter table public.documents alter column url set not null;

-- Plain, not partial: PostgREST cannot infer a partial index for
-- `on_conflict=url`.
create unique index if not exists uq_documents_url
  on public.documents (url);


-- 3. document_chunks: keyword index, headings, and a usable vector dimension --

alter table public.document_chunks
  add column if not exists heading     text,
  add column if not exists token_count int;

-- The two-argument to_tsvector is mandatory. The one-argument form depends on
-- default_text_search_config and is only `stable`, so PostgreSQL refuses it in a
-- generated column.
--
-- This covers heading + content and nothing else, on purpose. The reason
-- search_documents_fts has never been indexable is that it builds its tsvector
-- from `dc.content || d.title || d.source` -- an expression spanning two tables,
-- which no index can cover. The chunker writes the page's heading path into
-- `heading`, which recovers most of what the title was contributing.
alter table public.document_chunks
  add column if not exists tsv tsvector
  generated always as (
    to_tsvector('english', coalesce(heading, '') || ' ' || coalesce(content, ''))
  ) stored;

-- 1536 was ada-002-shaped and was never written to (verified against live on
-- 2026-09-30: documents and document_chunks both hold 0 rows), so this is
-- metadata-only and rewrite-free.
--
-- 768 is gemini-embedding-2 with outputDimensionality=768, and the number is
-- not arbitrary:
--
--   * The model is natively **3072** dimensions, and pgvector 0.8.0 refuses an
--     HNSW index above 2000 -- "column cannot have more than 2000 dimensions for
--     hnsw index". So the vector has to be truncated to be indexable at all,
--     unless the column becomes halfvec. Measured, not assumed.
--   * Truncation is Matryoshka, so the prefix is still a usable embedding -- but
--     gemini-embedding-001 returns a truncated vector with an L2 norm of ~0.59,
--     while gemini-embedding-2 returns ~1.00. Cosine distance on an
--     unnormalised vector is wrong in a way nothing reports, so the choice of
--     model here is load-bearing and is recorded in backend/kb/embed.py.
alter table public.document_chunks
  alter column embedding type vector(768);

-- Makes re-ingestion idempotent, and leads with document_id, so the separate
-- index on that column the join has always wanted comes free.
create unique index if not exists uq_document_chunks_doc_idx
  on public.document_chunks (document_id, chunk_index);

create index if not exists idx_document_chunks_tsv
  on public.document_chunks using gin (tsv);

-- The ivfflat index from 001:207-210 was built on an empty table, so its
-- centroids were trained on nothing and recall would be permanently poor. HNSW
-- needs no training pass and tolerates incremental inserts, which is what an
-- ingestion pipeline does.
drop index if exists public.idx_document_chunks_embedding;

create index if not exists idx_document_chunks_embedding_hnsw
  on public.document_chunks using hnsw (embedding vector_cosine_ops)
  with (m = 16, ef_construction = 64);


-- 4. kb_sources: the curated allowlist ----------------------------------------

-- Shape follows public.ats_registry (20260410:5-19): a natural unique key, a
-- last-probed watermark, and a partial index over the active subset.
--
-- This is the answer to "where does the knowledge come from" -- a hand-written
-- list of public URLs, not an import of internal documents. SJSU's advertised
-- sitemap (indexmap.txt) is a 2013-2016 artifact whose per-department sitemaps
-- all 404, so there is no cheap discovery mechanism and `crawl_depth` ships at 0.
create table if not exists public.kb_sources (
  id                     uuid primary key default gen_random_uuid(),
  url                    text not null,
  collection             text not null,
  audience_tags          text[] not null default '{}',
  visibility             text not null default 'public'
                           check (visibility in ('public', 'authenticated', 'restricted')),
  -- Capped at 2 so a misconfigured seed cannot walk the whole site.
  crawl_depth            int not null default 0 check (crawl_depth between 0 and 2),
  -- Extra hosts this seed may follow links to. Deliberately per-source: the
  -- global PREFERRED_DOMAINS list in services/web_search.py ranks search
  -- results, it does not authorise fetches.
  allow_hosts            text[] not null default '{}',
  max_pages              int not null default 40,
  enabled                boolean not null default true,
  -- Weekly. catalog.sjsu.edu sets crawl-delay: 120 in robots.txt, so it is ~30
  -- pages an hour and wants its own, longer interval.
  fetch_interval_minutes int not null default 10080,
  last_fetched_at        timestamptz,
  last_status            text,
  created_at             timestamptz not null default now(),
  unique (url)
);

create index if not exists idx_kb_sources_due
  on public.kb_sources (last_fetched_at nulls first) where enabled;


-- 5. kb_ingest_runs: the coverage report --------------------------------------

-- Shape follows public.job_fetch_runs (002:83-93).
--
-- `skip_reasons` is the point of this table. Every page the crawler declines is
-- counted by reason -- {"pdf": 7, "robots": 2, "thin": 4, "http_403": 1} -- so
-- "how much of SJSU are we missing, and why" is a query rather than a guess.
create table if not exists public.kb_ingest_runs (
  id                uuid primary key default gen_random_uuid(),
  source_id         uuid references public.kb_sources(id) on delete set null,
  status            text not null check (status in ('success', 'failed', 'partial')),
  pages_fetched     int not null default 0,
  pages_unchanged   int not null default 0,
  pages_skipped     int not null default 0,
  skip_reasons      jsonb not null default '{}',
  documents_written int not null default 0,
  chunks_written    int not null default 0,
  embed_tokens      int not null default 0,
  error_message     text,
  started_at        timestamptz not null default now(),
  completed_at      timestamptz
);

create index if not exists idx_kb_ingest_runs_started_at
  on public.kb_ingest_runs (started_at desc);


-- 6. kb_ingest_jobs: resumable work -------------------------------------------

-- Claimed with `for update skip locked`.
--
-- This looked like premature machinery for a single sequential worker until
-- catalog.sjsu.edu turned out to set crawl-delay: 120 in robots.txt. Honouring
-- that is ~30 pages an hour, so the catalog is a ten-hour job that has to
-- survive being interrupted. That is exactly what a claimable queue with an
-- attempt count and a lock expiry is for.
create table if not exists public.kb_ingest_jobs (
  id            uuid primary key default gen_random_uuid(),
  source_id     uuid not null references public.kb_sources(id) on delete cascade,
  status        text not null default 'pending'
                  check (status in ('pending', 'running', 'done', 'failed')),
  attempts      int not null default 0,
  locked_by     text,
  locked_until  timestamptz,
  error         text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

-- Partial, over the only subset a worker ever scans.
create index if not exists idx_kb_ingest_jobs_claimable
  on public.kb_ingest_jobs (created_at) where status in ('pending', 'running');

drop trigger if exists on_kb_ingest_jobs_updated on public.kb_ingest_jobs;
create trigger on_kb_ingest_jobs_updated
  before update on public.kb_ingest_jobs
  for each row execute function public.handle_updated_at();


-- 7. RLS ----------------------------------------------------------------------

-- 001:214-219 gave any signed-in user every row of both tables and anon none,
-- which predates both guests and the visibility column.
--
-- An honest note on what this does and does not buy. The chat backend reads
-- through the service key, and service_role bypasses RLS -- so the boundary that
-- actually protects a guest at request time is `p_include_authenticated` inside
-- the retrieval functions below, set from `principal.kind = 'user'` and
-- defaulting to false. These policies are a second, independent enforcement of
-- the same rule, so `visibility` is a database boundary and not only a backend
-- `if`, and they are what a future browser-side KB browser would read through.
drop policy if exists "Authenticated users can read documents" on public.documents;
drop policy if exists "Authenticated users can read document chunks" on public.document_chunks;

-- Two narrow policies rather than one expression: policies OR together, and
-- these read more clearly than a single compound predicate.
drop policy if exists "Public documents are readable by anyone" on public.documents;
create policy "Public documents are readable by anyone"
  on public.documents for select
  to anon, authenticated
  using (visibility = 'public');

drop policy if exists "Signed-in users also read authenticated documents" on public.documents;
create policy "Signed-in users also read authenticated documents"
  on public.documents for select
  to authenticated
  using (visibility in ('public', 'authenticated'));

drop policy if exists "Public document chunks are readable by anyone" on public.document_chunks;
create policy "Public document chunks are readable by anyone"
  on public.document_chunks for select
  to anon, authenticated
  using (exists (
    select 1 from public.documents d
    where d.id = document_chunks.document_id
      and d.visibility = 'public'
  ));

drop policy if exists "Signed-in users also read authenticated document chunks" on public.document_chunks;
create policy "Signed-in users also read authenticated document chunks"
  on public.document_chunks for select
  to authenticated
  using (exists (
    select 1 from public.documents d
    where d.id = document_chunks.document_id
      and d.visibility in ('public', 'authenticated')
  ));

-- No insert/update/delete policy on either table. service_role writes by
-- bypassing RLS -- the 001:214-221 shape, which 20260916000200:12 restored for
-- job_sources after 002:107-110's `for all` policy turned out to be a stored
-- SSRF hole.

-- The three new tables are operator state. RLS on with zero policies means
-- service_role only.
alter table public.kb_sources     enable row level security;
alter table public.kb_ingest_runs enable row level security;
alter table public.kb_ingest_jobs enable row level security;

-- Whole privileges only: backend/tests/test_migrations.py bans the column-level
-- form, which is a silent no-op against a table-level grant.
revoke all on public.kb_sources     from anon, authenticated;
revoke all on public.kb_ingest_runs from anon, authenticated;
revoke all on public.kb_ingest_jobs from anon, authenticated;

grant select on public.documents       to anon, authenticated;
grant select on public.document_chunks to anon, authenticated;
revoke insert, update, delete on public.documents       from anon, authenticated;
revoke insert, update, delete on public.document_chunks from anon, authenticated;


-- 8. search_documents_fts: fix the return type, and make it indexable ---------

-- Two defects, both from 20260915000000 capturing a hand-built live object
-- verbatim:
--
--   1. `rank double precision` while ts_rank_cd returns `real`. plpgsql does not
--      widen real -> double precision for a composite OUT column, so the first
--      matching row raises 42804. It has never fired because the table has
--      always been empty and an empty result set produces no tuple to check.
--   2. The tsvector was built inline from dc.content || d.title || d.source, an
--      expression across two tables, recomputed per row in both the WHERE and
--      the ORDER BY. No index could ever cover it.
--
-- The signature is preserved exactly: verify_policies.sql section 13 asserts
-- to_regprocedure('public.search_documents_fts(text, integer)') is not null.
-- Changing `returns table` requires a drop; create or replace cannot do it.
drop function if exists public.search_documents_fts(text, integer);

create function public.search_documents_fts(
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
  rank real,
  last_verified_at timestamptz
)
language plpgsql stable
as $fn$
declare
  q tsquery := websearch_to_tsquery('english', coalesce(query_text, ''));
begin
  -- websearch_to_tsquery yields an empty tsquery for input that is all stop
  -- words or punctuation, and `@@` against it matches nothing. Skip the scan.
  --
  -- numnode(), not `q = ''::tsquery`: the comparison parses an empty string,
  -- and the parser raises `NOTICE: text-search query doesn't contain lexemes`
  -- every time. This runs on every chat turn, so the guard has to be silent.
  if q is null or numnode(q) = 0 then
    return;
  end if;

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
      ts_rank_cd(dc.tsv, q) as rank,
      d.last_verified_at
    from public.document_chunks dc
    join public.documents d on d.id = dc.document_id
    where dc.tsv @@ q
    order by ts_rank_cd(dc.tsv, q) desc, d.last_verified_at desc nulls last
    limit match_count;
end;
$fn$;


-- 9. search_kb_chunks: the keyword retriever the backend calls ----------------

-- A new function rather than more parameters on search_documents_fts, whose
-- signature is pinned by a test and which has no notion of visibility or
-- audience.
--
-- `p_include_authenticated` defaults to false so that a caller which forgets to
-- pass it under-shares rather than leaks.
create or replace function public.search_kb_chunks(
  query_text text,
  p_audience text default null,
  p_include_authenticated boolean default false,
  match_count integer default 10
)
returns table (
  document_id uuid,
  chunk_index integer,
  heading text,
  content text,
  title text,
  url text,
  source text,
  collection text,
  audience_tags text[],
  rank real,
  fetched_at timestamptz,
  last_verified_at timestamptz,
  valid_until date
)
language plpgsql stable
as $fn$
declare
  q tsquery := websearch_to_tsquery('english', coalesce(query_text, ''));
begin
  if q is null or numnode(q) = 0 then
    return;
  end if;

  return query
    select
      dc.document_id,
      dc.chunk_index,
      dc.heading,
      dc.content,
      d.title,
      d.url,
      d.source,
      d.collection,
      d.audience_tags,
      ts_rank_cd(dc.tsv, q) as rank,
      d.fetched_at,
      d.last_verified_at,
      d.valid_until
    from public.document_chunks dc
    join public.documents d on d.id = dc.document_id
    where dc.tsv @@ q
      and (
        d.visibility = 'public'
        or (p_include_authenticated and d.visibility = 'authenticated')
      )
    -- audience_tags is a boost, never a filter: a public page tagged for faculty
    -- is still the right answer to a student's question about it.
    order by
      (p_audience is not null and p_audience = any (d.audience_tags)) desc,
      ts_rank_cd(dc.tsv, q) desc,
      d.last_verified_at desc nulls last
    limit match_count;
end;
$fn$;


-- 10. match_kb_hybrid: keyword and vector, fused by reciprocal rank -----------

-- Reciprocal rank fusion rather than a weighted score sum, because ts_rank_cd
-- and cosine distance are on unrelated scales and no constant converts one to
-- the other. RRF uses only each result's *position* in its own list, so the
-- scales never have to be reconciled.
--
-- A chunk with a null embedding simply does not appear in the vector arm. That
-- is deliberate: ingestion writes the chunk even when the embedding API fails,
-- so the keyword half keeps working and the row is not lost.
create or replace function public.match_kb_hybrid(
  query_text text,
  query_embedding vector default null,
  p_audience text default null,
  p_include_authenticated boolean default false,
  match_count integer default 10,
  rrf_k integer default 60
)
returns table (
  document_id uuid,
  chunk_index integer,
  heading text,
  content text,
  title text,
  url text,
  source text,
  collection text,
  audience_tags text[],
  rank real,
  fetched_at timestamptz,
  last_verified_at timestamptz,
  valid_until date
)
language sql stable
as $fn$
  with visible as (
    select dc.id, dc.document_id, dc.chunk_index, dc.heading, dc.content,
           dc.tsv, dc.embedding,
           d.title, d.url, d.source, d.collection, d.audience_tags,
           d.fetched_at, d.last_verified_at, d.valid_until
      from public.document_chunks dc
      join public.documents d on d.id = dc.document_id
     where d.visibility = 'public'
        or (p_include_authenticated and d.visibility = 'authenticated')
  ),
  q as (
    select websearch_to_tsquery('english', coalesce(query_text, '')) as tsq
  ),
  keyword as (
    select v.id,
           row_number() over (order by ts_rank_cd(v.tsv, q.tsq) desc) as pos
      from visible v, q
     where q.tsq is not null
       and numnode(q.tsq) > 0
       and v.tsv @@ q.tsq
     limit greatest(match_count * 4, 40)
  ),
  semantic as (
    select v.id,
           row_number() over (order by v.embedding <=> query_embedding) as pos
      from visible v
     where query_embedding is not null
       and v.embedding is not null
     limit greatest(match_count * 4, 40)
  ),
  fused as (
    select coalesce(k.id, s.id) as id,
           coalesce(1.0 / (rrf_k + k.pos), 0.0)
             + coalesce(1.0 / (rrf_k + s.pos), 0.0) as score
      from keyword k
      full outer join semantic s on s.id = k.id
  )
  select v.document_id, v.chunk_index, v.heading, v.content,
         v.title, v.url, v.source, v.collection, v.audience_tags,
         f.score::real as rank,
         v.fetched_at, v.last_verified_at, v.valid_until
    from fused f
    join visible v on v.id = f.id
   order by
     (p_audience is not null and p_audience = any (v.audience_tags)) desc,
     f.score desc,
     v.last_verified_at desc nulls last
   limit match_count;
$fn$;


-- 11. replace_document_chunks: one transaction --------------------------------

-- Over PostgREST, delete-then-insert is two requests, and a crash between them
-- leaves a document with zero chunks -- present, hashed, apparently ingested,
-- and silently unretrievable. Fifteen lines of plpgsql buys atomicity per
-- document.
create or replace function public.replace_document_chunks(
  p_document_id uuid,
  p_chunks jsonb
)
returns integer
language plpgsql
as $fn$
declare
  written integer;
begin
  delete from public.document_chunks where document_id = p_document_id;

  insert into public.document_chunks
    (document_id, chunk_index, heading, content, token_count, embedding, metadata)
  select
    p_document_id,
    (c->>'chunk_index')::int,
    c->>'heading',
    c->>'content',
    nullif(c->>'token_count', '')::int,
    case
      when c->'embedding' is null or c->'embedding' = 'null'::jsonb then null
      else (c->>'embedding')::vector
    end,
    coalesce(c->'metadata', '{}'::jsonb)
  from jsonb_array_elements(coalesce(p_chunks, '[]'::jsonb)) as c;

  get diagnostics written = row_count;
  return written;
end;
$fn$;


-- 12. Grants -- revoke by hand, because the default privileges do not hold -----

-- **20260918000100's "new functions now fail closed" is false, and this
-- migration is the first thing to prove it.** That migration set
--
--   alter default privileges for role postgres in schema public
--     revoke execute on functions from public;
--
-- and its comment, plus docs/HANDOFF.md, claim new functions are therefore
-- unreachable until granted by name. Measured on
-- supabase/postgres:17.6.1.084 (2026-09-30): a function created by postgres
-- after that statement still carries `=X/postgres` -- EXECUTE to PUBLIC -- so
-- anon and authenticated can call it by inheritance.
--
-- The reason is that PUBLIC's EXECUTE on a function is the *built-in* default,
-- not an entry in pg_default_acl. The image ships a pg_default_acl row for role
-- postgres listing postgres/anon/authenticated/service_role; the lockdown
-- removed anon and authenticated from it, which worked, but there was no PUBLIC
-- entry to remove, and a pg_default_acl row that omits PUBLIC does not suppress
-- the built-in grant. Verified both ways: an identical function with an
-- explicit revoke ends up anon=false, authenticated=false, service_role=true.
--
-- So every function above is revoked by hand, the way 20260916000200 and
-- 20260917000200 each had to do it. `from public` must come first -- anon and
-- authenticated hold EXECUTE through PUBLIC, so revoking from them alone is the
-- silent no-op that trap already warns about.
revoke all on function public.search_documents_fts(text, integer)
  from public, anon, authenticated;
revoke all on function public.search_kb_chunks(text, text, boolean, integer)
  from public, anon, authenticated;
revoke all on function public.match_kb_hybrid(text, public.vector, text, boolean, integer, integer)
  from public, anon, authenticated;
revoke all on function public.replace_document_chunks(uuid, jsonb)
  from public, anon, authenticated;

-- Then grant back only what the backend needs. service_role reached these
-- through PUBLIC, so the revoke above took its access too.
grant execute on function public.search_documents_fts(text, integer) to service_role;
grant execute on function public.search_kb_chunks(text, text, boolean, integer) to service_role;
grant execute on function public.match_kb_hybrid(text, public.vector, text, boolean, integer, integer) to service_role;
grant execute on function public.replace_document_chunks(uuid, jsonb) to service_role;

-- Deliberately NOT granted to anon or authenticated. The browser never queries
-- the knowledge base directly; the backend does, with the service key, and
-- passes visibility in as a parameter it controls.
