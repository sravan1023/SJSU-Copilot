


SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;


CREATE SCHEMA IF NOT EXISTS "public";


ALTER SCHEMA "public" OWNER TO "pg_database_owner";


COMMENT ON SCHEMA "public" IS 'standard public schema';



CREATE TYPE "public"."affiliation_kind" AS ENUM (
    'student',
    'alumni',
    'faculty',
    'staff',
    'applicant',
    'community'
);


ALTER TYPE "public"."affiliation_kind" OWNER TO "postgres";


CREATE TYPE "public"."memory_category" AS ENUM (
    'preference',
    'decision',
    'constraint',
    'fact',
    'task',
    'context'
);


ALTER TYPE "public"."memory_category" OWNER TO "postgres";


CREATE TYPE "public"."memory_scope" AS ENUM (
    'global',
    'project',
    'conversation'
);


ALTER TYPE "public"."memory_scope" OWNER TO "postgres";


CREATE TYPE "public"."memory_status" AS ENUM (
    'active',
    'superseded',
    'archived'
);


ALTER TYPE "public"."memory_status" OWNER TO "postgres";


CREATE TYPE "public"."verification_status" AS ENUM (
    'declared',
    'pending',
    'verified',
    'expired',
    'revoked'
);


ALTER TYPE "public"."verification_status" OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."archive_stale_memories"("p_conversation_age_days" integer DEFAULT 90, "p_min_importance" integer DEFAULT 3) RETURNS integer
    LANGUAGE "plpgsql"
    AS $$
declare
  archived_count int;
begin
  update memories
  set status = 'archived'
  where status = 'active'
    and scope = 'conversation'
    and importance <= p_min_importance
    and updated_at < now() - make_interval(days => p_conversation_age_days);

  get diagnostics archived_count = row_count;
  return archived_count;
end;
$$;


ALTER FUNCTION "public"."archive_stale_memories"("p_conversation_age_days" integer, "p_min_importance" integer) OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."create_default_behavior_settings"() RETURNS "trigger"
    LANGUAGE "plpgsql" SECURITY DEFINER
    SET "search_path" TO 'public'
    AS $$
begin
  if not exists (
    select 1
      from public.behavior_settings
     where user_id = new.id
       and project_id is null
       and conversation_id is null
  ) then
    insert into public.behavior_settings (user_id) values (new.id);
  end if;
  return new;
end;
$$;


ALTER FUNCTION "public"."create_default_behavior_settings"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."enforce_sjsu_email"() RETURNS "trigger"
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
begin
  if new.email is null or not (new.email ilike '%@sjsu.edu') then
    raise exception 'Only @sjsu.edu email addresses are allowed.';
  end if;
  return new;
end;
$$;


ALTER FUNCTION "public"."enforce_sjsu_email"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."freeze_profile_privilege_columns"() RETURNS "trigger"
    LANGUAGE "plpgsql"
    SET "search_path" TO 'public'
    AS $$
begin
  -- coalesce, not a bare `auth.role() <> 'service_role'`: on a direct psql or
  -- migration connection auth.role() is null, that comparison evaluates to
  -- NULL, the branch is skipped, and the trigger would silently do nothing.
  if coalesce(auth.role(), current_user) not in
       ('service_role', 'supabase_admin', 'postgres') then
    new.role := old.role;
    new.email := old.email;
  end if;
  return new;
end;
$$;


ALTER FUNCTION "public"."freeze_profile_privilege_columns"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."generate_job_dedupe_hash"("input_title" "text", "input_company" "text") RETURNS "text"
    LANGUAGE "sql" IMMUTABLE
    AS $$
  select md5(lower(trim(coalesce(input_title, ''))) || '|' || lower(trim(coalesce(input_company, ''))));
$$;


ALTER FUNCTION "public"."generate_job_dedupe_hash"("input_title" "text", "input_company" "text") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."get_memory_context"("p_user_id" "uuid", "p_conversation_id" "uuid", "p_project_id" "uuid" DEFAULT NULL::"uuid", "p_max_global" integer DEFAULT 20, "p_max_project" integer DEFAULT 30, "p_max_conversation" integer DEFAULT 20) RETURNS TABLE("id" "uuid", "scope" "public"."memory_scope", "content" "text", "category" "public"."memory_category", "confidence" double precision, "importance" integer, "created_at" timestamp with time zone, "updated_at" timestamp with time zone)
    LANGUAGE "sql" STABLE
    AS $$
  -- Global memories (user-wide)
  (
    select m.id, m.scope, m.content, m.category, m.confidence, m.importance, m.created_at, m.updated_at
    from memories m
    where m.user_id = p_user_id
      and m.scope = 'global'
      and m.status = 'active'
    order by m.importance desc, m.updated_at desc
    limit p_max_global
  )
  union all
  -- Project memories (if conversation belongs to a project)
  (
    select m.id, m.scope, m.content, m.category, m.confidence, m.importance, m.created_at, m.updated_at
    from memories m
    where m.project_id = p_project_id
      and m.scope = 'project'
      and m.status = 'active'
      and p_project_id is not null
    order by m.importance desc, m.updated_at desc
    limit p_max_project
  )
  union all
  -- Conversation memories
  (
    select m.id, m.scope, m.content, m.category, m.confidence, m.importance, m.created_at, m.updated_at
    from memories m
    where m.conversation_id = p_conversation_id
      and m.scope = 'conversation'
      and m.status = 'active'
    order by m.importance desc, m.updated_at desc
    limit p_max_conversation
  );
$$;


ALTER FUNCTION "public"."get_memory_context"("p_user_id" "uuid", "p_conversation_id" "uuid", "p_project_id" "uuid", "p_max_global" integer, "p_max_project" integer, "p_max_conversation" integer) OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."handle_new_user"() RETURNS "trigger"
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
begin
  insert into public.profiles (id, email, full_name)
  values (
    new.id,
    new.email,
    coalesce(new.raw_user_meta_data ->> 'full_name', '')
  );
  return new;
end;
$$;


ALTER FUNCTION "public"."handle_new_user"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."handle_updated_at"() RETURNS "trigger"
    LANGUAGE "plpgsql"
    AS $$
begin
  new.updated_at = now();
  return new;
end;
$$;


ALTER FUNCTION "public"."handle_updated_at"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."has_grant"("p_capability" "text") RETURNS boolean
    LANGUAGE "sql" STABLE SECURITY DEFINER
    SET "search_path" TO 'public'
    AS $$
  select exists (
    select 1
      from public.admin_grants g
     where g.user_id = auth.uid()
       and g.capability = p_capability
       and (g.expires_at is null or g.expires_at > now())
  );
$$;


ALTER FUNCTION "public"."has_grant"("p_capability" "text") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."match_documents"("query_embedding" "public"."vector", "match_count" integer DEFAULT 5, "match_threshold" double precision DEFAULT 0.78) RETURNS TABLE("id" "uuid", "document_id" "uuid", "chunk_index" integer, "content" "text", "metadata" "jsonb", "title" "text", "url" "text", "source" "text", "document_type" "text", "similarity" double precision, "last_verified_at" timestamp with time zone)
    LANGUAGE "plpgsql" STABLE
    AS $$
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


ALTER FUNCTION "public"."match_documents"("query_embedding" "public"."vector", "match_count" integer, "match_threshold" double precision) OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."match_kb_hybrid"("query_text" "text", "query_embedding" "public"."vector" DEFAULT NULL::"public"."vector", "p_audience" "text" DEFAULT NULL::"text", "p_include_authenticated" boolean DEFAULT false, "match_count" integer DEFAULT 10, "rrf_k" integer DEFAULT 60) RETURNS TABLE("document_id" "uuid", "chunk_index" integer, "heading" "text", "content" "text", "title" "text", "url" "text", "source" "text", "collection" "text", "audience_tags" "text"[], "rank" real, "fetched_at" timestamp with time zone, "last_verified_at" timestamp with time zone, "valid_until" "date")
    LANGUAGE "sql" STABLE
    AS $$
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
$$;


ALTER FUNCTION "public"."match_kb_hybrid"("query_text" "text", "query_embedding" "public"."vector", "p_audience" "text", "p_include_authenticated" boolean, "match_count" integer, "rrf_k" integer) OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."pipeline_state_set_updated_at"() RETURNS "trigger"
    LANGUAGE "plpgsql"
    AS $$
begin
  new.updated_at = now();
  return new;
end;
$$;


ALTER FUNCTION "public"."pipeline_state_set_updated_at"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."promote_memory_to_project"("p_memory_id" "uuid", "p_project_id" "uuid") RETURNS "uuid"
    LANGUAGE "plpgsql"
    AS $$
declare
  new_id uuid;
begin
  insert into memories (
    user_id, scope, project_id, conversation_id,
    content, category, confidence, importance, status,
    source_conversation_id, source_message_id
  )
  select
    user_id, 'project', p_project_id, null,
    content, category, confidence, importance, 'active',
    source_conversation_id, source_message_id
  from memories
  where id = p_memory_id
    and scope = 'conversation'
    and status = 'active'
  returning id into new_id;

  -- Mark the original as superseded
  if new_id is not null then
    update memories
    set status = 'superseded', superseded_by = new_id
    where id = p_memory_id;
  end if;

  return new_id;
end;
$$;


ALTER FUNCTION "public"."promote_memory_to_project"("p_memory_id" "uuid", "p_project_id" "uuid") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."replace_document_chunks"("p_document_id" "uuid", "p_chunks" "jsonb") RETURNS integer
    LANGUAGE "plpgsql"
    AS $$
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
$$;


ALTER FUNCTION "public"."replace_document_chunks"("p_document_id" "uuid", "p_chunks" "jsonb") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."search_documents_fts"("query_text" "text", "match_count" integer DEFAULT 5) RETURNS TABLE("id" "uuid", "document_id" "uuid", "chunk_index" integer, "content" "text", "metadata" "jsonb", "title" "text", "url" "text", "source" "text", "document_type" "text", "rank" real, "last_verified_at" timestamp with time zone)
    LANGUAGE "plpgsql" STABLE
    AS $$
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
$$;


