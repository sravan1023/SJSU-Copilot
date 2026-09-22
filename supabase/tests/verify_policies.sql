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
\echo '=== 12. profiles: privilege columns frozen, editable columns still work ==='
-- Was, from 2026-09-16 until 20260917000200 landed:
--   expect_rows('alice sets own role to admin', ..., 1)
-- i.e. this section asserted the escalation SUCCEEDED, deliberately, so the fix
-- would have something to flip. The assertion is now expect(..., should_fail)
-- rather than expect_rows(..., 0), because the column grant makes the statement
-- RAISE (permission denied) rather than filter to zero rows.
select pg_temp.expect('alice sets own role to admin',
  $q$update public.profiles set role = 'admin' where id = '11111111-1111-1111-1111-111111111111'$q$,
  true, 'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect('alice sets own email',
  $q$update public.profiles set email = 'alice@evil.example' where id = '11111111-1111-1111-1111-111111111111'$q$,
  true, 'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect('alice inserts a profile naming role',
  $q$insert into public.profiles (id, email, full_name, role) values ('44444444-4444-4444-4444-444444444444', 'mallory@sjsu.edu', 'M', 'admin')$q$,
  true, 'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect_rows('alice deletes own profile',
  $q$delete from public.profiles where id = '11111111-1111-1111-1111-111111111111'$q$,
  0, 'authenticated', '11111111-1111-1111-1111-111111111111');

-- The other half of the allowlist, and the one that actually breaks the product
-- if it is wrong: every column UI/src/components/UserProfile.jsx:80-87 writes,
-- in one statement, exactly as the client sends it.
select pg_temp.expect_rows('alice saves the real UserProfile.jsx column set',
  $q$update public.profiles set full_name = 'Alice A', university_id = '012345678', phone = '408-555-0100', major = 'CS', minor = 'Math', graduation_year = 2027, class_standing = 'Junior', gpa = 3.75 where id = '11111111-1111-1111-1111-111111111111'$q$,
  1, 'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect_rows('alice sets own active_audience',
  $q$update public.profiles set active_audience = 'alumni' where id = '11111111-1111-1111-1111-111111111111'$q$,
  1, 'authenticated', '11111111-1111-1111-1111-111111111111');

-- A column-level grant must not stop the BEFORE trigger from maintaining a
-- column the user cannot name.
DO $$
DECLARE before_ts timestamptz; after_ts timestamptz;
BEGIN
  SELECT updated_at INTO before_ts FROM public.profiles WHERE id = '11111111-1111-1111-1111-111111111111';
  PERFORM pg_sleep(0.01);
  PERFORM set_config('role', 'authenticated', true);
  PERFORM set_config('request.jwt.claim.sub', '11111111-1111-1111-1111-111111111111', true);
  PERFORM set_config('request.jwt.claim.role', 'authenticated', true);
  UPDATE public.profiles SET full_name = 'Alice B' WHERE id = '11111111-1111-1111-1111-111111111111';
  PERFORM set_config('role', 'postgres', true);
  SELECT updated_at INTO after_ts FROM public.profiles WHERE id = '11111111-1111-1111-1111-111111111111';
  IF after_ts > before_ts THEN RAISE NOTICE '  ok   handle_updated_at still fires under the column allowlist';
  ELSE RAISE NOTICE '  FAIL updated_at did not move (% -> %)', before_ts, after_ts; END IF;
END $$;

-- The empirical check. `revoke update (role) ... from authenticated` against a
-- table-level GRANT ALL is a silent no-op, so asserting the statements ran is
-- worth nothing -- read the resulting ACL instead. If the revoke ever stops
-- working, `got` becomes every column on the table and this fails loudly.
DO $$
DECLARE got text[];
        want text[] := array['active_audience','class_standing','full_name','gpa',
                             'graduation_year','major','minor','onboarded_at',
                             'phone','university_id'];
