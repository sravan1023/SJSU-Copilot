\set ON_ERROR_STOP on
\pset pager off
\set QUIET on

-- Did the statement raise? (For grants and WITH CHECK violations.)
create or replace function pg_temp.expect(
  p_label text, p_sql text, p_should_fail boolean,
  p_role text default 'authenticated', p_uid text default null
) returns void language plpgsql as $$
declare failed boolean := false; msg text := '';
begin
  begin
    perform set_config('role', p_role, true);
    if p_uid is not null then
      perform set_config('request.jwt.claim.sub', p_uid, true);
      perform set_config('request.jwt.claim.role', p_role, true);
    end if;
    execute p_sql;
  exception when others then failed := true; msg := substr(sqlerrm, 1, 65);
  end;
  perform set_config('role', 'postgres', true);
  if failed = p_should_fail then
    raise notice '  ok   %', p_label || case when failed then ' [blocked: ' || msg || ']' else '' end;
  else
    raise notice '  FAIL %', p_label || case when failed then ' [unexpectedly blocked: ' || msg || ']' else ' [unexpectedly ALLOWED]' end;
  end if;
end $$;

-- How many rows did the statement actually touch?
--
-- RLS does NOT raise on UPDATE/DELETE when no policy grants the command -- the
-- rows simply aren't visible, so the statement succeeds against zero rows.
-- Asserting "an error was raised" would be testing the wrong thing.
create or replace function pg_temp.expect_rows(
  p_label text, p_sql text, p_expected int,
  p_role text default 'authenticated', p_uid text default null
) returns void language plpgsql as $$
declare n int; msg text;
begin
  begin
    perform set_config('role', p_role, true);
    if p_uid is not null then
      perform set_config('request.jwt.claim.sub', p_uid, true);
      perform set_config('request.jwt.claim.role', p_role, true);
    end if;
    execute p_sql;
    get diagnostics n = row_count;
    perform set_config('role', 'postgres', true);
    if n = p_expected then
      raise notice '  ok   % [% rows affected]', p_label, n;
    else
      raise notice '  FAIL % [expected % rows, got %]', p_label, p_expected, n;
    end if;
  exception when others then
    msg := substr(sqlerrm, 1, 65);
    perform set_config('role', 'postgres', true);
    if p_expected = 0 then
      raise notice '  ok   % [blocked outright: %]', p_label, msg;
    else
      raise notice '  FAIL % [error: %]', p_label, msg;
    end if;
  end;
end $$;

\echo ''
\echo '=== 0. Fixture: can an account be created at all? ==='
DO $$
BEGIN
  INSERT INTO auth.users(id, email) VALUES
    ('11111111-1111-1111-1111-111111111111', 'alice@sjsu.edu'),
    ('22222222-2222-2222-2222-222222222222', 'bob@sjsu.edu');
  RAISE NOTICE '  ok   account creation succeeds';
EXCEPTION WHEN others THEN RAISE NOTICE '  FAIL account creation: %', sqlerrm;
END $$;

DO $$
DECLARE n int;
BEGIN
  SELECT count(*) INTO n FROM public.profiles WHERE id IN
    ('11111111-1111-1111-1111-111111111111','22222222-2222-2222-2222-222222222222');
  IF n = 2 THEN RAISE NOTICE '  ok   profiles auto-created (% rows)', n;
  ELSE RAISE NOTICE '  FAIL expected 2 profiles, got %', n; END IF;
  SELECT count(*) INTO n FROM public.behavior_settings WHERE user_id IN
    ('11111111-1111-1111-1111-111111111111','22222222-2222-2222-2222-222222222222');
  IF n = 2 THEN RAISE NOTICE '  ok   default behavior_settings auto-created (% rows)', n;
  ELSE RAISE NOTICE '  FAIL expected 2 behavior_settings, got %', n; END IF;
END $$;

\echo ''
\echo '=== 1. Regression: the ON CONFLICT signup bug was real ==='
create or replace function public.create_default_behavior_settings()
returns trigger as $$
begin
  insert into behavior_settings (user_id) values (new.id)
  on conflict (user_id) do nothing;
  return new;
end; $$ language plpgsql security definer;

DO $$
BEGIN
  INSERT INTO auth.users(id, email) VALUES ('33333333-3333-3333-3333-333333333333','carol@sjsu.edu');
  RAISE NOTICE '  FAIL pre-fix function did NOT break signup (bug not reproduced)';
EXCEPTION WHEN others THEN
  RAISE NOTICE '  ok   pre-fix breaks signup as predicted: %', substr(sqlerrm, 1, 75);
END $$;