ALTER FUNCTION "public"."search_documents_fts"("query_text" "text", "match_count" integer) OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."search_kb_chunks"("query_text" "text", "p_audience" "text" DEFAULT NULL::"text", "p_include_authenticated" boolean DEFAULT false, "match_count" integer DEFAULT 10) RETURNS TABLE("document_id" "uuid", "chunk_index" integer, "heading" "text", "content" "text", "title" "text", "url" "text", "source" "text", "collection" "text", "audience_tags" "text"[], "rank" real, "fetched_at" timestamp with time zone, "last_verified_at" timestamp with time zone, "valid_until" "date")
    LANGUAGE "plpgsql" STABLE
    AS $$
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
$$;


ALTER FUNCTION "public"."search_kb_chunks"("query_text" "text", "p_audience" "text", "p_include_authenticated" boolean, "match_count" integer) OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."set_conversation_preview"() RETURNS "trigger"
    LANGUAGE "plpgsql" SECURITY DEFINER
    SET "search_path" TO 'public'
    AS $$
begin
  update public.conversations
     set last_message_preview = case
           when length(new.content) > 80 then left(new.content, 80) || '...'
           else new.content
         end,
         updated_at = now()
   where id = new.conversation_id;
  return new;
end;
$$;


ALTER FUNCTION "public"."set_conversation_preview"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."set_updated_at"() RETURNS "trigger"
    LANGUAGE "plpgsql"
    AS $$
begin
  new.updated_at = now();
  return new;
end;
$$;


ALTER FUNCTION "public"."set_updated_at"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."update_pipeline_marker_if_expected"("p_pipeline_key" "text", "p_expected_previous_top_url" "text", "p_new_previous_top_url" "text", "p_last_run_id" "uuid") RETURNS boolean
    LANGUAGE "plpgsql" SECURITY DEFINER
    SET "search_path" TO 'public'
    AS $$
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


ALTER FUNCTION "public"."update_pipeline_marker_if_expected"("p_pipeline_key" "text", "p_expected_previous_top_url" "text", "p_new_previous_top_url" "text", "p_last_run_id" "uuid") OWNER TO "postgres";

SET default_tablespace = '';

SET default_table_access_method = "heap";


CREATE TABLE IF NOT EXISTS "public"."admin_grants" (
    "user_id" "uuid" NOT NULL,
    "capability" "text" NOT NULL,
    "granted_by" "uuid",
    "granted_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "expires_at" timestamp with time zone,
    CONSTRAINT "admin_grants_capability_check" CHECK (("capability" ~ '^[a-z][a-z0-9_]{1,63}$'::"text"))
);


ALTER TABLE "public"."admin_grants" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."ats_registry" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "slug" "text" NOT NULL,
    "ats" "text" NOT NULL,
    "display_name" "text" DEFAULT ''::"text" NOT NULL,
    "found" boolean DEFAULT false NOT NULL,
    "enabled" boolean DEFAULT true NOT NULL,
    "last_probed_at" timestamp with time zone DEFAULT "now"(),
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    CONSTRAINT "ats_registry_ats_check" CHECK (("ats" = ANY (ARRAY['greenhouse'::"text", 'lever'::"text", 'ashby'::"text"])))
);


ALTER TABLE "public"."ats_registry" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."behavior_feedback_log" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "response_id" "uuid" NOT NULL,
    "user_id" "uuid" NOT NULL,
    "conversation_id" "uuid",
    "behavior_snapshot" "jsonb" DEFAULT '{}'::"jsonb" NOT NULL,
    "validators_run" "text"[] DEFAULT '{}'::"text"[] NOT NULL,
    "validators_passed" boolean DEFAULT true NOT NULL,
    "repairs_applied" "text"[] DEFAULT '{}'::"text"[] NOT NULL,
    "user_feedback" "text",
    "model_used" "text",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    CONSTRAINT "behavior_feedback_log_user_feedback_check" CHECK (("user_feedback" = ANY (ARRAY['thumbs_up'::"text", 'thumbs_down'::"text"])))
);


ALTER TABLE "public"."behavior_feedback_log" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."behavior_settings" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "response_tone" "text" DEFAULT 'friendly'::"text",
    "response_length" "text" DEFAULT 'balanced'::"text",
    "response_format" "text" DEFAULT 'markdown'::"text",
    "emoji_usage" "text" DEFAULT 'occasional'::"text",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "priority_stack" "jsonb" DEFAULT '["safety", "accuracy", "task_completion", "clarity", "speed", "warmth"]'::"jsonb",
    "project_id" "uuid",
    "conversation_id" "uuid",
    CONSTRAINT "behavior_settings_emoji_usage_check" CHECK (("emoji_usage" = ANY (ARRAY['none'::"text", 'occasional'::"text", 'frequent'::"text"]))),
    CONSTRAINT "behavior_settings_response_format_check" CHECK (("response_format" = ANY (ARRAY['plain'::"text", 'markdown'::"text", 'bullet-heavy'::"text"]))),
    CONSTRAINT "behavior_settings_response_length_check" CHECK (("response_length" = ANY (ARRAY['concise'::"text", 'balanced'::"text", 'detailed'::"text"]))),
    CONSTRAINT "behavior_settings_response_tone_check" CHECK (("response_tone" = ANY (ARRAY['professional'::"text", 'friendly'::"text", 'casual'::"text", 'academic'::"text"])))
);


ALTER TABLE "public"."behavior_settings" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."campus_current" (
    "source_key" "text" NOT NULL,
    "scope_key" "text" NOT NULL,
    "snapshot_id" "uuid" NOT NULL,
    "verified_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."campus_current" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."campus_refresh_runs" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "started_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "finished_at" timestamp with time zone,
    "outcome" "text" DEFAULT 'running'::"text" NOT NULL,
    "stats" "jsonb" DEFAULT '{}'::"jsonb" NOT NULL,
    "error" "text",
    CONSTRAINT "campus_refresh_runs_outcome_check" CHECK (("outcome" = ANY (ARRAY['running'::"text", 'success'::"text", 'partial'::"text", 'failed'::"text"])))
);


ALTER TABLE "public"."campus_refresh_runs" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."campus_snapshots" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "source_key" "text" NOT NULL,
    "scope_key" "text" NOT NULL,
    "source_url" "text" NOT NULL,
    "fetched_from" "text",
    "fetched_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "page_last_updated" "date",
    "content_hash" "text" NOT NULL,
    "row_count" integer DEFAULT 0 NOT NULL,
    "header" "jsonb",
    "checks" "jsonb" DEFAULT '{}'::"jsonb" NOT NULL,
    "status" "text" DEFAULT 'staged'::"text" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    CONSTRAINT "campus_snapshots_row_count_check" CHECK (("row_count" >= 0)),
    CONSTRAINT "campus_snapshots_scope_key_check" CHECK (("scope_key" ~ '^((spring|summer|fall|winter)-20[0-9]{2}|ay-20[0-9]{2}-20[0-9]{2})$'::"text")),
    CONSTRAINT "campus_snapshots_source_key_check" CHECK (("source_key" = ANY (ARRAY['schedule'::"text", 'registrar'::"text", 'academic'::"text", 'exams'::"text", 'bursar'::"text"]))),
    CONSTRAINT "campus_snapshots_status_check" CHECK (("status" = ANY (ARRAY['staged'::"text", 'current'::"text", 'superseded'::"text", 'rejected'::"text"])))
);


ALTER TABLE "public"."campus_snapshots" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."conversation_summaries" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "conversation_id" "uuid" NOT NULL,
    "summary" "text" NOT NULL,
    "message_count" integer DEFAULT 0 NOT NULL,
    "last_summarized_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."conversation_summaries" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."conversations" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "title" "text",
    "last_message_preview" "text",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "project_id" "uuid",
    "audience" "text",
    CONSTRAINT "conversations_audience_check" CHECK ((("audience" IS NULL) OR ("audience" = ANY (ARRAY['student'::"text", 'alumni'::"text", 'guest'::"text", 'faculty'::"text"]))))
);


ALTER TABLE "public"."conversations" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."document_chunks" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "document_id" "uuid" NOT NULL,
    "chunk_index" integer NOT NULL,
    "content" "text" NOT NULL,
    "embedding" "public"."vector"(768),
    "metadata" "jsonb" DEFAULT '{}'::"jsonb",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "heading" "text",
    "token_count" integer,
    "tsv" "tsvector" GENERATED ALWAYS AS ("to_tsvector"('"english"'::"regconfig", ((COALESCE("heading", ''::"text") || ' '::"text") || COALESCE("content", ''::"text")))) STORED
);


ALTER TABLE "public"."document_chunks" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."documents" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "source" "text",
    "title" "text" NOT NULL,
    "url" "text" NOT NULL,
    "document_type" "text",
    "metadata" "jsonb" DEFAULT '{}'::"jsonb",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "last_verified_at" timestamp with time zone,
    "content_hash" "text",
    "collection" "text",
    "audience_tags" "text"[] DEFAULT '{}'::"text"[] NOT NULL,
    "visibility" "text" DEFAULT 'public'::"text" NOT NULL,
    "fetched_at" timestamp with time zone,
    "etag" "text",
    "last_modified" "text",
    "version" integer DEFAULT 1 NOT NULL,
    "valid_from" "date",
    "valid_until" "date",
    CONSTRAINT "documents_visibility_check" CHECK (("visibility" = ANY (ARRAY['public'::"text", 'authenticated'::"text", 'restricted'::"text"])))
);


ALTER TABLE "public"."documents" OWNER TO "postgres";


COMMENT ON COLUMN "public"."documents"."audience_tags" IS 'Relevance only -- a soft ranking boost. Never an access control decision.';



COMMENT ON COLUMN "public"."documents"."visibility" IS 'Access control. Enforced in RLS and in search_kb_chunks/match_kb_hybrid.';



CREATE TABLE IF NOT EXISTS "public"."job_fetch_runs" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "source_id" "uuid",
    "status" "text" NOT NULL,
    "fetched_count" integer DEFAULT 0 NOT NULL,
    "inserted_count" integer DEFAULT 0 NOT NULL,
    "deduped_count" integer DEFAULT 0 NOT NULL,
    "error_message" "text",
    "started_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "completed_at" timestamp with time zone,
    CONSTRAINT "job_fetch_runs_status_check" CHECK (("status" = ANY (ARRAY['success'::"text", 'failed'::"text", 'partial'::"text"])))
);