BEGIN
  SELECT array_agg(column_name::text ORDER BY column_name) INTO got
    FROM information_schema.column_privileges
   WHERE table_schema = 'public' AND table_name = 'profiles'
     AND grantee = 'authenticated' AND privilege_type = 'UPDATE';
  IF got IS NOT DISTINCT FROM want THEN
    RAISE NOTICE '  ok   profiles UPDATE allowlist is exactly the 10 expected columns';
  ELSE
    RAISE NOTICE '  FAIL profiles UPDATE allowlist is % (want %)', got, want;
  END IF;
END $$;

-- The backstop trigger, reached by restoring the grant the revoke removed.
-- Section 1 sets the precedent for harness-local DDL like this.
grant update (role) on public.profiles to authenticated;
select pg_temp.expect_rows('alice updates role with the grant restored',
  $q$update public.profiles set role = 'admin' where id = '11111111-1111-1111-1111-111111111111'$q$,
  1, 'authenticated', '11111111-1111-1111-1111-111111111111');
DO $$
DECLARE stored text;
BEGIN
  -- A BEFORE trigger returns NEW, so the row IS updated and row_count is 1.
  -- The value is what matters, not whether the statement touched a row.
  SELECT role INTO stored FROM public.profiles WHERE id = '11111111-1111-1111-1111-111111111111';
  IF stored = 'student' THEN RAISE NOTICE '  ok   freeze trigger reverted role to %', stored;
  ELSE RAISE NOTICE '  FAIL freeze trigger let role become %', stored; END IF;
END $$;
revoke update (role) on public.profiles from authenticated;

select pg_temp.expect_rows('service_role sets alice role to advisor',
  $q$update public.profiles set role = 'advisor' where id = '11111111-1111-1111-1111-111111111111'$q$,
  1, 'service_role', '11111111-1111-1111-1111-111111111111');
DO $$
DECLARE stored text;
BEGIN
  SELECT role INTO stored FROM public.profiles WHERE id = '11111111-1111-1111-1111-111111111111';
  IF stored = 'advisor' THEN RAISE NOTICE '  ok   service_role can still set role';
  ELSE RAISE NOTICE '  FAIL service_role write was reverted to %', stored; END IF;
  UPDATE public.profiles SET role = 'student' WHERE id = '11111111-1111-1111-1111-111111111111';
END $$;

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

\echo ''
\echo '=== 14. user_affiliations: users declare, never verify ==='
select pg_temp.expect('alice declares her own affiliation',
  $q$insert into public.user_affiliations (user_id, affiliation, source) values ('11111111-1111-1111-1111-111111111111', 'alumni', 'self')$q$,
  false, 'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect('alice claims a VERIFIED affiliation',
  $q$insert into public.user_affiliations (user_id, affiliation, status) values ('11111111-1111-1111-1111-111111111111', 'faculty', 'verified')$q$,
  true, 'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect('alice declares an affiliation for bob',
  $q$insert into public.user_affiliations (user_id, affiliation) values ('22222222-2222-2222-2222-222222222222', 'faculty')$q$,
  true, 'authenticated', '11111111-1111-1111-1111-111111111111');
-- `select 1 ... where` rather than count(*): a count always returns one row no
-- matter what RLS hid, so it could never detect a leak.
select pg_temp.expect_rows('bob sees no affiliations of alice',
  $q$select 1 from public.user_affiliations where user_id = '11111111-1111-1111-1111-111111111111'$q$,
  0, 'authenticated', '22222222-2222-2222-2222-222222222222');
select pg_temp.expect_rows('alice sets source on her own declared row',
  $q$update public.user_affiliations set source = 'updated' where user_id = '11111111-1111-1111-1111-111111111111' and affiliation = 'alumni'$q$,
  1, 'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect('alice promotes her own row to verified',
  $q$update public.user_affiliations set status = 'verified' where user_id = '11111111-1111-1111-1111-111111111111' and affiliation = 'alumni'$q$,
  true, 'authenticated', '11111111-1111-1111-1111-111111111111');

-- Staff verify the alumni claim, as the service role.
select pg_temp.expect_rows('service_role verifies the alumni affiliation',
  $q$update public.user_affiliations set status = 'verified', verified_at = now() where user_id = '11111111-1111-1111-1111-111111111111' and affiliation = 'alumni'$q$,
  1, 'service_role', '11111111-1111-1111-1111-111111111111');