create or replace function public.create_default_behavior_settings()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  if not exists (select 1 from public.behavior_settings
                  where user_id = new.id and project_id is null and conversation_id is null) then
    insert into public.behavior_settings (user_id) values (new.id);
  end if;
  return new;
end $$;

DO $$
BEGIN
  INSERT INTO auth.users(id, email) VALUES ('33333333-3333-3333-3333-333333333333','carol@sjsu.edu');
  RAISE NOTICE '  ok   signup works after the fix';
EXCEPTION WHEN others THEN RAISE NOTICE '  FAIL still broken after fix: %', sqlerrm;
END $$;

\echo ''
\echo '=== 2. behavior_settings scope uniqueness (COALESCE index) ==='
DO $$
BEGIN
  INSERT INTO public.behavior_settings(user_id) VALUES ('11111111-1111-1111-1111-111111111111');
  RAISE NOTICE '  FAIL duplicate global row ALLOWED';
EXCEPTION WHEN unique_violation THEN RAISE NOTICE '  ok   duplicate global row rejected';
WHEN others THEN RAISE NOTICE '  FAIL unexpected: %', sqlerrm;
END $$;

\echo ''
\echo '=== 3. ats_registry RLS (was: no RLS at all, anon read/write) ==='
select pg_temp.expect('anon SELECT ats_registry', 'select count(*) from public.ats_registry', true, 'anon');
select pg_temp.expect('anon INSERT ats_registry',
  $q$insert into public.ats_registry(slug, ats) values ('evil','greenhouse')$q$, true, 'anon');
select pg_temp.expect('anon UPDATE ats_registry',
  'update public.ats_registry set enabled = false', true, 'anon');
select pg_temp.expect('authenticated SELECT ats_registry (should work)',
  'select count(*) from public.ats_registry', false, 'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect('authenticated INSERT ats_registry',
  $q$insert into public.ats_registry(slug, ats) values ('evil2','lever')$q$, true, 'authenticated',
  '11111111-1111-1111-1111-111111111111');

\echo ''
\echo '=== 4. job_sources: stored-SSRF policy removed (rows affected, not errors) ==='
select pg_temp.expect_rows('authenticated UPDATE job_sources source_url',
  $q$update public.job_sources set source_url = 'http://169.254.169.254/'$q$, 0,
  'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect_rows('authenticated DELETE job_sources',
  'delete from public.job_sources', 0, 'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect('authenticated INSERT job_sources',
  $q$insert into public.job_sources(name, source_type, source_url) values ('x','rss','http://169.254.169.254/')$q$,
  true, 'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect('authenticated SELECT job_sources (should work)',
  'select count(*) from public.job_sources', false, 'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect('authenticated INSERT job_listings (phishing apply_url)',
  $q$insert into public.job_listings(title, company, apply_url) values ('x','y','http://evil.example')$q$,
  true, 'authenticated', '11111111-1111-1111-1111-111111111111');

\echo ''
\echo '=== 5. Seeded mock feed disabled ==='
DO $$
DECLARE v boolean;
BEGIN
  SELECT enabled INTO v FROM public.job_sources WHERE name = 'SJSU Career Mock Feed';
  IF v IS NULL THEN RAISE NOTICE '  FAIL mock feed row missing';
  ELSIF v THEN RAISE NOTICE '  FAIL mock feed still enabled';
  ELSE RAISE NOTICE '  ok   SJSU Career Mock Feed disabled'; END IF;
END $$;

\echo ''
\echo '=== 6. IDOR: internship match RPC is gone (20260331 model retired) ==='
-- The function took any user's id and returned their job preferences. The whole
-- model was retired by 20260915000000_reconcile_live.sql, so the fix is that
-- there is nothing left to call.
DO $$
BEGIN
  IF to_regprocedure('public.match_internship_listings_for_user(uuid, integer)') IS NULL
     AND to_regclass('public.internship_alert_preferences') IS NULL
  THEN RAISE NOTICE '  ok   match_internship_listings_for_user and its data are gone';
  ELSE RAISE NOTICE '  FAIL retired internship matching objects still exist'; END IF;
END $$;

\echo ''
\echo '=== 7. update_pipeline_marker_if_expected locked down ==='
select pg_temp.expect('authenticated EXECUTE pipeline marker',
  $q$select public.update_pipeline_marker_if_expected('intern_jobs_alert', null, 'x', null)$q$,
  true, 'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect('anon EXECUTE pipeline marker',
  $q$select public.update_pipeline_marker_if_expected('intern_jobs_alert', null, 'x', null)$q$,
  true, 'anon');

