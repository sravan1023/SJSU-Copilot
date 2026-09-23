-- Remove the privileges RLS does not govern, and stop handing them out by default.
--
-- Supabase's default posture for the public schema is `GRANT ALL` on every
-- table, sequence and function to anon and authenticated, with row level
-- security as the gate. That works for SELECT/INSERT/UPDATE/DELETE, which RLS
-- filters. It does not work for the rest of what ALL contains:
--
--   * TRUNCATE is **not subject to RLS**. A role holding it can empty a table
--     regardless of any policy on it.
--   * TRIGGER allows CREATE TRIGGER on the table, which needs no CREATE
--     privilege on the schema.
--   * REFERENCES allows foreign keys against the table.
--   * MAINTAIN (PostgreSQL 17) allows VACUUM / ANALYZE / REINDEX / CLUSTER.
--
-- None of these is reachable today: PostgREST exposes no verb for any of them,
-- anon and authenticated hold USAGE but **not CREATE** on schema public, and no
-- function in this schema contains a TRUNCATE. This is defence in depth against
-- a future function or a direct connection, not an incident.
--
-- The function grants are the sharper half, and they are reachable. The rule
--     ALTER DEFAULT PRIVILEGES ... GRANT ALL ON FUNCTIONS TO anon, authenticated
-- means every function created in public is executable by the browser's anon key
-- unless someone remembers to revoke it. Functions are not governed by RLS at
-- all -- only the tables they touch are -- so a SECURITY DEFINER function added
-- later is exposed to the internet by default. 20260916000200 caught this for
-- update_pipeline_marker_if_expected and 20260917000200 for has_grant, both by
-- hand. This migration makes the default fail closed instead.
--
-- Probed against live with the real anon key before writing: anon could execute
-- get_memory_context, promote_memory_to_project, archive_stale_memories,
-- match_documents, search_documents_fts and generate_job_dedupe_hash. No data
-- leaked -- all are SECURITY INVOKER, so RLS scoped them to nothing -- but RLS
-- was the only wall.

-- 1. Tables: drop the four privileges RLS cannot filter -----------------------

-- Surgical on purpose. Revoking ALL and granting CRUD back would touch the
-- privileges every RLS policy in the schema depends on, across 26 tables, to
-- fix something none of them govern. This leaves SELECT/INSERT/UPDATE/DELETE
-- exactly as they are.
--
-- MAINTAIN requires PostgreSQL 17. Live is 17.6 and the verification harness
-- pins supabase/postgres:17.6.1.084, so both are fine; a replay on an older
-- server would need it removed.
revoke truncate, references, trigger, maintain on all tables in schema public from anon;
revoke truncate, references, trigger, maintain on all tables in schema public from authenticated;

-- 2. Functions: deny by default, grant by name --------------------------------

-- **Revoked from `public` first, and by name.** Two traps here, both found by
-- running this:
--
-- 1. CREATE FUNCTION grants EXECUTE to PUBLIC automatically, so anon holds it
--    by inheritance rather than by a direct grant. `REVOKE ... FROM anon` alone
--    changes nothing and the function stays callable -- the first version of
--    this migration did exactly that, and verify_policies section 20 caught it.
--    20260916000200 and 20260917000200 already knew this; both revoke from
--    public before anon.
--
-- 2. `ON ALL FUNCTIONS IN SCHEMA public` cannot be used, because pgvector lives
--    in this schema. It would strip EXECUTE from ~100 operator, type-I/O and
--    support functions (vector_in, halfvec_cmp, hnswhandler ...), and operators
--    check EXECUTE on the function behind them, so the vector type would break
--    for everyone. Application functions are listed by signature instead.
--
-- service_role is untouched: it holds its own per-function grants, not PUBLIC's.

revoke all on function public.archive_stale_memories(integer, integer) from public, anon, authenticated;
revoke all on function public.create_default_behavior_settings() from public, anon, authenticated;
revoke all on function public.enforce_sjsu_email() from public, anon, authenticated;
revoke all on function public.freeze_profile_privilege_columns() from public, anon, authenticated;
revoke all on function public.generate_job_dedupe_hash(text, text) from public, anon, authenticated;
revoke all on function public.get_memory_context(uuid, uuid, uuid, integer, integer, integer) from public, anon, authenticated;
revoke all on function public.handle_new_user() from public, anon, authenticated;
revoke all on function public.handle_updated_at() from public, anon, authenticated;
revoke all on function public.has_grant(text) from public, anon, authenticated;
revoke all on function public.match_documents(public.vector, integer, double precision) from public, anon, authenticated;
revoke all on function public.pipeline_state_set_updated_at() from public, anon, authenticated;
revoke all on function public.promote_memory_to_project(uuid, uuid) from public, anon, authenticated;
revoke all on function public.search_documents_fts(text, integer) from public, anon, authenticated;
revoke all on function public.set_conversation_preview() from public, anon, authenticated;
revoke all on function public.set_updated_at() from public, anon, authenticated;
revoke all on function public.update_pipeline_marker_if_expected(text, text, text, uuid) from public, anon, authenticated;