ALTER TABLE "public"."job_fetch_runs" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."job_listings" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "external_id" "text",
    "title" "text" NOT NULL,
    "company" "text" NOT NULL,
    "location" "text",
    "remote_status" "text" DEFAULT 'unknown'::"text" NOT NULL,
    "job_type" "text" DEFAULT 'other'::"text" NOT NULL,
    "salary" "text",
    "experience_level" "text",
    "tags" "text"[] DEFAULT '{}'::"text"[] NOT NULL,
    "description" "text",
    "apply_url" "text" NOT NULL,
    "source_name" "text",
    "source_url" "text",
    "posted_date" "date",
    "fetched_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "dedupe_hash" "text" GENERATED ALWAYS AS ("public"."generate_job_dedupe_hash"("title", "company")) STORED,
    CONSTRAINT "job_listings_job_type_check" CHECK (("job_type" = ANY (ARRAY['internship'::"text", 'new_grad'::"text", 'full_time'::"text", 'part_time'::"text", 'contract'::"text", 'other'::"text"]))),
    CONSTRAINT "job_listings_remote_status_check" CHECK (("remote_status" = ANY (ARRAY['remote'::"text", 'hybrid'::"text", 'onsite'::"text", 'unknown'::"text"])))
);


ALTER TABLE "public"."job_listings" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."job_sources" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "name" "text" NOT NULL,
    "source_type" "text" NOT NULL,
    "source_url" "text",
    "config" "jsonb" DEFAULT '{}'::"jsonb" NOT NULL,
    "enabled" boolean DEFAULT true NOT NULL,
    "fetch_interval_minutes" integer DEFAULT 360 NOT NULL,
    "last_fetched_at" timestamp with time zone,
    "next_fetch_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "last_error" "text",
    "created_by" "uuid",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    CONSTRAINT "job_sources_fetch_interval_minutes_check" CHECK (("fetch_interval_minutes" > 0)),
    CONSTRAINT "job_sources_source_type_check" CHECK (("source_type" = ANY (ARRAY['api'::"text", 'rss'::"text", 'html'::"text", 'mock'::"text"])))
);


ALTER TABLE "public"."job_sources" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."jobs_snapshot" (
    "id" bigint NOT NULL,
    "run_id" "uuid" NOT NULL,
    "rank_position" integer NOT NULL,
    "job_url" "text" NOT NULL,
    "company" "text" NOT NULL,
    "title" "text" NOT NULL,
    "source_record_id" "text",
    "raw_record" "jsonb" DEFAULT '{}'::"jsonb" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    CONSTRAINT "jobs_snapshot_rank_position_check" CHECK ((("rank_position" >= 1) AND ("rank_position" <= 100)))
);


ALTER TABLE "public"."jobs_snapshot" OWNER TO "postgres";


ALTER TABLE "public"."jobs_snapshot" ALTER COLUMN "id" ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME "public"."jobs_snapshot_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);



CREATE TABLE IF NOT EXISTS "public"."kb_ingest_jobs" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "source_id" "uuid" NOT NULL,
    "status" "text" DEFAULT 'pending'::"text" NOT NULL,
    "attempts" integer DEFAULT 0 NOT NULL,
    "locked_by" "text",
    "locked_until" timestamp with time zone,
    "error" "text",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    CONSTRAINT "kb_ingest_jobs_status_check" CHECK (("status" = ANY (ARRAY['pending'::"text", 'running'::"text", 'done'::"text", 'failed'::"text"])))
);


ALTER TABLE "public"."kb_ingest_jobs" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."kb_ingest_runs" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "source_id" "uuid",
    "status" "text" NOT NULL,
    "pages_fetched" integer DEFAULT 0 NOT NULL,
    "pages_unchanged" integer DEFAULT 0 NOT NULL,
    "pages_skipped" integer DEFAULT 0 NOT NULL,
    "skip_reasons" "jsonb" DEFAULT '{}'::"jsonb" NOT NULL,
    "documents_written" integer DEFAULT 0 NOT NULL,
    "chunks_written" integer DEFAULT 0 NOT NULL,
    "embed_tokens" integer DEFAULT 0 NOT NULL,
    "error_message" "text",
    "started_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "completed_at" timestamp with time zone,
    CONSTRAINT "kb_ingest_runs_status_check" CHECK (("status" = ANY (ARRAY['success'::"text", 'failed'::"text", 'partial'::"text"])))
);


ALTER TABLE "public"."kb_ingest_runs" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."kb_sources" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "url" "text" NOT NULL,
    "collection" "text" NOT NULL,
    "audience_tags" "text"[] DEFAULT '{}'::"text"[] NOT NULL,
    "visibility" "text" DEFAULT 'public'::"text" NOT NULL,
    "crawl_depth" integer DEFAULT 0 NOT NULL,
    "allow_hosts" "text"[] DEFAULT '{}'::"text"[] NOT NULL,
    "max_pages" integer DEFAULT 40 NOT NULL,
    "enabled" boolean DEFAULT true NOT NULL,
    "fetch_interval_minutes" integer DEFAULT 10080 NOT NULL,
    "last_fetched_at" timestamp with time zone,
    "last_status" "text",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    CONSTRAINT "kb_sources_crawl_depth_check" CHECK ((("crawl_depth" >= 0) AND ("crawl_depth" <= 2))),
    CONSTRAINT "kb_sources_visibility_check" CHECK (("visibility" = ANY (ARRAY['public'::"text", 'authenticated'::"text", 'restricted'::"text"])))
);


ALTER TABLE "public"."kb_sources" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."memories" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "scope" "public"."memory_scope" NOT NULL,
    "project_id" "uuid",
    "conversation_id" "uuid",
    "content" "text" NOT NULL,
    "category" "public"."memory_category" NOT NULL,
    "confidence" double precision DEFAULT 0.8 NOT NULL,
    "importance" integer DEFAULT 5 NOT NULL,
    "status" "public"."memory_status" DEFAULT 'active'::"public"."memory_status" NOT NULL,
    "superseded_by" "uuid",
    "source_conversation_id" "uuid",
    "source_message_id" "uuid",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    CONSTRAINT "memories_confidence_check" CHECK ((("confidence" >= (0)::double precision) AND ("confidence" <= (1)::double precision))),
    CONSTRAINT "memories_importance_check" CHECK ((("importance" >= 1) AND ("importance" <= 10))),
    CONSTRAINT "scope_conversation_check" CHECK ((("scope" <> 'conversation'::"public"."memory_scope") OR ("conversation_id" IS NOT NULL))),
    CONSTRAINT "scope_global_check" CHECK ((("scope" <> 'global'::"public"."memory_scope") OR (("project_id" IS NULL) AND ("conversation_id" IS NULL)))),
    CONSTRAINT "scope_project_check" CHECK ((("scope" <> 'project'::"public"."memory_scope") OR (("project_id" IS NOT NULL) AND ("conversation_id" IS NULL))))
);


ALTER TABLE "public"."memories" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."messages" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "conversation_id" "uuid" NOT NULL,
    "role" "text" NOT NULL,
    "content" "text" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "citations" "jsonb" DEFAULT '[]'::"jsonb" NOT NULL,
    "retrieval_meta" "jsonb" DEFAULT '{}'::"jsonb" NOT NULL,
    CONSTRAINT "messages_role_check" CHECK (("role" = ANY (ARRAY['user'::"text", 'assistant'::"text", 'system'::"text"])))
);


ALTER TABLE "public"."messages" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."pipeline_runs" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "pipeline_key" "text" DEFAULT 'intern_jobs_alert'::"text" NOT NULL,
    "status" "text" DEFAULT 'running'::"text" NOT NULL,
    "top_count" integer DEFAULT 0 NOT NULL,
    "new_jobs_count" integer DEFAULT 0 NOT NULL,
    "email_sent" boolean DEFAULT false NOT NULL,
    "error_message" "text",
    "started_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "finished_at" timestamp with time zone,
    CONSTRAINT "pipeline_runs_new_jobs_count_check" CHECK (("new_jobs_count" >= 0)),
    CONSTRAINT "pipeline_runs_status_check" CHECK (("status" = ANY (ARRAY['running'::"text", 'success'::"text", 'failed'::"text"]))),
    CONSTRAINT "pipeline_runs_top_count_check" CHECK (("top_count" >= 0))
);


ALTER TABLE "public"."pipeline_runs" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."pipeline_state" (
    "pipeline_key" "text" NOT NULL,
    "previous_top_url" "text",
    "last_success_at" timestamp with time zone,
    "last_run_id" "uuid",
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."pipeline_state" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."profile_audience_details" (
    "user_id" "uuid" NOT NULL,
    "audience" "text" NOT NULL,
    "details" "jsonb" DEFAULT '{}'::"jsonb" NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    CONSTRAINT "profile_audience_details_audience_check" CHECK (("audience" = ANY (ARRAY['student'::"text", 'alumni'::"text", 'guest'::"text", 'faculty'::"text"]))),
    CONSTRAINT "profile_audience_details_size_check" CHECK (("length"(("details")::"text") <= 8192))
);


ALTER TABLE "public"."profile_audience_details" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."profiles" (
    "id" "uuid" NOT NULL,
    "email" "text" NOT NULL,
    "full_name" "text",
    "role" "text" DEFAULT 'student'::"text" NOT NULL,
    "university_id" "text",
    "major" "text",
    "minor" "text",
    "department" "text",
    "graduation_year" integer,
    "phone" "text",
    "gpa" numeric(3,2),
    "class_standing" "text",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "active_audience" "text" DEFAULT 'student'::"text" NOT NULL,
    "onboarded_at" timestamp with time zone,
    CONSTRAINT "profiles_active_audience_check" CHECK (("active_audience" = ANY (ARRAY['student'::"text", 'alumni'::"text", 'guest'::"text", 'faculty'::"text"]))),
    CONSTRAINT "profiles_class_standing_check" CHECK (("class_standing" = ANY (ARRAY['Freshman'::"text", 'Sophomore'::"text", 'Junior'::"text", 'Senior'::"text", 'Graduate'::"text"]))),
    CONSTRAINT "profiles_gpa_check" CHECK ((("gpa" >= (0)::numeric) AND ("gpa" <= (4)::numeric))),
    CONSTRAINT "profiles_role_check" CHECK (("role" = ANY (ARRAY['student'::"text", 'advisor'::"text", 'admin'::"text"])))
);


ALTER TABLE "public"."profiles" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."project_summaries" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "project_id" "uuid" NOT NULL,
    "summary" "text" NOT NULL,
    "memory_count" integer DEFAULT 0 NOT NULL,
    "last_summarized_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."project_summaries" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."projects" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "name" "text" NOT NULL,
    "description" "text",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."projects" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."reg_class_sections" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "snapshot_id" "uuid" NOT NULL,
    "class_number" integer NOT NULL,
    "course_key" "text" NOT NULL,
    "section" "text",
    "mode" "text",
    "title" "text",
    "satisfies_raw" "text",
    "satisfies" "text"[] DEFAULT '{}'::"text"[] NOT NULL,
    "units_min" numeric,
    "units_max" numeric,
    "type" "text",
    "days" "text",
    "start_time" time without time zone,
    "end_time" time without time zone,
    "times_raw" "text",
    "meetings" "jsonb" DEFAULT '[]'::"jsonb" NOT NULL,
    "meeting_count" smallint DEFAULT 1 NOT NULL,
    "instructor" "text",
    "location" "text",
    "start_date" "date",
    "end_date" "date",
    "open_seats" integer,
    "notes" "text",
    "raw" "jsonb" DEFAULT '{}'::"jsonb" NOT NULL,
    CONSTRAINT "reg_class_sections_meetings_check" CHECK (
CASE
    WHEN ("jsonb_typeof"("meetings") = 'array'::"text") THEN (("jsonb_array_length"("meetings") = 0) OR ("jsonb_array_length"("meetings") = "meeting_count"))
    ELSE false
END)
);