-- The reason status='declared' is in the UPDATE USING clause and not only in
-- WITH CHECK. With WITH CHECK alone this would succeed, and alice would hold a
-- VERIFIED faculty affiliation she was never granted.
select pg_temp.expect_rows('alice re-points her VERIFIED row at faculty',
  $q$update public.user_affiliations set affiliation = 'faculty' where user_id = '11111111-1111-1111-1111-111111111111' and status = 'verified'$q$,
  0, 'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect_rows('alice deletes her VERIFIED row',
  $q$delete from public.user_affiliations where user_id = '11111111-1111-1111-1111-111111111111' and status = 'verified'$q$,
  0, 'authenticated', '11111111-1111-1111-1111-111111111111');

-- The backfill in 20260917000100 covers the profiles that existed when it ran.
-- On a fresh replay that is none: this harness creates its fixture users
-- afterwards. So re-run the same statement here -- which also exercises its
-- idempotency, since alice already has a declared alumni row and bob is about
-- to get a student one in section 18.
DO $$
DECLARE missing int; verified int; promoted int;
BEGIN
  INSERT INTO public.user_affiliations (user_id, affiliation, status, source)
  SELECT p.id, 'student'::public.affiliation_kind,
         'declared'::public.verification_status, 'legacy_backfill'
    FROM public.profiles p
  ON CONFLICT (user_id, affiliation) DO NOTHING;

  SELECT count(*) INTO missing
    FROM public.profiles p
   WHERE NOT EXISTS (
     SELECT 1 FROM public.user_affiliations a
      WHERE a.user_id = p.id AND a.affiliation = 'student');
  IF missing = 0 THEN RAISE NOTICE '  ok   the backfill statement covers every profile';
  ELSE RAISE NOTICE '  FAIL % profiles have no student affiliation', missing; END IF;

  SELECT count(*) INTO verified
    FROM public.user_affiliations WHERE source = 'legacy_backfill' AND status <> 'declared';
  IF verified = 0 THEN RAISE NOTICE '  ok   the backfill verified nothing';
  ELSE RAISE NOTICE '  FAIL % backfilled rows are not declared', verified; END IF;

  -- Alice's alumni row was verified above. The backfill must not have touched
  -- it, or a re-run would quietly downgrade real verifications.
  SELECT count(*) INTO promoted
    FROM public.user_affiliations
   WHERE user_id = '11111111-1111-1111-1111-111111111111'
     AND affiliation = 'alumni' AND status = 'verified';
  IF promoted = 1 THEN RAISE NOTICE '  ok   re-running the backfill left a verified row alone';
  ELSE RAISE NOTICE '  FAIL the verified alumni row did not survive a backfill re-run'; END IF;
END $$;