-- Granted back, with the caller that needs each one:
--
-- has_grant -- read by RLS policies and by anything checking its own
-- capabilities. Originally granted in 20260917000200; re-granted because the
-- blanket revoke above would otherwise take it away.
grant execute on function public.has_grant(text) to authenticated;

-- get_memory_context and promote_memory_to_project -- called by the `memory`
-- edge function (supabase/functions/memory/retrieval.ts:38 and service.ts:145).
-- That function builds its client with the anon key but forwards the caller's
-- JWT (index.ts:103-107) and 401s without a user, so it executes as
-- authenticated, never as anon.
grant execute on function public.get_memory_context(uuid, uuid, uuid, integer, integer, integer) to authenticated;
grant execute on function public.promote_memory_to_project(uuid, uuid) to authenticated;

-- Deliberately NOT granted back:
--
-- archive_stale_memories -- the sharpest of them. It is `update memories set
-- status = 'archived'` with **no user_id predicate at all**, written like a
-- maintenance job but exposed to anon. Today RLS scopes it to the caller's own
-- rows, so anon archives nothing; the day it is made SECURITY DEFINER, as five
-- other functions here already are, it becomes a one-call global wipe. It has
-- no caller anywhere in UI/src, backend/ or supabase/functions/. service_role
-- keeps it.
--
-- match_documents, search_documents_fts -- no callers. search_documents_fts is
-- additionally broken: calling it returns 42804, `Returned type real does not
-- match expected type double precision`. It is one of the hand-built live-only
-- objects 20260915000000 captured verbatim, so it is left in place rather than
-- dropped, but nothing should be able to call it. BUILD_PLAN Phase 4 rebuilds
-- this retrieval path from scratch.
--
-- generate_job_dedupe_hash -- pure, but it backs a GENERATED ALWAYS AS ...
-- STORED column on job_listings, so anyone inserting there evaluates it. Only
-- service_role can insert into job_listings since 20260916000200 dropped the
-- open insert policy, so revoking it from anon and authenticated costs nothing.
-- If a user-facing insert into job_listings is ever added, this grant has to
-- come back with it.
--
-- The trigger functions (handle_updated_at, set_updated_at,
-- set_conversation_preview, create_default_behavior_settings, handle_new_user,
-- enforce_sjsu_email, pipeline_state_set_updated_at,
-- freeze_profile_privilege_columns, generate_job_dedupe_hash) -- EXECUTE is
-- checked when a trigger is created, not each time it fires, so revoking it
-- does not stop existing triggers. verify_policies.sql proves that rather than
-- assuming it: sections 0, 10 and 12 exercise the signup trigger, the preview
-- trigger, handle_updated_at and the role freeze after this revoke.

-- 3. Stop handing these out to every future object ----------------------------

-- Functions fail closed from here: a new RPC is unreachable until it is granted
-- by name. That is a loud failure -- PostgREST returns permission denied -- and
-- the alternative is a silent one, which is what this migration is cleaning up.
-- **If you add an RPC the browser calls, grant it explicitly in the same
-- migration.**
--
-- The PUBLIC line is the load-bearing one, for the reason in section 2: a new
-- function is granted to PUBLIC by CREATE FUNCTION itself, so revoking the
-- anon and authenticated defaults alone would leave it callable.
--
-- **Caveat, and it is a real one:** this also applies to functions an extension
-- creates in public when installed as postgres. pgvector is already installed
-- and is unaffected, but a future `create extension ... with schema public`
-- may need an explicit `grant execute` for its operators to work. If an
-- extension misbehaves right after installation, this is why.
alter default privileges for role postgres in schema public revoke execute on functions from public;
alter default privileges for role postgres in schema public revoke all on functions from anon;
alter default privileges for role postgres in schema public revoke all on functions from authenticated;

-- Tables keep Supabase's model -- new tables are readable and writable subject
-- to RLS -- minus the four privileges RLS cannot filter.
alter default privileges for role postgres in schema public revoke all on tables from anon;
alter default privileges for role postgres in schema public revoke all on tables from authenticated;
alter default privileges for role postgres in schema public
  grant select, insert, update, delete on tables to anon;
alter default privileges for role postgres in schema public
  grant select, insert, update, delete on tables to authenticated;

-- Sequences keep USAGE and SELECT but lose UPDATE, which is setval: enough to
-- force primary key collisions on any table still using a serial.
alter default privileges for role postgres in schema public revoke all on sequences from anon;
alter default privileges for role postgres in schema public revoke all on sequences from authenticated;
alter default privileges for role postgres in schema public grant usage, select on sequences to anon;
alter default privileges for role postgres in schema public grant usage, select on sequences to authenticated;

revoke update on all sequences in schema public from anon;
revoke update on all sequences in schema public from authenticated;