ALTER TABLE "public"."reg_class_sections" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."reg_exam_rules" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "snapshot_id" "uuid" NOT NULL,
    "day_patterns" "text"[] DEFAULT '{}'::"text"[] NOT NULL,
    "start_time_from" time without time zone,
    "start_time_to" time without time zone,
    "exam_date" "date",
    "exam_start" time without time zone,
    "exam_end" time without time zone,
    "is_exception" boolean DEFAULT false NOT NULL,
    "course_keys" "text"[] DEFAULT '{}'::"text"[] NOT NULL,
    "note" "text",
    "raw" "jsonb" DEFAULT '{}'::"jsonb" NOT NULL
);


ALTER TABLE "public"."reg_exam_rules" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."reg_term_events" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "snapshot_id" "uuid" NOT NULL,
    "category" "text" NOT NULL,
    "label_raw" "text" NOT NULL,
    "date_raw" "text" NOT NULL,
    "start_date" "date",
    "end_date" "date",
    "event_key" "text",
    CONSTRAINT "reg_term_events_category_check" CHECK (("category" = ANY (ARRAY['registrar'::"text", 'academic'::"text", 'payment'::"text"]))),
    CONSTRAINT "reg_term_events_date_order_check" CHECK ((("end_date" IS NULL) OR (("start_date" IS NOT NULL) AND ("end_date" >= "start_date"))))
);


ALTER TABLE "public"."reg_term_events" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."saved_conversations" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "title" "text" DEFAULT 'New Conversation'::"text" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."saved_conversations" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."student_academic_records" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "major" "text",
    "minor" "text",
    "completed_credits" integer DEFAULT 0,
    "gpa" numeric(3,2),
    "class_standing" "text",
    "catalog_year" integer,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    CONSTRAINT "student_academic_records_class_standing_check" CHECK (("class_standing" = ANY (ARRAY['Freshman'::"text", 'Sophomore'::"text", 'Junior'::"text", 'Senior'::"text", 'Graduate'::"text"]))),
    CONSTRAINT "student_academic_records_gpa_check" CHECK ((("gpa" >= (0)::numeric) AND ("gpa" <= (4)::numeric)))
);


ALTER TABLE "public"."student_academic_records" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."uploaded_documents" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "owner_id" "uuid" NOT NULL,
    "file_name" "text" NOT NULL,
    "storage_path" "text" NOT NULL,
    "mime_type" "text",
    "size_bytes" bigint,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."uploaded_documents" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."user_affiliations" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "affiliation" "public"."affiliation_kind" NOT NULL,
    "status" "public"."verification_status" DEFAULT 'declared'::"public"."verification_status" NOT NULL,
    "source" "text",
    "verified_at" timestamp with time zone,
    "expires_at" timestamp with time zone,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."user_affiliations" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."user_job_applications" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "job_id" "uuid" NOT NULL,
    "status" "text" DEFAULT 'saved'::"text" NOT NULL,
    "applied_at" "date",
    "notes" "text",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    CONSTRAINT "user_job_applications_status_check" CHECK (("status" = ANY (ARRAY['saved'::"text", 'applied'::"text", 'interview'::"text", 'offer'::"text", 'rejected'::"text", 'withdrawn'::"text"])))
);


ALTER TABLE "public"."user_job_applications" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."user_saved_jobs" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "job_id" "uuid" NOT NULL,
    "notes" "text",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."user_saved_jobs" OWNER TO "postgres";


ALTER TABLE ONLY "public"."admin_grants"
    ADD CONSTRAINT "admin_grants_pkey" PRIMARY KEY ("user_id", "capability");



ALTER TABLE ONLY "public"."ats_registry"
    ADD CONSTRAINT "ats_registry_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."ats_registry"
    ADD CONSTRAINT "ats_registry_slug_ats_key" UNIQUE ("slug", "ats");



ALTER TABLE ONLY "public"."behavior_feedback_log"
    ADD CONSTRAINT "behavior_feedback_log_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."behavior_settings"
    ADD CONSTRAINT "behavior_settings_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."campus_current"
    ADD CONSTRAINT "campus_current_pkey" PRIMARY KEY ("source_key", "scope_key");



ALTER TABLE ONLY "public"."campus_current"
    ADD CONSTRAINT "campus_current_snapshot_id_key" UNIQUE ("snapshot_id");



ALTER TABLE ONLY "public"."campus_refresh_runs"
    ADD CONSTRAINT "campus_refresh_runs_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."campus_snapshots"
    ADD CONSTRAINT "campus_snapshots_id_source_scope_key" UNIQUE ("id", "source_key", "scope_key");



ALTER TABLE ONLY "public"."campus_snapshots"
    ADD CONSTRAINT "campus_snapshots_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."conversation_summaries"
    ADD CONSTRAINT "conversation_summaries_conversation_id_key" UNIQUE ("conversation_id");



ALTER TABLE ONLY "public"."conversation_summaries"
    ADD CONSTRAINT "conversation_summaries_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."conversations"
    ADD CONSTRAINT "conversations_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."document_chunks"
    ADD CONSTRAINT "document_chunks_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."documents"
    ADD CONSTRAINT "documents_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."job_fetch_runs"
    ADD CONSTRAINT "job_fetch_runs_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."job_listings"
    ADD CONSTRAINT "job_listings_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."job_sources"
    ADD CONSTRAINT "job_sources_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."jobs_snapshot"
    ADD CONSTRAINT "jobs_snapshot_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."jobs_snapshot"
    ADD CONSTRAINT "jobs_snapshot_run_id_job_url_key" UNIQUE ("run_id", "job_url");



ALTER TABLE ONLY "public"."jobs_snapshot"
    ADD CONSTRAINT "jobs_snapshot_run_id_rank_position_key" UNIQUE ("run_id", "rank_position");



ALTER TABLE ONLY "public"."kb_ingest_jobs"
    ADD CONSTRAINT "kb_ingest_jobs_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."kb_ingest_runs"
    ADD CONSTRAINT "kb_ingest_runs_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."kb_sources"
    ADD CONSTRAINT "kb_sources_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."kb_sources"
    ADD CONSTRAINT "kb_sources_url_key" UNIQUE ("url");



ALTER TABLE ONLY "public"."memories"
    ADD CONSTRAINT "memories_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."messages"
    ADD CONSTRAINT "messages_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."pipeline_runs"
    ADD CONSTRAINT "pipeline_runs_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."pipeline_state"
    ADD CONSTRAINT "pipeline_state_pkey" PRIMARY KEY ("pipeline_key");



ALTER TABLE ONLY "public"."profile_audience_details"
    ADD CONSTRAINT "profile_audience_details_pkey" PRIMARY KEY ("user_id", "audience");



ALTER TABLE ONLY "public"."profiles"
    ADD CONSTRAINT "profiles_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."project_summaries"
    ADD CONSTRAINT "project_summaries_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."project_summaries"
    ADD CONSTRAINT "project_summaries_project_id_key" UNIQUE ("project_id");



ALTER TABLE ONLY "public"."projects"
    ADD CONSTRAINT "projects_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."reg_class_sections"
    ADD CONSTRAINT "reg_class_sections_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."reg_exam_rules"
    ADD CONSTRAINT "reg_exam_rules_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."reg_term_events"
    ADD CONSTRAINT "reg_term_events_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."saved_conversations"
    ADD CONSTRAINT "saved_conversations_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."student_academic_records"
    ADD CONSTRAINT "student_academic_records_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."uploaded_documents"
    ADD CONSTRAINT "uploaded_documents_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."user_affiliations"
    ADD CONSTRAINT "uq_user_affiliations_user_affiliation" UNIQUE ("user_id", "affiliation");



ALTER TABLE ONLY "public"."user_affiliations"
    ADD CONSTRAINT "user_affiliations_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."user_job_applications"
    ADD CONSTRAINT "user_job_applications_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."user_job_applications"
    ADD CONSTRAINT "user_job_applications_user_id_job_id_key" UNIQUE ("user_id", "job_id");



ALTER TABLE ONLY "public"."user_saved_jobs"
    ADD CONSTRAINT "user_saved_jobs_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."user_saved_jobs"
    ADD CONSTRAINT "user_saved_jobs_user_id_job_id_key" UNIQUE ("user_id", "job_id");



CREATE INDEX "idx_ats_registry_active" ON "public"."ats_registry" USING "btree" ("ats") WHERE (("found" = true) AND ("enabled" = true));



CREATE INDEX "idx_behavior_settings_scope" ON "public"."behavior_settings" USING "btree" ("user_id", "project_id", "conversation_id");



CREATE INDEX "idx_behavior_settings_user" ON "public"."behavior_settings" USING "btree" ("user_id");



CREATE INDEX "idx_campus_refresh_runs_started_at" ON "public"."campus_refresh_runs" USING "btree" ("started_at" DESC);