\echo ''
\echo '=== 15. admin_grants: server-managed only, and has_grant ==='
select pg_temp.expect('alice grants herself a capability',
  $q$insert into public.admin_grants (user_id, capability) values ('11111111-1111-1111-1111-111111111111', 'run_jobs')$q$,
  true, 'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect('service_role seeds a grant for alice',
  $q$insert into public.admin_grants (user_id, capability) values ('11111111-1111-1111-1111-111111111111', 'run_jobs')$q$,
  false, 'service_role', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect('a malformed capability is rejected at seed time',
  $q$insert into public.admin_grants (user_id, capability) values ('22222222-2222-2222-2222-222222222222', 'Run Jobs')$q$,
  true, 'service_role', '22222222-2222-2222-2222-222222222222');
select pg_temp.expect_rows('alice reads her own grant',
  $q$select 1 from public.admin_grants where user_id = '11111111-1111-1111-1111-111111111111'$q$,
  1, 'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect_rows('bob reads alice grants',
  $q$select 1 from public.admin_grants where user_id = '11111111-1111-1111-1111-111111111111'$q$,
  0, 'authenticated', '22222222-2222-2222-2222-222222222222');
select pg_temp.expect_rows('alice cannot update her grant',
  $q$update public.admin_grants set capability = 'run_ingestion' where user_id = '11111111-1111-1111-1111-111111111111'$q$,
  0, 'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect_rows('alice cannot delete her grant',
  $q$delete from public.admin_grants where user_id = '11111111-1111-1111-1111-111111111111'$q$,
  0, 'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect_rows('has_grant is true for alice',
  $q$select 1 where public.has_grant('run_jobs')$q$,
  1, 'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect_rows('has_grant is false for bob',
  $q$select 1 where public.has_grant('run_jobs')$q$,
  0, 'authenticated', '22222222-2222-2222-2222-222222222222');
select pg_temp.expect('anon cannot execute has_grant',
  $q$select public.has_grant('run_jobs')$q$,
  true, 'anon');

insert into public.admin_grants (user_id, capability, expires_at)
  values ('22222222-2222-2222-2222-222222222222', 'run_jobs', now() - interval '1 day');
select pg_temp.expect_rows('an expired grant does not count',
  $q$select 1 where public.has_grant('run_jobs')$q$,
  0, 'authenticated', '22222222-2222-2222-2222-222222222222');

\echo ''
\echo '=== 16. profile_audience_details ==='
select pg_temp.expect('alice writes her own audience details',
  $q$insert into public.profile_audience_details (user_id, audience, details) values ('11111111-1111-1111-1111-111111111111', 'alumni', '{"employer":"Acme"}'::jsonb)$q$,
  false, 'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect('alice writes details for bob',
  $q$insert into public.profile_audience_details (user_id, audience, details) values ('22222222-2222-2222-2222-222222222222', 'alumni', '{}'::jsonb)$q$,
  true, 'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect_rows('bob reads alice details',
  $q$select 1 from public.profile_audience_details where user_id = '11111111-1111-1111-1111-111111111111'$q$,
  0, 'authenticated', '22222222-2222-2222-2222-222222222222');
select pg_temp.expect('an oversized details blob is rejected',
  $q$insert into public.profile_audience_details (user_id, audience, details) values ('11111111-1111-1111-1111-111111111111', 'faculty', jsonb_build_object('bio', repeat('x', 9000)))$q$,
  true, 'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect('an unknown audience is rejected',
  $q$insert into public.profile_audience_details (user_id, audience, details) values ('11111111-1111-1111-1111-111111111111', 'martian', '{}'::jsonb)$q$,
  true, 'authenticated', '11111111-1111-1111-1111-111111111111');

\echo ''
\echo '=== 17. conversations.audience snapshot ==='
select pg_temp.expect_rows('alice stamps her own conversation',
  $q$update public.conversations set audience = 'alumni' where user_id = '11111111-1111-1111-1111-111111111111'$q$,
  1, 'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect('an unknown audience is rejected',
  $q$update public.conversations set audience = 'martian' where user_id = '11111111-1111-1111-1111-111111111111'$q$,
  true, 'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect_rows('bob stamps alice conversation',
  $q$update public.conversations set audience = 'faculty' where user_id = '11111111-1111-1111-1111-111111111111'$q$,
  0, 'authenticated', '22222222-2222-2222-2222-222222222222');

\echo ''
\echo '=== 18. Column-level INSERT grants still let defaults apply ==='
-- The user_affiliations design leans on this: authenticated cannot NAME status,
-- so the column default has to supply it. Measured, not assumed.
-- 'community' rather than 'student': section 14 re-runs the backfill, which
-- gives every profile a student row, so that one would collide.
select pg_temp.expect('bob declares an affiliation naming only the granted columns',
  $q$insert into public.user_affiliations (user_id, affiliation) values ('22222222-2222-2222-2222-222222222222', 'community')$q$,
  false, 'authenticated', '22222222-2222-2222-2222-222222222222');
DO $$
DECLARE got text;
BEGIN
  SELECT status::text INTO got FROM public.user_affiliations
   WHERE user_id = '22222222-2222-2222-2222-222222222222' AND affiliation = 'community';
  IF got = 'declared' THEN RAISE NOTICE '  ok   default status applied under a column grant (%)', got;
  ELSE RAISE NOTICE '  FAIL status came out as %', got; END IF;
END $$;