\echo ''
\echo '=== 8. WITH CHECK: row-hijack by reassigning user_id ==='
insert into public.conversations(id, user_id, title) values
  ('aaaaaaaa-0000-0000-0000-000000000001','11111111-1111-1111-1111-111111111111','alice convo');
insert into public.memories(user_id, scope, category, content) values
  ('11111111-1111-1111-1111-111111111111','global','fact','alice memory');

select pg_temp.expect_rows('alice UPDATE own conversation (normal)',
  $q$update public.conversations set title = 'renamed' where id = 'aaaaaaaa-0000-0000-0000-000000000001'$q$,
  1, 'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect('alice reassigns conversation to bob',
  $q$update public.conversations set user_id = '22222222-2222-2222-2222-222222222222' where id = 'aaaaaaaa-0000-0000-0000-000000000001'$q$,
  true, 'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect('alice reassigns memory to bob',
  $q$update public.memories set user_id = '22222222-2222-2222-2222-222222222222' where content = 'alice memory'$q$,
  true, 'authenticated', '11111111-1111-1111-1111-111111111111');

\echo ''
\echo '=== 9. anon-readable internship_listing_public view is gone ==='
select pg_temp.expect('anon SELECT internship_listing_public',
  'select count(*) from public.internship_listing_public', true, 'anon');
DO $$
BEGIN
  IF to_regclass('public.internship_listing_public') IS NULL
     AND to_regclass('public.internship_listings') IS NULL
  THEN RAISE NOTICE '  ok   internship_listing_public and internship_listings are gone';
  ELSE RAISE NOTICE '  FAIL retired internship listing objects still exist'; END IF;
END $$;

\echo ''
\echo '=== 10. message preview trigger ==='
DO $$
DECLARE preview text;
BEGIN
  INSERT INTO public.messages(conversation_id, role, content)
  VALUES ('aaaaaaaa-0000-0000-0000-000000000001', 'user', repeat('x', 200));
  SELECT last_message_preview INTO preview FROM public.conversations
   WHERE id = 'aaaaaaaa-0000-0000-0000-000000000001';
  IF preview IS NULL THEN RAISE NOTICE '  FAIL preview not set by trigger';
  ELSIF length(preview) = 83 AND right(preview,3) = '...' THEN
    RAISE NOTICE '  ok   trigger truncated preview to 80 chars + ellipsis';
  ELSE RAISE NOTICE '  FAIL unexpected preview length %', length(preview); END IF;
END $$;

\echo ''
\echo '=== 11. Cross-user read isolation ==='
select pg_temp.expect_rows('bob UPDATE alice conversation',
  $q$update public.conversations set title = 'stolen' where user_id = '11111111-1111-1111-1111-111111111111'$q$,
  0, 'authenticated', '22222222-2222-2222-2222-222222222222');
select pg_temp.expect_rows('bob DELETE alice memories',
  $q$delete from public.memories where user_id = '11111111-1111-1111-1111-111111111111'$q$,
  0, 'authenticated', '22222222-2222-2222-2222-222222222222');

\echo ''
\echo '=== 12. profiles.role is still self-writable (NOT yet fixed) ==='
select pg_temp.expect_rows('alice sets own role to admin',
  $q$update public.profiles set role = 'admin' where id = '11111111-1111-1111-1111-111111111111'$q$,
  1, 'authenticated', '11111111-1111-1111-1111-111111111111');

\echo ''
\echo '=== 13. Reconciled with live (20260915000000_reconcile_live.sql) ==='
-- Objects that existed only on the hand-built live database are now part of the
-- history, and chat_messages (superseded by messages) is gone.
DO $$
DECLARE missing text[] := '{}';
BEGIN
  IF to_regprocedure('public.search_documents_fts(text, integer)') IS NULL THEN missing := missing || 'search_documents_fts'::text; END IF;
  IF to_regclass('public.idx_documents_last_verified_at') IS NULL THEN missing := missing || 'idx_documents_last_verified_at'::text; END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'messages' AND column_name = 'citations') THEN missing := missing || 'messages.citations'::text; END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'documents' AND column_name = 'content_hash') THEN missing := missing || 'documents.content_hash'::text; END IF;
  IF cardinality(missing) = 0 THEN RAISE NOTICE '  ok   live-only objects captured';
  ELSE RAISE NOTICE '  FAIL missing after reconcile: %', array_to_string(missing, ', '); END IF;
  IF to_regclass('public.chat_messages') IS NULL THEN RAISE NOTICE '  ok   chat_messages retired';
  ELSE RAISE NOTICE '  FAIL chat_messages still exists'; END IF;
END $$;