CREATE INDEX "idx_campus_snapshots_source_scope_created" ON "public"."campus_snapshots" USING "btree" ("source_key", "scope_key", "created_at" DESC);



CREATE INDEX "idx_conversations_project" ON "public"."conversations" USING "btree" ("project_id") WHERE ("project_id" IS NOT NULL);



CREATE INDEX "idx_conversations_user_updated" ON "public"."conversations" USING "btree" ("user_id", "updated_at" DESC);



CREATE INDEX "idx_document_chunks_embedding_hnsw" ON "public"."document_chunks" USING "hnsw" ("embedding" "public"."vector_cosine_ops") WITH ("m"='16', "ef_construction"='64');



CREATE INDEX "idx_document_chunks_tsv" ON "public"."document_chunks" USING "gin" ("tsv");



CREATE INDEX "idx_documents_last_verified_at" ON "public"."documents" USING "btree" ("last_verified_at" DESC);



CREATE INDEX "idx_feedback_log_response" ON "public"."behavior_feedback_log" USING "btree" ("response_id");



CREATE INDEX "idx_feedback_log_user_conv" ON "public"."behavior_feedback_log" USING "btree" ("user_id", "conversation_id");



CREATE INDEX "idx_job_fetch_runs_started_at" ON "public"."job_fetch_runs" USING "btree" ("started_at" DESC);



CREATE UNIQUE INDEX "idx_job_listings_apply_url_unique" ON "public"."job_listings" USING "btree" ("apply_url");



CREATE UNIQUE INDEX "idx_job_listings_dedupe_hash_unique" ON "public"."job_listings" USING "btree" ("dedupe_hash");



CREATE INDEX "idx_job_listings_job_type" ON "public"."job_listings" USING "btree" ("job_type");



CREATE INDEX "idx_job_listings_posted_date" ON "public"."job_listings" USING "btree" ("posted_date" DESC NULLS LAST);



CREATE INDEX "idx_job_listings_remote_status" ON "public"."job_listings" USING "btree" ("remote_status");



CREATE INDEX "idx_job_listings_tags_gin" ON "public"."job_listings" USING "gin" ("tags");



CREATE INDEX "idx_jobs_snapshot_job_url" ON "public"."jobs_snapshot" USING "btree" ("job_url");



CREATE INDEX "idx_jobs_snapshot_run_rank" ON "public"."jobs_snapshot" USING "btree" ("run_id", "rank_position");



CREATE INDEX "idx_kb_ingest_jobs_claimable" ON "public"."kb_ingest_jobs" USING "btree" ("created_at") WHERE ("status" = ANY (ARRAY['pending'::"text", 'running'::"text"]));



CREATE INDEX "idx_kb_ingest_runs_started_at" ON "public"."kb_ingest_runs" USING "btree" ("started_at" DESC);



CREATE INDEX "idx_kb_sources_due" ON "public"."kb_sources" USING "btree" ("last_fetched_at" NULLS FIRST) WHERE "enabled";



CREATE INDEX "idx_memories_conversation" ON "public"."memories" USING "btree" ("conversation_id", "status", "importance" DESC) WHERE (("scope" = 'conversation'::"public"."memory_scope") AND ("status" = 'active'::"public"."memory_status"));



CREATE INDEX "idx_memories_project" ON "public"."memories" USING "btree" ("project_id", "status", "importance" DESC) WHERE (("scope" = 'project'::"public"."memory_scope") AND ("status" = 'active'::"public"."memory_status"));



CREATE INDEX "idx_memories_source_conversation" ON "public"."memories" USING "btree" ("source_conversation_id");



CREATE INDEX "idx_memories_user_global" ON "public"."memories" USING "btree" ("user_id", "status", "importance" DESC) WHERE (("scope" = 'global'::"public"."memory_scope") AND ("status" = 'active'::"public"."memory_status"));



CREATE INDEX "idx_messages_conversation_created" ON "public"."messages" USING "btree" ("conversation_id", "created_at" DESC);



CREATE INDEX "idx_pipeline_runs_pipeline_started" ON "public"."pipeline_runs" USING "btree" ("pipeline_key", "started_at" DESC);



CREATE INDEX "idx_pipeline_runs_status_started" ON "public"."pipeline_runs" USING "btree" ("status", "started_at" DESC);



CREATE INDEX "idx_projects_user" ON "public"."projects" USING "btree" ("user_id", "updated_at" DESC);



CREATE INDEX "idx_reg_class_sections_satisfies" ON "public"."reg_class_sections" USING "gin" ("satisfies");



CREATE INDEX "idx_reg_class_sections_snapshot_course" ON "public"."reg_class_sections" USING "btree" ("snapshot_id", "course_key", "section");



CREATE INDEX "idx_reg_exam_rules_snapshot" ON "public"."reg_exam_rules" USING "btree" ("snapshot_id");



CREATE INDEX "idx_reg_term_events_snapshot_event" ON "public"."reg_term_events" USING "btree" ("snapshot_id", "event_key");



CREATE INDEX "idx_user_affiliations_user" ON "public"."user_affiliations" USING "btree" ("user_id");



CREATE UNIQUE INDEX "uq_behavior_settings_scope_idx" ON "public"."behavior_settings" USING "btree" ("user_id", COALESCE("project_id", '00000000-0000-0000-0000-000000000000'::"uuid"), COALESCE("conversation_id", '00000000-0000-0000-0000-000000000000'::"uuid"));



CREATE UNIQUE INDEX "uq_document_chunks_doc_idx" ON "public"."document_chunks" USING "btree" ("document_id", "chunk_index");



CREATE UNIQUE INDEX "uq_documents_url" ON "public"."documents" USING "btree" ("url");



CREATE UNIQUE INDEX "uq_reg_class_sections_snapshot_class" ON "public"."reg_class_sections" USING "btree" ("snapshot_id", "class_number");



CREATE OR REPLACE TRIGGER "on_academic_records_updated" BEFORE UPDATE ON "public"."student_academic_records" FOR EACH ROW EXECUTE FUNCTION "public"."handle_updated_at"();



CREATE OR REPLACE TRIGGER "on_conversations_updated" BEFORE UPDATE ON "public"."saved_conversations" FOR EACH ROW EXECUTE FUNCTION "public"."handle_updated_at"();



CREATE OR REPLACE TRIGGER "on_job_sources_updated" BEFORE UPDATE ON "public"."job_sources" FOR EACH ROW EXECUTE FUNCTION "public"."handle_updated_at"();



CREATE OR REPLACE TRIGGER "on_kb_ingest_jobs_updated" BEFORE UPDATE ON "public"."kb_ingest_jobs" FOR EACH ROW EXECUTE FUNCTION "public"."handle_updated_at"();



CREATE OR REPLACE TRIGGER "on_profile_audience_details_updated" BEFORE UPDATE ON "public"."profile_audience_details" FOR EACH ROW EXECUTE FUNCTION "public"."handle_updated_at"();



CREATE OR REPLACE TRIGGER "on_profiles_updated" BEFORE UPDATE ON "public"."profiles" FOR EACH ROW EXECUTE FUNCTION "public"."handle_updated_at"();



CREATE OR REPLACE TRIGGER "on_user_job_applications_updated" BEFORE UPDATE ON "public"."user_job_applications" FOR EACH ROW EXECUTE FUNCTION "public"."handle_updated_at"();



CREATE OR REPLACE TRIGGER "trg_conversation_summaries_updated_at" BEFORE UPDATE ON "public"."conversation_summaries" FOR EACH ROW EXECUTE FUNCTION "public"."set_updated_at"();



CREATE OR REPLACE TRIGGER "trg_create_default_behavior_settings" AFTER INSERT ON "public"."profiles" FOR EACH ROW EXECUTE FUNCTION "public"."create_default_behavior_settings"();



CREATE OR REPLACE TRIGGER "trg_freeze_profile_privilege_columns" BEFORE UPDATE ON "public"."profiles" FOR EACH ROW EXECUTE FUNCTION "public"."freeze_profile_privilege_columns"();



CREATE OR REPLACE TRIGGER "trg_memories_updated_at" BEFORE UPDATE ON "public"."memories" FOR EACH ROW EXECUTE FUNCTION "public"."set_updated_at"();



CREATE OR REPLACE TRIGGER "trg_pipeline_state_set_updated_at" BEFORE UPDATE ON "public"."pipeline_state" FOR EACH ROW EXECUTE FUNCTION "public"."pipeline_state_set_updated_at"();



CREATE OR REPLACE TRIGGER "trg_project_summaries_updated_at" BEFORE UPDATE ON "public"."project_summaries" FOR EACH ROW EXECUTE FUNCTION "public"."set_updated_at"();



CREATE OR REPLACE TRIGGER "trg_projects_updated_at" BEFORE UPDATE ON "public"."projects" FOR EACH ROW EXECUTE FUNCTION "public"."set_updated_at"();



CREATE OR REPLACE TRIGGER "trg_set_conversation_preview" AFTER INSERT ON "public"."messages" FOR EACH ROW EXECUTE FUNCTION "public"."set_conversation_preview"();



ALTER TABLE ONLY "public"."admin_grants"
    ADD CONSTRAINT "admin_grants_granted_by_fkey" FOREIGN KEY ("granted_by") REFERENCES "auth"."users"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "public"."admin_grants"
    ADD CONSTRAINT "admin_grants_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."behavior_feedback_log"
    ADD CONSTRAINT "behavior_feedback_log_conversation_id_fkey" FOREIGN KEY ("conversation_id") REFERENCES "public"."conversations"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "public"."behavior_feedback_log"
    ADD CONSTRAINT "behavior_feedback_log_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."behavior_settings"
    ADD CONSTRAINT "behavior_settings_conversation_id_fkey" FOREIGN KEY ("conversation_id") REFERENCES "public"."conversations"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."behavior_settings"
    ADD CONSTRAINT "behavior_settings_project_id_fkey" FOREIGN KEY ("project_id") REFERENCES "public"."projects"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."behavior_settings"
    ADD CONSTRAINT "behavior_settings_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."campus_current"
    ADD CONSTRAINT "campus_current_snapshot_fkey" FOREIGN KEY ("snapshot_id", "source_key", "scope_key") REFERENCES "public"."campus_snapshots"("id", "source_key", "scope_key") ON DELETE RESTRICT;



ALTER TABLE ONLY "public"."conversation_summaries"
    ADD CONSTRAINT "conversation_summaries_conversation_id_fkey" FOREIGN KEY ("conversation_id") REFERENCES "public"."conversations"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."conversations"
    ADD CONSTRAINT "conversations_project_id_fkey" FOREIGN KEY ("project_id") REFERENCES "public"."projects"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "public"."conversations"
    ADD CONSTRAINT "conversations_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."document_chunks"
    ADD CONSTRAINT "document_chunks_document_id_fkey" FOREIGN KEY ("document_id") REFERENCES "public"."documents"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."job_fetch_runs"
    ADD CONSTRAINT "job_fetch_runs_source_id_fkey" FOREIGN KEY ("source_id") REFERENCES "public"."job_sources"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."job_sources"
    ADD CONSTRAINT "job_sources_created_by_fkey" FOREIGN KEY ("created_by") REFERENCES "public"."profiles"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "public"."jobs_snapshot"
    ADD CONSTRAINT "jobs_snapshot_run_id_fkey" FOREIGN KEY ("run_id") REFERENCES "public"."pipeline_runs"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."kb_ingest_jobs"
    ADD CONSTRAINT "kb_ingest_jobs_source_id_fkey" FOREIGN KEY ("source_id") REFERENCES "public"."kb_sources"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."kb_ingest_runs"
    ADD CONSTRAINT "kb_ingest_runs_source_id_fkey" FOREIGN KEY ("source_id") REFERENCES "public"."kb_sources"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "public"."memories"
    ADD CONSTRAINT "memories_conversation_id_fkey" FOREIGN KEY ("conversation_id") REFERENCES "public"."conversations"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."memories"
    ADD CONSTRAINT "memories_project_id_fkey" FOREIGN KEY ("project_id") REFERENCES "public"."projects"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."memories"
    ADD CONSTRAINT "memories_source_conversation_id_fkey" FOREIGN KEY ("source_conversation_id") REFERENCES "public"."conversations"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "public"."memories"
    ADD CONSTRAINT "memories_source_message_id_fkey" FOREIGN KEY ("source_message_id") REFERENCES "public"."messages"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "public"."memories"
    ADD CONSTRAINT "memories_superseded_by_fkey" FOREIGN KEY ("superseded_by") REFERENCES "public"."memories"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "public"."memories"
    ADD CONSTRAINT "memories_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."messages"
    ADD CONSTRAINT "messages_conversation_id_fkey" FOREIGN KEY ("conversation_id") REFERENCES "public"."conversations"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."pipeline_state"
    ADD CONSTRAINT "pipeline_state_last_run_id_fkey" FOREIGN KEY ("last_run_id") REFERENCES "public"."pipeline_runs"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "public"."profile_audience_details"
    ADD CONSTRAINT "profile_audience_details_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."profiles"
    ADD CONSTRAINT "profiles_id_fkey" FOREIGN KEY ("id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."project_summaries"
    ADD CONSTRAINT "project_summaries_project_id_fkey" FOREIGN KEY ("project_id") REFERENCES "public"."projects"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."projects"
    ADD CONSTRAINT "projects_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."reg_class_sections"
    ADD CONSTRAINT "reg_class_sections_snapshot_id_fkey" FOREIGN KEY ("snapshot_id") REFERENCES "public"."campus_snapshots"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."reg_exam_rules"
    ADD CONSTRAINT "reg_exam_rules_snapshot_id_fkey" FOREIGN KEY ("snapshot_id") REFERENCES "public"."campus_snapshots"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."reg_term_events"
    ADD CONSTRAINT "reg_term_events_snapshot_id_fkey" FOREIGN KEY ("snapshot_id") REFERENCES "public"."campus_snapshots"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."saved_conversations"
    ADD CONSTRAINT "saved_conversations_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "public"."profiles"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."student_academic_records"
    ADD CONSTRAINT "student_academic_records_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "public"."profiles"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."uploaded_documents"
    ADD CONSTRAINT "uploaded_documents_owner_id_fkey" FOREIGN KEY ("owner_id") REFERENCES "public"."profiles"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."user_affiliations"
    ADD CONSTRAINT "user_affiliations_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."user_job_applications"
    ADD CONSTRAINT "user_job_applications_job_id_fkey" FOREIGN KEY ("job_id") REFERENCES "public"."job_listings"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."user_job_applications"
    ADD CONSTRAINT "user_job_applications_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "public"."profiles"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."user_saved_jobs"
    ADD CONSTRAINT "user_saved_jobs_job_id_fkey" FOREIGN KEY ("job_id") REFERENCES "public"."job_listings"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."user_saved_jobs"
    ADD CONSTRAINT "user_saved_jobs_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "public"."profiles"("id") ON DELETE CASCADE;



CREATE POLICY "Authenticated users can read fetch logs" ON "public"."job_fetch_runs" FOR SELECT USING (("auth"."role"() = 'authenticated'::"text"));



CREATE POLICY "Authenticated users can read job sources" ON "public"."job_sources" FOR SELECT USING (("auth"."role"() = 'authenticated'::"text"));



CREATE POLICY "Authenticated users can read jobs" ON "public"."job_listings" FOR SELECT USING (("auth"."role"() = 'authenticated'::"text"));



CREATE POLICY "Public document chunks are readable by anyone" ON "public"."document_chunks" FOR SELECT TO "authenticated", "anon" USING ((EXISTS ( SELECT 1
   FROM "public"."documents" "d"
  WHERE (("d"."id" = "document_chunks"."document_id") AND ("d"."visibility" = 'public'::"text")))));



CREATE POLICY "Public documents are readable by anyone" ON "public"."documents" FOR SELECT TO "authenticated", "anon" USING (("visibility" = 'public'::"text"));



CREATE POLICY "Signed-in users also read authenticated document chunks" ON "public"."document_chunks" FOR SELECT TO "authenticated" USING ((EXISTS ( SELECT 1
   FROM "public"."documents" "d"
  WHERE (("d"."id" = "document_chunks"."document_id") AND ("d"."visibility" = ANY (ARRAY['public'::"text", 'authenticated'::"text"]))))));



CREATE POLICY "Signed-in users also read authenticated documents" ON "public"."documents" FOR SELECT TO "authenticated" USING (("visibility" = ANY (ARRAY['public'::"text", 'authenticated'::"text"])));



CREATE POLICY "Users can delete own conversations" ON "public"."saved_conversations" FOR DELETE USING (("auth"."uid"() = "user_id"));



CREATE POLICY "Users can delete own uploads" ON "public"."uploaded_documents" FOR DELETE USING (("auth"."uid"() = "owner_id"));



CREATE POLICY "Users can insert own academic records" ON "public"."student_academic_records" FOR INSERT WITH CHECK (("auth"."uid"() = "user_id"));



CREATE POLICY "Users can insert own behavior settings" ON "public"."behavior_settings" FOR INSERT WITH CHECK (("auth"."uid"() = "user_id"));



CREATE POLICY "Users can insert own conversations" ON "public"."saved_conversations" FOR INSERT WITH CHECK (("auth"."uid"() = "user_id"));



CREATE POLICY "Users can insert own feedback logs" ON "public"."behavior_feedback_log" FOR INSERT WITH CHECK (("auth"."uid"() = "user_id"));



CREATE POLICY "Users can insert own profile" ON "public"."profiles" FOR INSERT WITH CHECK (("auth"."uid"() = "id"));



CREATE POLICY "Users can insert own uploads" ON "public"."uploaded_documents" FOR INSERT WITH CHECK (("auth"."uid"() = "owner_id"));



CREATE POLICY "Users can manage own applications" ON "public"."user_job_applications" USING (("auth"."uid"() = "user_id")) WITH CHECK (("auth"."uid"() = "user_id"));



CREATE POLICY "Users can manage own saved jobs" ON "public"."user_saved_jobs" USING (("auth"."uid"() = "user_id")) WITH CHECK (("auth"."uid"() = "user_id"));



CREATE POLICY "Users can read own applications" ON "public"."user_job_applications" FOR SELECT USING (("auth"."uid"() = "user_id"));



CREATE POLICY "Users can read own behavior settings" ON "public"."behavior_settings" FOR SELECT USING (("auth"."uid"() = "user_id"));



CREATE POLICY "Users can read own feedback logs" ON "public"."behavior_feedback_log" FOR SELECT USING (("auth"."uid"() = "user_id"));



CREATE POLICY "Users can read own saved jobs" ON "public"."user_saved_jobs" FOR SELECT USING (("auth"."uid"() = "user_id"));



CREATE POLICY "Users can update own academic records" ON "public"."student_academic_records" FOR UPDATE USING (("auth"."uid"() = "user_id")) WITH CHECK (("auth"."uid"() = "user_id"));



CREATE POLICY "Users can update own behavior settings" ON "public"."behavior_settings" FOR UPDATE USING (("auth"."uid"() = "user_id"));



CREATE POLICY "Users can update own conversations" ON "public"."saved_conversations" FOR UPDATE USING (("auth"."uid"() = "user_id")) WITH CHECK (("auth"."uid"() = "user_id"));



CREATE POLICY "Users can update own feedback logs" ON "public"."behavior_feedback_log" FOR UPDATE USING (("auth"."uid"() = "user_id")) WITH CHECK (("auth"."uid"() = "user_id"));



CREATE POLICY "Users can update own profile" ON "public"."profiles" FOR UPDATE USING (("auth"."uid"() = "id")) WITH CHECK (("auth"."uid"() = "id"));



CREATE POLICY "Users can view own academic records" ON "public"."student_academic_records" FOR SELECT USING (("auth"."uid"() = "user_id"));



CREATE POLICY "Users can view own conversations" ON "public"."saved_conversations" FOR SELECT USING (("auth"."uid"() = "user_id"));



CREATE POLICY "Users can view own profile" ON "public"."profiles" FOR SELECT USING (("auth"."uid"() = "id"));



CREATE POLICY "Users can view own uploads" ON "public"."uploaded_documents" FOR SELECT USING (("auth"."uid"() = "owner_id"));



ALTER TABLE "public"."admin_grants" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "admin_grants_select_own" ON "public"."admin_grants" FOR SELECT TO "authenticated" USING (("user_id" = "auth"."uid"()));



ALTER TABLE "public"."ats_registry" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "ats_registry_select_authenticated" ON "public"."ats_registry" FOR SELECT TO "authenticated" USING (true);



ALTER TABLE "public"."behavior_feedback_log" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."behavior_settings" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."campus_current" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."campus_refresh_runs" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."campus_snapshots" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."conversation_summaries" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."conversations" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."document_chunks" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."documents" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."job_fetch_runs" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."job_listings" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."job_sources" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."jobs_snapshot" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "jobs_snapshot_select_authenticated" ON "public"."jobs_snapshot" FOR SELECT TO "authenticated" USING (true);



CREATE POLICY "jobs_snapshot_service_write" ON "public"."jobs_snapshot" TO "service_role" USING (("auth"."role"() = 'service_role'::"text")) WITH CHECK (("auth"."role"() = 'service_role'::"text"));



ALTER TABLE "public"."kb_ingest_jobs" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."kb_ingest_runs" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."kb_sources" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."memories" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."messages" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."pipeline_runs" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "pipeline_runs_select_authenticated" ON "public"."pipeline_runs" FOR SELECT TO "authenticated" USING (true);



CREATE POLICY "pipeline_runs_service_write" ON "public"."pipeline_runs" TO "service_role" USING (("auth"."role"() = 'service_role'::"text")) WITH CHECK (("auth"."role"() = 'service_role'::"text"));



ALTER TABLE "public"."pipeline_state" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "pipeline_state_select_authenticated" ON "public"."pipeline_state" FOR SELECT TO "authenticated" USING (true);



CREATE POLICY "pipeline_state_service_write" ON "public"."pipeline_state" TO "service_role" USING (("auth"."role"() = 'service_role'::"text")) WITH CHECK (("auth"."role"() = 'service_role'::"text"));



ALTER TABLE "public"."profile_audience_details" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "profile_audience_details_delete_own" ON "public"."profile_audience_details" FOR DELETE TO "authenticated" USING (("user_id" = "auth"."uid"()));



CREATE POLICY "profile_audience_details_insert_own" ON "public"."profile_audience_details" FOR INSERT TO "authenticated" WITH CHECK (("user_id" = "auth"."uid"()));



CREATE POLICY "profile_audience_details_select_own" ON "public"."profile_audience_details" FOR SELECT TO "authenticated" USING (("user_id" = "auth"."uid"()));



CREATE POLICY "profile_audience_details_update_own" ON "public"."profile_audience_details" FOR UPDATE TO "authenticated" USING (("user_id" = "auth"."uid"())) WITH CHECK (("user_id" = "auth"."uid"()));



ALTER TABLE "public"."profiles" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."project_summaries" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."projects" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."reg_class_sections" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."reg_exam_rules" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."reg_term_events" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."saved_conversations" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."student_academic_records" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."uploaded_documents" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."user_affiliations" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "user_affiliations_delete_own_declared" ON "public"."user_affiliations" FOR DELETE TO "authenticated" USING ((("user_id" = "auth"."uid"()) AND ("status" = 'declared'::"public"."verification_status")));



CREATE POLICY "user_affiliations_insert_own_declared" ON "public"."user_affiliations" FOR INSERT TO "authenticated" WITH CHECK ((("user_id" = "auth"."uid"()) AND ("status" = 'declared'::"public"."verification_status")));



CREATE POLICY "user_affiliations_select_own" ON "public"."user_affiliations" FOR SELECT TO "authenticated" USING (("user_id" = "auth"."uid"()));



CREATE POLICY "user_affiliations_update_own_declared" ON "public"."user_affiliations" FOR UPDATE TO "authenticated" USING ((("user_id" = "auth"."uid"()) AND ("status" = 'declared'::"public"."verification_status"))) WITH CHECK ((("user_id" = "auth"."uid"()) AND ("status" = 'declared'::"public"."verification_status")));



ALTER TABLE "public"."user_job_applications" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."user_saved_jobs" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "users can delete messages in own conversations" ON "public"."messages" FOR DELETE USING ((EXISTS ( SELECT 1
   FROM "public"."conversations" "c"
  WHERE (("c"."id" = "messages"."conversation_id") AND ("c"."user_id" = "auth"."uid"())))));



CREATE POLICY "users can delete own conversations" ON "public"."conversations" FOR DELETE USING (("user_id" = "auth"."uid"()));



CREATE POLICY "users can delete own memories" ON "public"."memories" FOR DELETE USING (("user_id" = "auth"."uid"()));



CREATE POLICY "users can insert messages in own conversations" ON "public"."messages" FOR INSERT WITH CHECK ((EXISTS ( SELECT 1
   FROM "public"."conversations" "c"
  WHERE (("c"."id" = "messages"."conversation_id") AND ("c"."user_id" = "auth"."uid"())))));



CREATE POLICY "users can insert own conversations" ON "public"."conversations" FOR INSERT WITH CHECK (("user_id" = "auth"."uid"()));



CREATE POLICY "users can insert own memories" ON "public"."memories" FOR INSERT WITH CHECK (("user_id" = "auth"."uid"()));



CREATE POLICY "users can manage own conversation summaries" ON "public"."conversation_summaries" USING ((EXISTS ( SELECT 1
   FROM "public"."conversations" "c"
  WHERE (("c"."id" = "conversation_summaries"."conversation_id") AND ("c"."user_id" = "auth"."uid"()))))) WITH CHECK ((EXISTS ( SELECT 1
   FROM "public"."conversations" "c"
  WHERE (("c"."id" = "conversation_summaries"."conversation_id") AND ("c"."user_id" = "auth"."uid"())))));



CREATE POLICY "users can manage own project summaries" ON "public"."project_summaries" USING ((EXISTS ( SELECT 1
   FROM "public"."projects" "p"
  WHERE (("p"."id" = "project_summaries"."project_id") AND ("p"."user_id" = "auth"."uid"()))))) WITH CHECK ((EXISTS ( SELECT 1
   FROM "public"."projects" "p"
  WHERE (("p"."id" = "project_summaries"."project_id") AND ("p"."user_id" = "auth"."uid"())))));



CREATE POLICY "users can manage own projects" ON "public"."projects" USING (("user_id" = "auth"."uid"())) WITH CHECK (("user_id" = "auth"."uid"()));



CREATE POLICY "users can update own conversations" ON "public"."conversations" FOR UPDATE USING (("user_id" = "auth"."uid"())) WITH CHECK (("user_id" = "auth"."uid"()));



CREATE POLICY "users can update own memories" ON "public"."memories" FOR UPDATE USING (("user_id" = "auth"."uid"())) WITH CHECK (("user_id" = "auth"."uid"()));



CREATE POLICY "users can view messages in own conversations" ON "public"."messages" FOR SELECT USING ((EXISTS ( SELECT 1
   FROM "public"."conversations" "c"
  WHERE (("c"."id" = "messages"."conversation_id") AND ("c"."user_id" = "auth"."uid"())))));



CREATE POLICY "users can view own conversations" ON "public"."conversations" FOR SELECT USING (("user_id" = "auth"."uid"()));



CREATE POLICY "users can view own memories" ON "public"."memories" FOR SELECT USING (("user_id" = "auth"."uid"()));



GRANT USAGE ON SCHEMA "public" TO "postgres";
GRANT USAGE ON SCHEMA "public" TO "anon";
GRANT USAGE ON SCHEMA "public" TO "authenticated";
GRANT USAGE ON SCHEMA "public" TO "service_role";



REVOKE ALL ON FUNCTION "public"."archive_stale_memories"("p_conversation_age_days" integer, "p_min_importance" integer) FROM PUBLIC;
GRANT ALL ON FUNCTION "public"."archive_stale_memories"("p_conversation_age_days" integer, "p_min_importance" integer) TO "service_role";



REVOKE ALL ON FUNCTION "public"."create_default_behavior_settings"() FROM PUBLIC;
GRANT ALL ON FUNCTION "public"."create_default_behavior_settings"() TO "service_role";



REVOKE ALL ON FUNCTION "public"."enforce_sjsu_email"() FROM PUBLIC;
GRANT ALL ON FUNCTION "public"."enforce_sjsu_email"() TO "service_role";



REVOKE ALL ON FUNCTION "public"."freeze_profile_privilege_columns"() FROM PUBLIC;
GRANT ALL ON FUNCTION "public"."freeze_profile_privilege_columns"() TO "service_role";



REVOKE ALL ON FUNCTION "public"."generate_job_dedupe_hash"("input_title" "text", "input_company" "text") FROM PUBLIC;
GRANT ALL ON FUNCTION "public"."generate_job_dedupe_hash"("input_title" "text", "input_company" "text") TO "service_role";



REVOKE ALL ON FUNCTION "public"."get_memory_context"("p_user_id" "uuid", "p_conversation_id" "uuid", "p_project_id" "uuid", "p_max_global" integer, "p_max_project" integer, "p_max_conversation" integer) FROM PUBLIC;
GRANT ALL ON FUNCTION "public"."get_memory_context"("p_user_id" "uuid", "p_conversation_id" "uuid", "p_project_id" "uuid", "p_max_global" integer, "p_max_project" integer, "p_max_conversation" integer) TO "service_role";
GRANT ALL ON FUNCTION "public"."get_memory_context"("p_user_id" "uuid", "p_conversation_id" "uuid", "p_project_id" "uuid", "p_max_global" integer, "p_max_project" integer, "p_max_conversation" integer) TO "authenticated";



REVOKE ALL ON FUNCTION "public"."handle_new_user"() FROM PUBLIC;
GRANT ALL ON FUNCTION "public"."handle_new_user"() TO "service_role";



REVOKE ALL ON FUNCTION "public"."handle_updated_at"() FROM PUBLIC;
GRANT ALL ON FUNCTION "public"."handle_updated_at"() TO "service_role";



REVOKE ALL ON FUNCTION "public"."has_grant"("p_capability" "text") FROM PUBLIC;
GRANT ALL ON FUNCTION "public"."has_grant"("p_capability" "text") TO "service_role";
GRANT ALL ON FUNCTION "public"."has_grant"("p_capability" "text") TO "authenticated";



REVOKE ALL ON FUNCTION "public"."match_documents"("query_embedding" "public"."vector", "match_count" integer, "match_threshold" double precision) FROM PUBLIC;
GRANT ALL ON FUNCTION "public"."match_documents"("query_embedding" "public"."vector", "match_count" integer, "match_threshold" double precision) TO "service_role";



REVOKE ALL ON FUNCTION "public"."match_kb_hybrid"("query_text" "text", "query_embedding" "public"."vector", "p_audience" "text", "p_include_authenticated" boolean, "match_count" integer, "rrf_k" integer) FROM PUBLIC;
GRANT ALL ON FUNCTION "public"."match_kb_hybrid"("query_text" "text", "query_embedding" "public"."vector", "p_audience" "text", "p_include_authenticated" boolean, "match_count" integer, "rrf_k" integer) TO "service_role";



REVOKE ALL ON FUNCTION "public"."pipeline_state_set_updated_at"() FROM PUBLIC;
GRANT ALL ON FUNCTION "public"."pipeline_state_set_updated_at"() TO "service_role";



REVOKE ALL ON FUNCTION "public"."promote_memory_to_project"("p_memory_id" "uuid", "p_project_id" "uuid") FROM PUBLIC;
GRANT ALL ON FUNCTION "public"."promote_memory_to_project"("p_memory_id" "uuid", "p_project_id" "uuid") TO "service_role";
GRANT ALL ON FUNCTION "public"."promote_memory_to_project"("p_memory_id" "uuid", "p_project_id" "uuid") TO "authenticated";



REVOKE ALL ON FUNCTION "public"."replace_document_chunks"("p_document_id" "uuid", "p_chunks" "jsonb") FROM PUBLIC;
GRANT ALL ON FUNCTION "public"."replace_document_chunks"("p_document_id" "uuid", "p_chunks" "jsonb") TO "service_role";



REVOKE ALL ON FUNCTION "public"."search_documents_fts"("query_text" "text", "match_count" integer) FROM PUBLIC;
GRANT ALL ON FUNCTION "public"."search_documents_fts"("query_text" "text", "match_count" integer) TO "service_role";



REVOKE ALL ON FUNCTION "public"."search_kb_chunks"("query_text" "text", "p_audience" "text", "p_include_authenticated" boolean, "match_count" integer) FROM PUBLIC;
GRANT ALL ON FUNCTION "public"."search_kb_chunks"("query_text" "text", "p_audience" "text", "p_include_authenticated" boolean, "match_count" integer) TO "service_role";



REVOKE ALL ON FUNCTION "public"."set_conversation_preview"() FROM PUBLIC;
GRANT ALL ON FUNCTION "public"."set_conversation_preview"() TO "service_role";



REVOKE ALL ON FUNCTION "public"."set_updated_at"() FROM PUBLIC;
GRANT ALL ON FUNCTION "public"."set_updated_at"() TO "service_role";



REVOKE ALL ON FUNCTION "public"."update_pipeline_marker_if_expected"("p_pipeline_key" "text", "p_expected_previous_top_url" "text", "p_new_previous_top_url" "text", "p_last_run_id" "uuid") FROM PUBLIC;
GRANT ALL ON FUNCTION "public"."update_pipeline_marker_if_expected"("p_pipeline_key" "text", "p_expected_previous_top_url" "text", "p_new_previous_top_url" "text", "p_last_run_id" "uuid") TO "service_role";



GRANT ALL ON TABLE "public"."admin_grants" TO "service_role";
GRANT SELECT ON TABLE "public"."admin_grants" TO "authenticated";



GRANT ALL ON TABLE "public"."ats_registry" TO "service_role";
GRANT SELECT ON TABLE "public"."ats_registry" TO "authenticated";



GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."behavior_feedback_log" TO "anon";
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."behavior_feedback_log" TO "authenticated";
GRANT ALL ON TABLE "public"."behavior_feedback_log" TO "service_role";



GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."behavior_settings" TO "anon";
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."behavior_settings" TO "authenticated";
GRANT ALL ON TABLE "public"."behavior_settings" TO "service_role";



GRANT ALL ON TABLE "public"."campus_current" TO "service_role";



GRANT ALL ON TABLE "public"."campus_refresh_runs" TO "service_role";



GRANT ALL ON TABLE "public"."campus_snapshots" TO "service_role";



GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."conversation_summaries" TO "anon";
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."conversation_summaries" TO "authenticated";
GRANT ALL ON TABLE "public"."conversation_summaries" TO "service_role";



GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."conversations" TO "anon";
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."conversations" TO "authenticated";
GRANT ALL ON TABLE "public"."conversations" TO "service_role";



GRANT SELECT ON TABLE "public"."document_chunks" TO "anon";
GRANT SELECT ON TABLE "public"."document_chunks" TO "authenticated";
GRANT ALL ON TABLE "public"."document_chunks" TO "service_role";



GRANT SELECT ON TABLE "public"."documents" TO "anon";
GRANT SELECT ON TABLE "public"."documents" TO "authenticated";
GRANT ALL ON TABLE "public"."documents" TO "service_role";



GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."job_fetch_runs" TO "anon";
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."job_fetch_runs" TO "authenticated";
GRANT ALL ON TABLE "public"."job_fetch_runs" TO "service_role";



GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."job_listings" TO "anon";
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."job_listings" TO "authenticated";
GRANT ALL ON TABLE "public"."job_listings" TO "service_role";



GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."job_sources" TO "anon";
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."job_sources" TO "authenticated";
GRANT ALL ON TABLE "public"."job_sources" TO "service_role";



GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."jobs_snapshot" TO "anon";
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."jobs_snapshot" TO "authenticated";
GRANT ALL ON TABLE "public"."jobs_snapshot" TO "service_role";



GRANT SELECT,USAGE ON SEQUENCE "public"."jobs_snapshot_id_seq" TO "anon";
GRANT SELECT,USAGE ON SEQUENCE "public"."jobs_snapshot_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."jobs_snapshot_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."kb_ingest_jobs" TO "service_role";



GRANT ALL ON TABLE "public"."kb_ingest_runs" TO "service_role";



GRANT ALL ON TABLE "public"."kb_sources" TO "service_role";



GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."memories" TO "anon";
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."memories" TO "authenticated";
GRANT ALL ON TABLE "public"."memories" TO "service_role";



GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."messages" TO "anon";
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."messages" TO "authenticated";
GRANT ALL ON TABLE "public"."messages" TO "service_role";



GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."pipeline_runs" TO "anon";
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."pipeline_runs" TO "authenticated";
GRANT ALL ON TABLE "public"."pipeline_runs" TO "service_role";



GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."pipeline_state" TO "anon";
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."pipeline_state" TO "authenticated";
GRANT ALL ON TABLE "public"."pipeline_state" TO "service_role";



GRANT ALL ON TABLE "public"."profile_audience_details" TO "service_role";
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."profile_audience_details" TO "authenticated";



GRANT SELECT ON TABLE "public"."profiles" TO "anon";
GRANT SELECT ON TABLE "public"."profiles" TO "authenticated";
GRANT ALL ON TABLE "public"."profiles" TO "service_role";



GRANT INSERT("id") ON TABLE "public"."profiles" TO "authenticated";



GRANT INSERT("email") ON TABLE "public"."profiles" TO "authenticated";



GRANT INSERT("full_name"),UPDATE("full_name") ON TABLE "public"."profiles" TO "authenticated";



GRANT UPDATE("university_id") ON TABLE "public"."profiles" TO "authenticated";



GRANT UPDATE("major") ON TABLE "public"."profiles" TO "authenticated";



GRANT UPDATE("minor") ON TABLE "public"."profiles" TO "authenticated";



GRANT UPDATE("graduation_year") ON TABLE "public"."profiles" TO "authenticated";



GRANT UPDATE("phone") ON TABLE "public"."profiles" TO "authenticated";



GRANT UPDATE("gpa") ON TABLE "public"."profiles" TO "authenticated";



GRANT UPDATE("class_standing") ON TABLE "public"."profiles" TO "authenticated";



GRANT UPDATE("active_audience") ON TABLE "public"."profiles" TO "authenticated";



GRANT UPDATE("onboarded_at") ON TABLE "public"."profiles" TO "authenticated";



GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."project_summaries" TO "anon";
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."project_summaries" TO "authenticated";
GRANT ALL ON TABLE "public"."project_summaries" TO "service_role";



GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."projects" TO "anon";
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."projects" TO "authenticated";
GRANT ALL ON TABLE "public"."projects" TO "service_role";



GRANT ALL ON TABLE "public"."reg_class_sections" TO "service_role";



GRANT ALL ON TABLE "public"."reg_exam_rules" TO "service_role";



GRANT ALL ON TABLE "public"."reg_term_events" TO "service_role";



GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."saved_conversations" TO "anon";
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."saved_conversations" TO "authenticated";
GRANT ALL ON TABLE "public"."saved_conversations" TO "service_role";



GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."student_academic_records" TO "anon";
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."student_academic_records" TO "authenticated";
GRANT ALL ON TABLE "public"."student_academic_records" TO "service_role";



GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."uploaded_documents" TO "anon";
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."uploaded_documents" TO "authenticated";
GRANT ALL ON TABLE "public"."uploaded_documents" TO "service_role";



GRANT ALL ON TABLE "public"."user_affiliations" TO "service_role";
GRANT SELECT,DELETE ON TABLE "public"."user_affiliations" TO "authenticated";



GRANT INSERT("user_id") ON TABLE "public"."user_affiliations" TO "authenticated";



GRANT INSERT("affiliation"),UPDATE("affiliation") ON TABLE "public"."user_affiliations" TO "authenticated";



GRANT INSERT("source"),UPDATE("source") ON TABLE "public"."user_affiliations" TO "authenticated";



GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."user_job_applications" TO "anon";
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."user_job_applications" TO "authenticated";
GRANT ALL ON TABLE "public"."user_job_applications" TO "service_role";



GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."user_saved_jobs" TO "anon";
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE "public"."user_saved_jobs" TO "authenticated";
GRANT ALL ON TABLE "public"."user_saved_jobs" TO "service_role";



ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT SELECT,USAGE ON SEQUENCES TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT SELECT,USAGE ON SEQUENCES TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "service_role";






ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "service_role";






ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT SELECT,INSERT,DELETE,UPDATE ON TABLES TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT SELECT,INSERT,DELETE,UPDATE ON TABLES TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "service_role";







