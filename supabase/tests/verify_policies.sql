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

-- Did the statement raise for the expected reason?
--
-- pg_temp.expect only asks whether a statement raised, so a constraint test can
-- pass on a missing fixture row (a foreign-key error), a permission error, or a
-- different constraint than the one under test. This also checks the SQLSTATE
-- and the object the error names: the constraint for a check or unique
-- violation, the column for a not-null violation.
create or replace function pg_temp.expect_violation(
  p_label text, p_sql text, p_sqlstate text, p_object text,
  p_role text default 'authenticated', p_uid text default null
) returns void language plpgsql as $$
declare raised boolean := false; st text; con text; col text; msg text;
begin
  begin
    perform set_config('role', p_role, true);
    if p_uid is not null then
      perform set_config('request.jwt.claim.sub', p_uid, true);
      perform set_config('request.jwt.claim.role', p_role, true);
    end if;
    execute p_sql;
  exception when others then
    raised := true;
    get stacked diagnostics st = returned_sqlstate, con = constraint_name,
                            col = column_name, msg = message_text;
  end;
  perform set_config('role', 'postgres', true);
  if not raised then
    raise notice '  FAIL % [unexpectedly ALLOWED]', p_label;
  elsif st = p_sqlstate and p_object in (con, col) then
    raise notice '  ok   % [blocked: % on %]', p_label, st, p_object;
  else
    raise notice '  FAIL % [blocked for the wrong reason: % %]', p_label, st, substr(msg, 1, 65);
  end if;
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

-- search_documents_fts declared `rank double precision` while ts_rank_cd returns
-- `real`, so the first matching row raised 42804. The defect was unobservable
-- for as long as the table was empty: an empty result set produces no tuple, so
-- nothing is ever type-checked. 20260930000100 fixed it, and this is the only
-- place the fix is provable -- it needs a row that actually matches.
DO $$
DECLARE doc_id uuid; hits int;
BEGIN
  INSERT INTO public.documents (title, url, source, visibility, collection)
    VALUES ('Visitor parking', 'https://example.invalid/kb/parking', 'sjsu.edu', 'public', 'guest')
    RETURNING id INTO doc_id;
  INSERT INTO public.document_chunks (document_id, chunk_index, heading, content)
    VALUES (doc_id, 0, 'Parking', 'Visitor parking is available in the North Garage.');

  SELECT count(*) INTO hits FROM public.search_documents_fts('parking', 5);
  IF hits >= 1 THEN
    RAISE NOTICE '  ok   search_documents_fts returns a matching row (42804 fixed)';
  ELSE
    RAISE NOTICE '  FAIL search_documents_fts matched nothing for an indexed term';
  END IF;
EXCEPTION WHEN others THEN
  RAISE NOTICE '  FAIL search_documents_fts raised: %', substr(sqlerrm, 1, 65);
END $$;

-- The generated tsv exists and the GIN index is there to serve it. Without the
-- index the function still works and silently seq-scans the whole corpus.
DO $$
DECLARE missing text[] := '{}';
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                  WHERE table_schema = 'public' AND table_name = 'document_chunks'
                    AND column_name = 'tsv' AND is_generated = 'ALWAYS')
    THEN missing := missing || 'document_chunks.tsv (generated)'::text; END IF;
  IF to_regclass('public.idx_document_chunks_tsv') IS NULL
    THEN missing := missing || 'idx_document_chunks_tsv'::text; END IF;
  IF to_regclass('public.uq_document_chunks_doc_idx') IS NULL
    THEN missing := missing || 'uq_document_chunks_doc_idx'::text; END IF;
  IF to_regclass('public.uq_documents_url') IS NULL
    THEN missing := missing || 'uq_documents_url'::text; END IF;
  IF to_regclass('public.idx_document_chunks_embedding_hnsw') IS NULL
    THEN missing := missing || 'idx_document_chunks_embedding_hnsw'::text; END IF;
  -- The empty-table ivfflat index had meaningless centroids and had to go.
  IF to_regclass('public.idx_document_chunks_embedding') IS NOT NULL
    THEN missing := missing || 'ivfflat index still present'::text; END IF;
  IF cardinality(missing) = 0 THEN
    RAISE NOTICE '  ok   kb indexes are as 20260930000100 leaves them';
  ELSE RAISE NOTICE '  FAIL kb index problems: %', array_to_string(missing, ', '); END IF;
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

\echo ''
\echo '=== 19. Privileges RLS cannot filter (20260918000100) ==='
-- TRUNCATE is the one that matters: it ignores every policy on the table.
select pg_temp.expect('alice truncates conversations',
  $q$truncate table public.conversations$q$,
  true, 'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect('anon truncates profiles',
  $q$truncate table public.profiles$q$,
  true, 'anon');
-- TRIGGER needs no CREATE on the schema, so it is its own escalation path.
select pg_temp.expect('alice creates a trigger on profiles',
  $q$create trigger t_evil before insert on public.profiles for each row execute function public.handle_updated_at()$q$,
  true, 'authenticated', '11111111-1111-1111-1111-111111111111');

DO $$
DECLARE leftover text;
BEGIN
  -- No table in the schema may still hand these to anon or authenticated.
  SELECT string_agg(DISTINCT table_name || ':' || grantee || ':' || privilege_type, ', ')
    INTO leftover
    FROM information_schema.table_privileges
   WHERE table_schema = 'public'
     AND grantee IN ('anon', 'authenticated')
     AND privilege_type IN ('TRUNCATE', 'REFERENCES', 'TRIGGER', 'MAINTAIN');
  IF leftover IS NULL THEN
    RAISE NOTICE '  ok   no table grants TRUNCATE/REFERENCES/TRIGGER/MAINTAIN to anon or authenticated';
  ELSE
    RAISE NOTICE '  FAIL still granted: %', leftover;
  END IF;
END $$;

DO $$
DECLARE n int;
BEGIN
  -- CRUD must be untouched, or an RLS policy somewhere has nothing to act on.
  SELECT count(*) INTO n
    FROM information_schema.table_privileges
   WHERE table_schema = 'public' AND table_name = 'conversations'
     AND grantee = 'authenticated'
     AND privilege_type IN ('SELECT', 'INSERT', 'UPDATE', 'DELETE');
  IF n = 4 THEN RAISE NOTICE '  ok   CRUD on conversations is untouched (4 privileges)';
  ELSE RAISE NOTICE '  FAIL conversations has % of 4 CRUD privileges for authenticated', n; END IF;
END $$;

\echo ''
\echo '=== 20. Functions are deny-by-default, granted by name ==='
select pg_temp.expect('anon executes archive_stale_memories',
  $q$select public.archive_stale_memories(0, 2147483647)$q$,
  true, 'anon');
-- The one that would be a global wipe if it were ever made SECURITY DEFINER.
-- It has no caller anywhere; service_role keeps it.
select pg_temp.expect('alice executes archive_stale_memories',
  $q$select public.archive_stale_memories(0, 2147483647)$q$,
  true, 'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect('anon executes get_memory_context',
  $q$select public.get_memory_context('11111111-1111-1111-1111-111111111111'::uuid, null::uuid)$q$,
  true, 'anon');
-- match_documents had no caller and 20261004000100 drops it. An expect() here
-- would pass on "function does not exist", so the catalog is checked instead.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                 WHERE n.nspname = 'public' AND p.proname = 'match_documents')
  THEN RAISE NOTICE '  ok   match_documents dropped';
  ELSE RAISE NOTICE '  FAIL match_documents still exists'; END IF;
END $$;
-- The four functions 20260930000100 creates or recreates. Deny-by-default is
-- aspirational without an assertion per function: measured on this image, a new
-- function in public still carries EXECUTE to PUBLIC despite
-- 20260918000100's default-privileges change, so each one is revoked by hand and
-- each revoke is checked here.
select pg_temp.expect('anon executes search_documents_fts',
  $q$select * from public.search_documents_fts('parking', 1)$q$,
  true, 'anon');
select pg_temp.expect('anon executes search_kb_chunks',
  $q$select * from public.search_kb_chunks('parking', 'guest', false, 1)$q$,
  true, 'anon');
select pg_temp.expect('anon executes match_kb_hybrid',
  $q$select * from public.match_kb_hybrid('parking', null, 'guest', false, 1, 60)$q$,
  true, 'anon');
select pg_temp.expect('anon executes replace_document_chunks',
  $q$select public.replace_document_chunks('00000000-0000-0000-0000-000000000000'::uuid, '[]'::jsonb)$q$,
  true, 'anon');
select pg_temp.expect('alice executes search_kb_chunks',
  $q$select * from public.search_kb_chunks('parking', 'student', true, 1)$q$,
  true, 'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect('alice executes replace_document_chunks',
  $q$select public.replace_document_chunks('00000000-0000-0000-0000-000000000000'::uuid, '[]'::jsonb)$q$,
  true, 'authenticated', '11111111-1111-1111-1111-111111111111');

select pg_temp.expect('anon executes generate_job_dedupe_hash',
  $q$select public.generate_job_dedupe_hash('t', 'c')$q$,
  true, 'anon');

-- Kept, because the memory edge function forwards the user's JWT and so runs
-- as authenticated.
select pg_temp.expect('alice executes get_memory_context',
  $q$select public.get_memory_context('11111111-1111-1111-1111-111111111111'::uuid, null::uuid)$q$,
  false, 'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect('alice executes has_grant',
  $q$select public.has_grant('run_jobs')$q$,
  false, 'authenticated', '11111111-1111-1111-1111-111111111111');

DO $$
DECLARE leftover text;
BEGIN
  -- Extension-owned functions are excluded on purpose (deptype 'e'). pgvector
  -- installs ~95 operator, type-I/O and index-support functions into public,
  -- and operators check EXECUTE on the function behind them -- revoking those
  -- would break the vector type for everyone. This asserts the application
  -- surface only, which is what the migration revokes.
  SELECT string_agg(DISTINCT p.proname, ', ') INTO leftover
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
   WHERE n.nspname = 'public'
     AND has_function_privilege('anon', p.oid, 'EXECUTE')
     AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = p.oid AND d.deptype = 'e');
  IF leftover IS NULL THEN RAISE NOTICE '  ok   anon can execute no application function in public';
  ELSE RAISE NOTICE '  FAIL anon can still execute: %', leftover; END IF;
END $$;

DO $$
DECLARE leftover text;
BEGIN
  -- Same for authenticated, minus the three granted back by name.
  SELECT string_agg(DISTINCT p.proname, ', ') INTO leftover
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
   WHERE n.nspname = 'public'
     AND has_function_privilege('authenticated', p.oid, 'EXECUTE')
     AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = p.oid AND d.deptype = 'e')
     AND p.proname NOT IN ('has_grant', 'get_memory_context', 'promote_memory_to_project');
  IF leftover IS NULL THEN
    RAISE NOTICE '  ok   authenticated executes only the three functions granted by name';
  ELSE RAISE NOTICE '  FAIL authenticated can also execute: %', leftover; END IF;
END $$;

\echo ''
\echo '=== 21. Revoking EXECUTE did not stop triggers firing ==='
-- EXECUTE is checked when a trigger is created, not each time it fires. That is
-- the assumption section 2 of the migration rests on, so measure it: every one
-- of these runs a trigger function that anon and authenticated can no longer
-- call directly.
DO $$
DECLARE before_ts timestamptz; after_ts timestamptz; preview text;
BEGIN
  SELECT updated_at INTO before_ts FROM public.profiles WHERE id = '11111111-1111-1111-1111-111111111111';
  PERFORM pg_sleep(0.01);
  PERFORM set_config('role', 'authenticated', true);
  PERFORM set_config('request.jwt.claim.sub', '11111111-1111-1111-1111-111111111111', true);
  PERFORM set_config('request.jwt.claim.role', 'authenticated', true);
  UPDATE public.profiles SET full_name = 'Alice C' WHERE id = '11111111-1111-1111-1111-111111111111';
  INSERT INTO public.messages(conversation_id, role, content)
    VALUES ((SELECT id FROM public.conversations WHERE user_id = '11111111-1111-1111-1111-111111111111' LIMIT 1),
            'user', 'does the preview trigger still fire after the revoke?');
  PERFORM set_config('role', 'postgres', true);

  SELECT updated_at INTO after_ts FROM public.profiles WHERE id = '11111111-1111-1111-1111-111111111111';
  IF after_ts > before_ts THEN RAISE NOTICE '  ok   handle_updated_at still fires (EXECUTE revoked)';
  ELSE RAISE NOTICE '  FAIL handle_updated_at stopped firing after the revoke'; END IF;

  SELECT last_message_preview INTO preview FROM public.conversations
   WHERE user_id = '11111111-1111-1111-1111-111111111111' LIMIT 1;
  IF preview LIKE 'does the preview trigger%' THEN
    RAISE NOTICE '  ok   set_conversation_preview still fires (EXECUTE revoked)';
  ELSE RAISE NOTICE '  FAIL preview trigger stopped firing: %', preview; END IF;
END $$;

-- The signup chain (handle_new_user -> create_default_behavior_settings) is the
-- highest-consequence trigger path of all: if the revoke broke it, no account
-- could be created.
DO $$
DECLARE uid uuid := '55555555-5555-5555-5555-555555555555';
BEGIN
  INSERT INTO auth.users (id, email, raw_user_meta_data)
    VALUES (uid, 'trigger-probe@sjsu.edu', '{"full_name":"Trigger Probe"}'::jsonb);
  IF EXISTS (SELECT 1 FROM public.profiles WHERE id = uid)
     AND EXISTS (SELECT 1 FROM public.behavior_settings WHERE user_id = uid) THEN
    RAISE NOTICE '  ok   signup chain still fires (EXECUTE revoked)';
  ELSE
    RAISE NOTICE '  FAIL signup chain broke after the revoke';
  END IF;
EXCEPTION WHEN others THEN
  RAISE NOTICE '  FAIL signup raised after the revoke: %', substr(sqlerrm, 1, 65);
END $$;

-- generate_job_dedupe_hash backs a GENERATED ALWAYS AS ... STORED column on
-- job_listings, so an insert evaluates it. Revoking EXECUTE from anon and
-- authenticated must not stop the service role writing there -- that is the
-- whole job pipeline.
DO $$
BEGIN
  PERFORM set_config('role', 'service_role', true);
  INSERT INTO public.job_listings(title, company, apply_url)
    VALUES ('Dedupe probe', 'Acme', 'https://example.invalid/job');
  PERFORM set_config('role', 'postgres', true);
  IF EXISTS (SELECT 1 FROM public.job_listings
              WHERE title = 'Dedupe probe' AND dedupe_hash IS NOT NULL) THEN
    RAISE NOTICE '  ok   service_role still inserts job_listings (generated column evaluated)';
  ELSE
    RAISE NOTICE '  FAIL generated dedupe_hash was not produced';
  END IF;
EXCEPTION WHEN others THEN
  PERFORM set_config('role', 'postgres', true);
  RAISE NOTICE '  FAIL service_role insert into job_listings broke: %', substr(sqlerrm, 1, 65);
END $$;

\echo ''
\echo '=== 22. Knowledge base visibility (20260930000100) ==='
-- Two independent boundaries guard the same rule, and both are tested here
-- because neither covers the other:
--
--   * RLS on documents/document_chunks -- what a browser reading through
--     PostgREST would see. Not what the chat path uses.
--   * `p_include_authenticated` inside search_kb_chunks/match_kb_hybrid -- what
--     the chat path actually relies on, because the backend reads with the
--     service key and service_role bypasses RLS entirely.
--
-- Testing only the policies would leave the real control unverified; testing
-- only the function would leave `visibility` as a backend convention rather than
-- a database boundary.

-- Fixtures: one document per visibility, each with one matching chunk.
DO $$
DECLARE pub_id uuid; auth_id uuid; res_id uuid;
BEGIN
  INSERT INTO public.documents (title, url, source, visibility, collection, audience_tags)
    VALUES ('KB public doc', 'https://example.invalid/kbvis/pub', 'sjsu.edu', 'public', 'guest', '{guest}')
    RETURNING id INTO pub_id;
  INSERT INTO public.documents (title, url, source, visibility, collection, audience_tags)
    VALUES ('KB authenticated doc', 'https://example.invalid/kbvis/auth', 'sjsu.edu', 'authenticated', 'student', '{student}')
    RETURNING id INTO auth_id;
  INSERT INTO public.documents (title, url, source, visibility, collection, audience_tags)
    VALUES ('KB restricted doc', 'https://example.invalid/kbvis/res', 'sjsu.edu', 'restricted', 'faculty', '{faculty}')
    RETURNING id INTO res_id;

  INSERT INTO public.document_chunks (document_id, chunk_index, heading, content) VALUES
    (pub_id,  0, 'Shuttle', 'The campus shuttle runs every fifteen minutes.'),
    (auth_id, 0, 'Shuttle', 'The campus shuttle schedule for enrolled students.'),
    (res_id,  0, 'Shuttle', 'The campus shuttle contract and internal costings.');
END $$;

-- RLS: anon sees only public, a signed-in user also sees authenticated, and
-- neither ever sees restricted.
select pg_temp.expect_rows('anon reads only public documents',
  $q$select 1 from public.documents where url like 'https://example.invalid/kbvis/%'$q$, 1, 'anon');
select pg_temp.expect_rows('alice reads public + authenticated documents',
  $q$select 1 from public.documents where url like 'https://example.invalid/kbvis/%'$q$, 2,
  'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect_rows('anon reads only public chunks',
  $q$select 1 from public.document_chunks where heading = 'Shuttle'$q$, 1, 'anon');
select pg_temp.expect_rows('alice reads public + authenticated chunks',
  $q$select 1 from public.document_chunks where heading = 'Shuttle'$q$, 2,
  'authenticated', '11111111-1111-1111-1111-111111111111');

-- The corpus is server-written. There is no insert/update/delete policy on
-- either table, so RLS denies every write no matter who asks.
select pg_temp.expect('anon inserts a document',
  $q$insert into public.documents (title, url) values ('forged', 'https://example.invalid/kbvis/forged')$q$,
  true, 'anon');
select pg_temp.expect('alice inserts a document',
  $q$insert into public.documents (title, url) values ('forged', 'https://example.invalid/kbvis/forged2')$q$,
  true, 'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect_rows('alice relabels a restricted document as public',
  $q$update public.documents set visibility = 'public' where url = 'https://example.invalid/kbvis/res'$q$, 0,
  'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect_rows('alice deletes a chunk',
  $q$delete from public.document_chunks where heading = 'Shuttle'$q$, 0,
  'authenticated', '11111111-1111-1111-1111-111111111111');

-- Operator state: RLS enabled with zero policies, so nobody but service_role
-- sees a row. These tables carry the crawl allowlist, so a user who could write
-- kb_sources could point the crawler anywhere -- the stored-SSRF shape that
-- 20260916000200 had to remove from job_sources.
select pg_temp.expect_rows('anon reads kb_sources',
  $q$select 1 from public.kb_sources$q$, 0, 'anon');
select pg_temp.expect_rows('alice reads kb_sources',
  $q$select 1 from public.kb_sources$q$, 0,
  'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect('alice inserts a crawl seed',
  $q$insert into public.kb_sources (url, collection) values ('http://169.254.169.254/', 'guest')$q$,
  true, 'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect_rows('alice reads kb_ingest_runs',
  $q$select 1 from public.kb_ingest_runs$q$, 0,
  'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect_rows('alice reads kb_ingest_jobs',
  $q$select 1 from public.kb_ingest_jobs$q$, 0,
  'authenticated', '11111111-1111-1111-1111-111111111111');

-- The boundary the chat path actually depends on. Run as postgres, because that
-- is the situation the backend is in: RLS bypassed, so the only thing standing
-- between a guest and an authenticated-only document is this parameter.
DO $$
DECLARE as_guest int; as_user int; restricted_leak int;
BEGIN
  SELECT count(*) INTO as_guest
    FROM public.search_kb_chunks('shuttle', 'guest', false, 10);
  SELECT count(*) INTO as_user
    FROM public.search_kb_chunks('shuttle', 'student', true, 10);
  SELECT count(*) INTO restricted_leak
    FROM public.search_kb_chunks('shuttle', 'faculty', true, 10) r
   WHERE r.url = 'https://example.invalid/kbvis/res';

  IF as_guest = 1 THEN
    RAISE NOTICE '  ok   search_kb_chunks(include_authenticated => false) returns public only';
  ELSE
    RAISE NOTICE '  FAIL guest view returned % rows, expected 1', as_guest;
  END IF;

  IF as_user = 2 THEN
    RAISE NOTICE '  ok   search_kb_chunks(include_authenticated => true) adds authenticated';
  ELSE
    RAISE NOTICE '  FAIL user view returned % rows, expected 2', as_user;
  END IF;

  -- restricted is reachable by nobody, through any parameter combination.
  IF restricted_leak = 0 THEN
    RAISE NOTICE '  ok   restricted documents are unreachable through search_kb_chunks';
  ELSE
    RAISE NOTICE '  FAIL a restricted document leaked into search_kb_chunks';
  END IF;
END $$;

-- match_kb_hybrid must enforce the same rule. Called with a null embedding, so
-- the vector arm drops out and this exercises the keyword arm plus the filter --
-- which is exactly the state the corpus is in before any embedding is written.
DO $$
DECLARE as_guest int; as_user int;
BEGIN
  SELECT count(*) INTO as_guest
    FROM public.match_kb_hybrid('shuttle', null, 'guest', false, 10, 60);
  SELECT count(*) INTO as_user
    FROM public.match_kb_hybrid('shuttle', null, 'student', true, 10, 60);
  IF as_guest = 1 AND as_user = 2 THEN
    RAISE NOTICE '  ok   match_kb_hybrid enforces the same visibility rule';
  ELSE
    RAISE NOTICE '  FAIL match_kb_hybrid visibility: guest=%, user=% (expected 1, 2)', as_guest, as_user;
  END IF;
END $$;

-- replace_document_chunks is the only write path, and it has to be atomic:
-- over PostgREST a delete followed by an insert is two requests, and a crash
-- between them leaves a document present, hashed and chunkless.
DO $$
DECLARE doc_id uuid; written int; final_count int;
BEGIN
  SELECT id INTO doc_id FROM public.documents WHERE url = 'https://example.invalid/kbvis/pub';

  SELECT public.replace_document_chunks(doc_id, $json$[
    {"chunk_index": 0, "heading": "Shuttle", "content": "Replaced chunk zero.", "token_count": 4},
    {"chunk_index": 1, "heading": "Shuttle", "content": "Replaced chunk one.", "token_count": 4}
  ]$json$::jsonb) INTO written;

  SELECT count(*) INTO final_count FROM public.document_chunks WHERE document_id = doc_id;

  IF written = 2 AND final_count = 2 THEN
    RAISE NOTICE '  ok   replace_document_chunks replaces rather than appends';
  ELSE
    RAISE NOTICE '  FAIL replace_document_chunks wrote %, left % rows', written, final_count;
  END IF;
END $$;

-- The generated tsv must track a replacement, or a re-ingested page stays
-- findable only by its old text.
DO $$
DECLARE hits int;
BEGIN
  SELECT count(*) INTO hits FROM public.search_kb_chunks('replaced', 'guest', false, 10);
  IF hits >= 1 THEN
    RAISE NOTICE '  ok   the generated tsv reflects replaced content';
  ELSE
    RAISE NOTICE '  FAIL replaced content is not searchable';
  END IF;
END $$;

\echo ''
\echo '=== 23. Campus snapshot tables: service_role only (20261001000100) ==='
-- Six tables of public SJSU data, none of it for the browser: the UI reads
-- through /api/registration/* and the backend uses the service key. So every
-- table gets the same assertions (anon and alice are refused, the service role
-- is not), plus the two foreign-key behaviours the refresh depends on: pruning
-- a snapshot takes its rows with it, and the snapshot readers are on cannot be
-- pruned at all.
--
-- Fixture ids: c...01/02 are two schedule snapshots for fall-2026 (01 is what
-- readers are on, 02 is staged behind it, which is the state just before a
-- flip). 03 registrar and 04 exams are superseded leftovers. 05 is a bursar
-- snapshot, which is Registration C fitting through source_key with no second
-- migration. 06 is the academic calendar, scoped to an academic year.

select pg_temp.expect('service_role inserts campus_snapshots',
  $q$insert into public.campus_snapshots (id, source_key, scope_key, source_url, fetched_from, page_last_updated, content_hash, row_count, header, status) values
     ('c0000000-0000-4000-8000-000000000001', 'schedule',  'fall-2026',    'https://www.sjsu.edu/classes/schedules/fall-2026.php', 'https://www.sjsu.edu/classes/schedules/fall-2026.php', '2026-09-30', 'h1', 3, '["Section","Class Number"]', 'current'),
     ('c0000000-0000-4000-8000-000000000002', 'schedule',  'fall-2026',    'https://www.sjsu.edu/classes/schedules/fall-2026.php', 'https://www.sjsu.edu/classes/schedules/fall-2026.php', '2026-10-01', 'h2', 4, '["Section","Class Number"]', 'staged'),
     ('c0000000-0000-4000-8000-000000000003', 'registrar', 'fall-2026',    'https://www.sjsu.edu/registrar/calendar/fall-2026.php', null, null, 'h3', 2, null, 'superseded'),
     ('c0000000-0000-4000-8000-000000000004', 'exams',     'fall-2026',    'https://www.sjsu.edu/classes/final-exam-schedule/fall-2026.php', null, null, 'h4', 2, null, 'superseded'),
     ('c0000000-0000-4000-8000-000000000005', 'bursar',    'fall-2026',    'https://www.sjsu.edu/bursar/fees-due-dates/payment-due-dates/fall.php', null, null, 'h5', 1, null, 'staged'),
     ('c0000000-0000-4000-8000-000000000006', 'academic',  'ay-2026-2027', 'https://www.sjsu.edu/classes/calendar/2026-2027.php', null, null, 'h6', 1, null, 'current')$q$,
  false, 'service_role');
select pg_temp.expect('service_role inserts campus_current',
  $q$insert into public.campus_current (source_key, scope_key, snapshot_id) values
     ('schedule', 'fall-2026',    'c0000000-0000-4000-8000-000000000001'),
     ('academic', 'ay-2026-2027', 'c0000000-0000-4000-8000-000000000006')$q$,
  false, 'service_role');
select pg_temp.expect('service_role inserts campus_refresh_runs',
  $q$insert into public.campus_refresh_runs (outcome, finished_at, stats) values ('success', now(), '{"schedule": {"rows": 3, "swapped": true}}')$q$,
  false, 'service_role');
-- The same class numbers under both schedule snapshots: unique per snapshot,
-- not globally. 40003 is a two-meeting section with a TBA second meeting, the
-- shape 473 rows of the Fall 2026 page have.
select pg_temp.expect('service_role inserts reg_class_sections',
  $q$insert into public.reg_class_sections (snapshot_id, class_number, course_key, section, satisfies_raw, satisfies, days, start_time, end_time, times_raw, meetings, meeting_count, instructor) values
     ('c0000000-0000-4000-8000-000000000001', 43104, 'PHYS 50',  '25', null,         '{}',      'TR', '13:30', '14:45', '1:30PM-2:45PM', '[{"days":"TR","start_time":"13:30","end_time":"14:45","location":"SCI 142","instructor":"Staff"}]', 1, 'Staff'),
     ('c0000000-0000-4000-8000-000000000001', 40002, 'PHIL 186', '01', 'GE: 5A+5C',  '{5A,5C}', 'MW', '09:00', '10:15', '9:00AM-10:15AM', '[{"days":"MW","start_time":"09:00","end_time":"10:15","location":"BBC 202","instructor":"A / A"}]', 1, 'A / A'),
     ('c0000000-0000-4000-8000-000000000001', 40003, 'ENGR 10',  '02', 'GE:3B+US23', '{3B,US23}', 'MW', '10:30', '11:45', '10:30AM-11:45AM<br>TBA', '[{"days":"MW","start_time":"10:30","end_time":"11:45","location":"ENG 189","instructor":"B"},{"days":"TBA","start_time":null,"end_time":null,"location":"ONLINE","instructor":"B"}]', 2, 'B / B'),
     ('c0000000-0000-4000-8000-000000000002', 43104, 'PHYS 50',  '25', null,         '{}',      'TR', '13:30', '14:45', '1:30PM-2:45PM', '[{"days":"TR","start_time":"13:30","end_time":"14:45","location":"SCI 142","instructor":"Staff"}]', 1, 'Staff'),
     ('c0000000-0000-4000-8000-000000000002', 40002, 'PHIL 186', '01', 'GE: 5A+5C',  '{5A,5C}', 'MW', '09:00', '10:15', '9:00AM-10:15AM', '[{"days":"MW","start_time":"09:00","end_time":"10:15","location":"BBC 202","instructor":"A / A"}]', 1, 'A / A'),
     ('c0000000-0000-4000-8000-000000000002', 40003, 'ENGR 10',  '02', 'GE:3B+US23', '{3B,US23}', 'MW', '10:30', '11:45', '10:30AM-11:45AM<br>TBA', '[{"days":"MW","start_time":"10:30","end_time":"11:45","location":"ENG 189","instructor":"B"},{"days":"TBA","start_time":null,"end_time":null,"location":"ONLINE","instructor":"B"}]', 2, 'B / B')$q$,
  false, 'service_role');
-- A December-to-January range is legal: the date-order check rejects only an
-- end before its start.
select pg_temp.expect('service_role inserts reg_term_events (registrar, payment, academic)',
  $q$insert into public.reg_term_events (snapshot_id, category, label_raw, date_raw, start_date, end_date, event_key) values
     ('c0000000-0000-4000-8000-000000000003', 'registrar', 'Last day to add classes', 'Tue, Sep. 15', '2026-09-15', null, 'last_day_to_add'),
     ('c0000000-0000-4000-8000-000000000003', 'registrar', 'Campus closed', 'Mon, Dec. 21 - Sun, Jan. 3', '2026-12-21', '2027-01-03', null),
     ('c0000000-0000-4000-8000-000000000005', 'payment',   'First installment due', 'Aug. 15', '2026-08-15', null, 'first_installment_due'),
     ('c0000000-0000-4000-8000-000000000006', 'academic',  'First day of instruction', 'Thu, Aug. 20', '2026-08-20', null, 'instruction_begins')$q$,
  false, 'service_role');
select pg_temp.expect('service_role inserts reg_exam_rules (a rule and an exception)',
  $q$insert into public.reg_exam_rules (snapshot_id, day_patterns, start_time_from, start_time_to, exam_date, exam_start, exam_end, is_exception, course_keys, note, raw) values
     ('c0000000-0000-4000-8000-000000000004', '{MW,MWF,M,W}', '09:00', '10:15', '2026-12-14', '09:45', '12:00', false, '{}', null, '{"cells": ["MW, MWF, M, W", "0900-1015", "Mon, Dec. 14", "0945-1200"]}'),
     ('c0000000-0000-4000-8000-000000000004', '{}', null, null, '2026-12-12', '08:00', '10:15', true, '{"MATH 19","MATH 30"}', 'Common final', '{"cells": ["MATH 19, 30", "Sat, Dec. 12", "0800-1015"]}')$q$,
  false, 'service_role');

-- anon and alice, against every table. SELECT must raise rather than return
-- zero rows: zero rows is also what RLS alone would produce, so only a raise
-- proves the revoke is there. RLS is tested on its own further down. Each
-- insert names valid values, so a refusal cannot come from a constraint.
DO $$
DECLARE t record;
BEGIN
  FOR t IN SELECT * FROM (VALUES
    ('campus_snapshots',
     $i$insert into public.campus_snapshots (source_key, scope_key, source_url, content_hash) values ('schedule', 'spring-2027', 'https://example.invalid/forged', 'x')$i$),
    ('campus_current',
     $i$insert into public.campus_current (source_key, scope_key, snapshot_id) values ('bursar', 'fall-2026', 'c0000000-0000-4000-8000-000000000005')$i$),
    ('campus_refresh_runs',
     $i$insert into public.campus_refresh_runs (outcome) values ('success')$i$),
    ('reg_class_sections',
     $i$insert into public.reg_class_sections (snapshot_id, class_number, course_key, meeting_count) values ('c0000000-0000-4000-8000-000000000002', 49999, 'CS 999', 1)$i$),
    ('reg_term_events',
     $i$insert into public.reg_term_events (snapshot_id, category, label_raw, date_raw) values ('c0000000-0000-4000-8000-000000000005', 'payment', 'Forged deadline', 'Mon, Jan. 4')$i$),
    ('reg_exam_rules',
     $i$insert into public.reg_exam_rules (snapshot_id, note) values ('c0000000-0000-4000-8000-000000000004', 'forged')$i$)
  ) AS v(tbl, ins)
  LOOP
    PERFORM pg_temp.expect('anon reads ' || t.tbl, 'select 1 from public.' || t.tbl, true, 'anon');
    PERFORM pg_temp.expect('alice reads ' || t.tbl, 'select 1 from public.' || t.tbl, true,
                           'authenticated', '11111111-1111-1111-1111-111111111111');
    PERFORM pg_temp.expect('anon inserts into ' || t.tbl, t.ins, true, 'anon');
    PERFORM pg_temp.expect('alice inserts into ' || t.tbl, t.ins, true,
                           'authenticated', '11111111-1111-1111-1111-111111111111');
  END LOOP;
END $$;

-- The writes that would cost the most: re-pointing every reader at a snapshot
-- of alice's choosing, and deleting the data they are on.
select pg_temp.expect_rows('alice re-points campus_current',
  $q$update public.campus_current set snapshot_id = 'c0000000-0000-4000-8000-000000000002' where source_key = 'schedule'$q$,
  0, 'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect_rows('alice deletes a campus snapshot',
  $q$delete from public.campus_snapshots where id = 'c0000000-0000-4000-8000-000000000002'$q$,
  0, 'authenticated', '11111111-1111-1111-1111-111111111111');
select pg_temp.expect_rows('anon deletes reg_term_events',
  $q$delete from public.reg_term_events$q$, 0, 'anon');

-- The outer wall, read from the catalog rather than inferred from a refusal.
-- has_table_privilege also counts what a role inherits through PUBLIC.
DO $$
DECLARE
  tbls text[] := array['campus_snapshots', 'campus_current', 'campus_refresh_runs',
                       'reg_class_sections', 'reg_term_events', 'reg_exam_rules'];
  missing text; leftover text; no_rls text; pols text;
BEGIN
  SELECT string_agg(t, ', ') INTO missing FROM unnest(tbls) t
   WHERE to_regclass('public.' || t) IS NULL;
  IF missing IS NOT NULL THEN
    RAISE NOTICE '  FAIL campus tables missing: %', missing;
    RETURN;
  END IF;

  SELECT string_agg(r || ':' || t || ':' || p, ', ') INTO leftover
    FROM unnest(array['anon', 'authenticated']) r,
         unnest(tbls) t,
         unnest(array['SELECT', 'INSERT', 'UPDATE', 'DELETE',
                      'TRUNCATE', 'REFERENCES', 'TRIGGER', 'MAINTAIN']) p
   WHERE has_table_privilege(r::name, 'public.' || t, p);
  IF leftover IS NULL THEN
    RAISE NOTICE '  ok   anon and authenticated hold no privilege on any campus table';
  ELSE RAISE NOTICE '  FAIL campus table privileges still granted: %', leftover; END IF;

  SELECT string_agg(t, ', ') INTO no_rls
    FROM unnest(tbls) t JOIN pg_class c ON c.oid = ('public.' || t)::regclass
   WHERE NOT c.relrowsecurity;
  IF no_rls IS NULL THEN RAISE NOTICE '  ok   RLS is enabled on all six campus tables';
  ELSE RAISE NOTICE '  FAIL RLS is off on: %', no_rls; END IF;

  SELECT string_agg(tablename || ':' || policyname, ', ') INTO pols
    FROM pg_policies WHERE schemaname = 'public' AND tablename = ANY (tbls);
  IF pols IS NULL THEN RAISE NOTICE '  ok   no campus table has a policy (service_role only)';
  ELSE RAISE NOTICE '  FAIL campus tables have policies: %', pols; END IF;
END $$;

-- The inner wall on its own. Restore the SELECT the revoke removed (section 12
-- sets the precedent for harness-local DDL) and RLS, with no policy to admit
-- anyone, must still hide every row. The tables are not empty, or zero rows
-- would prove nothing.
grant select on public.campus_snapshots, public.campus_current, public.campus_refresh_runs,
                public.reg_class_sections, public.reg_term_events, public.reg_exam_rules
  to anon, authenticated;
DO $$
DECLARE
  t text; total int; as_alice int; as_anon int;
  leaks text[] := '{}'; empties text[] := '{}';
BEGIN
  FOREACH t IN ARRAY array['campus_snapshots', 'campus_current', 'campus_refresh_runs',
                           'reg_class_sections', 'reg_term_events', 'reg_exam_rules'] LOOP
    EXECUTE format('select count(*) from public.%I', t) INTO total;
    IF total = 0 THEN empties := empties || t; END IF;
    PERFORM set_config('role', 'authenticated', true);
    PERFORM set_config('request.jwt.claim.sub', '11111111-1111-1111-1111-111111111111', true);
    PERFORM set_config('request.jwt.claim.role', 'authenticated', true);
    EXECUTE format('select count(*) from public.%I', t) INTO as_alice;
    PERFORM set_config('role', 'anon', true);
    EXECUTE format('select count(*) from public.%I', t) INTO as_anon;
    PERFORM set_config('role', 'postgres', true);
    IF as_alice + as_anon > 0 THEN
      leaks := leaks || format('%s (alice %s, anon %s of %s)', t, as_alice, as_anon, total);
    END IF;
  END LOOP;
  IF cardinality(empties) > 0 THEN
    RAISE NOTICE '  FAIL RLS check is vacuous, empty tables: %', array_to_string(empties, ', ');
  ELSIF cardinality(leaks) = 0 THEN
    RAISE NOTICE '  ok   with SELECT granted back, RLS alone still hides every campus row';
  ELSE
    RAISE NOTICE '  FAIL RLS let rows through: %', array_to_string(leaks, '; ');
  END IF;
EXCEPTION WHEN others THEN
  PERFORM set_config('role', 'postgres', true);
  RAISE NOTICE '  FAIL RLS wall check raised: %', substr(sqlerrm, 1, 65);
END $$;
revoke select on public.campus_snapshots, public.campus_current, public.campus_refresh_runs,
                 public.reg_class_sections, public.reg_term_events, public.reg_exam_rules
  from anon, authenticated;

-- Class search and the cascade both depend on these indexes. Without them
-- everything still works and silently scans 7,000 rows per snapshot.
DO $$
DECLARE missing text[] := '{}'; t text;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname = 'public' AND tablename = 'reg_class_sections'
                    AND indexdef LIKE 'CREATE UNIQUE INDEX % USING btree (snapshot_id, class_number)')
    THEN missing := missing || 'unique (snapshot_id, class_number)'::text; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname = 'public' AND tablename = 'reg_class_sections'
                    AND indexdef LIKE '% USING gin (satisfies)')
    THEN missing := missing || 'gin (satisfies)'::text; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname = 'public' AND tablename = 'reg_class_sections'
                    AND indexdef LIKE '% USING btree (snapshot_id, course_key%')
    THEN missing := missing || '(snapshot_id, course_key)'::text; END IF;
  FOREACH t IN ARRAY array['reg_term_events', 'reg_exam_rules'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname = 'public' AND tablename = t
                      AND indexdef LIKE '% USING btree (snapshot_id%')
      THEN missing := missing || (t || ' (snapshot_id ...)'); END IF;
  END LOOP;
  IF cardinality(missing) = 0 THEN
    RAISE NOTICE '  ok   campus indexes: unique class number per snapshot, GIN satisfies, snapshot_id on every child';
  ELSE RAISE NOTICE '  FAIL campus indexes missing: %', array_to_string(missing, ', '); END IF;
END $$;

-- What class search will send for ge=5C. PostgREST's `satisfies=cs.{5C}` is
-- `satisfies @> '{5C}'`; a cell naming two areas matches either.
select pg_temp.expect_rows('service_role finds a section by one area of a combined GE cell',
  $q$select 1 from public.reg_class_sections where snapshot_id = 'c0000000-0000-4000-8000-000000000002' and satisfies @> '{5C}'$q$,
  1, 'service_role');
-- Nothing constrains what a tag may say: the vocabulary belongs to the parser.
select pg_temp.expect('satisfies accepts tags in any spelling',
  $q$insert into public.reg_class_sections (snapshot_id, class_number, course_key, satisfies_raw, satisfies, meeting_count) values ('c0000000-0000-4000-8000-000000000002', 40004, 'KIN 1', 'GE: 1Bor4', '{"GE: 1Bor4","PE: PhysEd","AI: US1",GWAR,WID}', 1)$q$,
  false, 'service_role');

-- Constraints bind the service role too. They are what stop a refresh bug from
-- writing rows that no reader will ever look up, or that answer wrongly.
select pg_temp.expect('an unknown source_key is rejected',
  $q$insert into public.campus_snapshots (source_key, scope_key, source_url, content_hash) values ('catalog', 'fall-2026', 'https://example.invalid/x', 'x')$q$,
  true, 'service_role');
select pg_temp.expect('a two-digit year scope_key is rejected (fall-26)',
  $q$insert into public.campus_snapshots (source_key, scope_key, source_url, content_hash) values ('schedule', 'fall-26', 'https://example.invalid/x', 'x')$q$,
  true, 'service_role');
select pg_temp.expect('a display-form scope_key is rejected (Fall 2026)',
  $q$insert into public.campus_snapshots (source_key, scope_key, source_url, content_hash) values ('schedule', 'Fall 2026', 'https://example.invalid/x', 'x')$q$,
  true, 'service_role');
select pg_temp.expect('an unknown snapshot status is rejected',
  $q$insert into public.campus_snapshots (source_key, scope_key, source_url, content_hash, status) values ('schedule', 'winter-2027', 'https://example.invalid/x', 'x', 'live')$q$,
  true, 'service_role');
select pg_temp.expect('an unknown refresh outcome is rejected',
  $q$insert into public.campus_refresh_runs (outcome) values ('ok')$q$,
  true, 'service_role');
-- The page's own duplicate (PHYS 50 section 25). The parser drops exact
-- duplicates; one that gets this far is a parser bug and must fail the batch.
select pg_temp.expect('a duplicate class number within one snapshot is rejected (43104)',
  $q$insert into public.reg_class_sections (snapshot_id, class_number, course_key, section, meeting_count) values ('c0000000-0000-4000-8000-000000000002', 43104, 'PHYS 50', '25', 1)$q$,
  true, 'service_role');
-- A count of 1 against two meetings would let the final-exam lookup compute a
-- slot from the first pattern alone, which is exactly the guess it must refuse.
select pg_temp.expect('a meeting_count that disagrees with meetings is rejected',
  $q$insert into public.reg_class_sections (snapshot_id, class_number, course_key, meetings, meeting_count) values ('c0000000-0000-4000-8000-000000000002', 40005, 'ENGR 10', '[{"days":"MW"},{"days":"TBA"}]', 1)$q$,
  true, 'service_role');
select pg_temp.expect('a meetings value that is not an array is rejected',
  $q$insert into public.reg_class_sections (snapshot_id, class_number, course_key, meetings, meeting_count) values ('c0000000-0000-4000-8000-000000000002', 40006, 'ENGR 10', '{"days":"MW"}', 1)$q$,
  true, 'service_role');
select pg_temp.expect('an unknown event category is rejected',
  $q$insert into public.reg_term_events (snapshot_id, category, label_raw, date_raw) values ('c0000000-0000-4000-8000-000000000003', 'parking', 'x', 'x')$q$,
  true, 'service_role');
-- 'Dec. 21 - Jan. 3' with both years inferred as 2026: the wrong-deadline bug.
select pg_temp.expect('an event that ends before it starts is rejected',
  $q$insert into public.reg_term_events (snapshot_id, category, label_raw, date_raw, start_date, end_date) values ('c0000000-0000-4000-8000-000000000003', 'registrar', 'Campus closed', 'Mon, Dec. 21 - Sun, Jan. 3', '2026-12-21', '2026-01-03')$q$,
  true, 'service_role');

-- campus_current's ON DELETE RESTRICT. A prune whose filter sweeps the snapshot
-- readers are on is refused as a whole statement, so it deletes nothing at all,
-- including the staged snapshot it also matched.
select pg_temp.expect('a prune that sweeps the current snapshot is refused',
  $q$delete from public.campus_snapshots where source_key = 'schedule' and scope_key = 'fall-2026'$q$,
  true, 'service_role');
select pg_temp.expect_rows('the refused prune deleted nothing',
  $q$select 1 from public.campus_snapshots where source_key = 'schedule' and scope_key = 'fall-2026'$q$,
  2, 'service_role');
select pg_temp.expect('the current snapshot cannot be deleted by id',
  $q$delete from public.campus_snapshots where id = 'c0000000-0000-4000-8000-000000000001'$q$,
  true, 'service_role');
-- The composite key: the schedule pointer can only name a schedule snapshot
-- for the same term.
select pg_temp.expect('campus_current cannot point schedule/fall-2026 at the bursar snapshot',
  $q$update public.campus_current set snapshot_id = 'c0000000-0000-4000-8000-000000000005' where source_key = 'schedule' and scope_key = 'fall-2026'$q$,
  true, 'service_role');

-- The refresh's own sequence, as the service role: flip, then bookkeeping,
-- then prune.
select pg_temp.expect_rows('service_role flips schedule/fall-2026 to the staged snapshot',
  $q$update public.campus_current set snapshot_id = 'c0000000-0000-4000-8000-000000000002', verified_at = now() where source_key = 'schedule' and scope_key = 'fall-2026'$q$,
  1, 'service_role');
select pg_temp.expect_rows('service_role marks the old snapshot superseded and the new one current',
  $q$update public.campus_snapshots set status = case when id = 'c0000000-0000-4000-8000-000000000001' then 'superseded' else 'current' end where id in ('c0000000-0000-4000-8000-000000000001', 'c0000000-0000-4000-8000-000000000002')$q$,
  2, 'service_role');

-- The cascade. The prune excludes the ids campus_current names, which is the
-- shape the refresh needs: `status` alone is bookkeeping and can lag the pointer.
DO $$
DECLARE
  gone uuid[] := array['c0000000-0000-4000-8000-000000000001',
                       'c0000000-0000-4000-8000-000000000003',
                       'c0000000-0000-4000-8000-000000000004']::uuid[];
  pruned int;
  sec_before int; evt_before int; rule_before int;
  sec_after int;  evt_after int;  rule_after int;
  sec_kept_before int; evt_kept_before int;
  sec_kept_after int;  evt_kept_after int;
BEGIN
  SELECT count(*) INTO sec_before  FROM public.reg_class_sections WHERE snapshot_id = ANY (gone);
  SELECT count(*) INTO evt_before  FROM public.reg_term_events    WHERE snapshot_id = ANY (gone);
  SELECT count(*) INTO rule_before FROM public.reg_exam_rules     WHERE snapshot_id = ANY (gone);
  SELECT count(*) INTO sec_kept_before FROM public.reg_class_sections WHERE NOT snapshot_id = ANY (gone);
  SELECT count(*) INTO evt_kept_before FROM public.reg_term_events    WHERE NOT snapshot_id = ANY (gone);

  PERFORM set_config('role', 'service_role', true);
  DELETE FROM public.campus_snapshots
   WHERE status = 'superseded'
     AND id NOT IN (SELECT snapshot_id FROM public.campus_current);
  GET DIAGNOSTICS pruned = ROW_COUNT;
  PERFORM set_config('role', 'postgres', true);

  SELECT count(*) INTO sec_after  FROM public.reg_class_sections WHERE snapshot_id = ANY (gone);
  SELECT count(*) INTO evt_after  FROM public.reg_term_events    WHERE snapshot_id = ANY (gone);
  SELECT count(*) INTO rule_after FROM public.reg_exam_rules     WHERE snapshot_id = ANY (gone);
  SELECT count(*) INTO sec_kept_after FROM public.reg_class_sections WHERE NOT snapshot_id = ANY (gone);
  SELECT count(*) INTO evt_kept_after FROM public.reg_term_events    WHERE NOT snapshot_id = ANY (gone);

  IF pruned = 3 THEN RAISE NOTICE '  ok   service_role prunes the 3 superseded snapshots';
  ELSE RAISE NOTICE '  FAIL prune deleted % snapshots, expected 3', pruned; END IF;

  IF sec_before > 0 AND sec_after = 0 AND sec_kept_after = sec_kept_before THEN
    RAISE NOTICE '  ok   pruning cascades to reg_class_sections (% gone, % kept)', sec_before, sec_kept_after;
  ELSE RAISE NOTICE '  FAIL reg_class_sections cascade: % -> % pruned rows, % -> % kept rows',
         sec_before, sec_after, sec_kept_before, sec_kept_after; END IF;

  IF evt_before > 0 AND evt_after = 0 AND evt_kept_after = evt_kept_before THEN
    RAISE NOTICE '  ok   pruning cascades to reg_term_events (% gone, % kept)', evt_before, evt_kept_after;
  ELSE RAISE NOTICE '  FAIL reg_term_events cascade: % -> % pruned rows, % -> % kept rows',
         evt_before, evt_after, evt_kept_before, evt_kept_after; END IF;

  IF rule_before > 0 AND rule_after = 0 THEN
    RAISE NOTICE '  ok   pruning cascades to reg_exam_rules (% gone)', rule_before;
  ELSE RAISE NOTICE '  FAIL reg_exam_rules cascade: % -> % rows', rule_before, rule_after; END IF;
EXCEPTION WHEN others THEN
  PERFORM set_config('role', 'postgres', true);
  RAISE NOTICE '  FAIL the prune raised: %', substr(sqlerrm, 1, 65);
END $$;

-- And the protection follows the pointer: the snapshot readers are on now is
-- the one that can't be deleted.
select pg_temp.expect('the newly current snapshot cannot be deleted',
  $q$delete from public.campus_snapshots where id = 'c0000000-0000-4000-8000-000000000002'$q$,
  true, 'service_role');

\echo ''
\echo '=== 24. reg_class_sections.meeting_count: no default, at least 1 (20261002000100) ==='
-- The final-exam lookup refuses a multi-pattern section on meeting_count alone,
-- so the count has to be one the producer stated. 20261001000100 defaulted it to
-- 1, which let a two-meeting section with an unfilled `meetings` array pass as a
-- one-meeting section. Every refusal below names the constraint or column it
-- expects, so none can pass on a missing fixture row or on a different check.
--
-- Everything here runs as service_role, the only role that can write the table.
-- That no grant changed is section 23's catalog check, which runs against the
-- schema after every migration, this one included.
--
-- Fixture: c...07 is a staged spring-2027 schedule snapshot of its own, so these
-- rows stay out of section 23's counts.

select pg_temp.expect('service_role inserts a staged spring-2027 schedule snapshot',
  $q$insert into public.campus_snapshots (id, source_key, scope_key, source_url, content_hash) values ('c0000000-0000-4000-8000-000000000007', 'schedule', 'spring-2027', 'https://www.sjsu.edu/classes/schedules/spring-2027.php', 'h7')$q$,
  false, 'service_role');

-- The catalog, so that a NOT VALID constraint or a different bound shows up by
-- name rather than only through whichever values the tests below happen to try.
DO $$
DECLARE not_null boolean; has_def boolean; def text; con_def text; con_valid boolean;
BEGIN
  SELECT a.attnotnull, a.atthasdef, pg_get_expr(d.adbin, d.adrelid)
    INTO not_null, has_def, def
    FROM pg_attribute a
    LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
   WHERE a.attrelid = 'public.reg_class_sections'::regclass
     AND a.attname = 'meeting_count' AND NOT a.attisdropped;
  IF not_null AND NOT has_def THEN
    RAISE NOTICE '  ok   meeting_count is NOT NULL with no default';
  ELSE
    RAISE NOTICE '  FAIL meeting_count: not null %, default %', not_null, coalesce(def, 'none');
  END IF;

  SELECT pg_get_constraintdef(c.oid), c.convalidated INTO con_def, con_valid
    FROM pg_constraint c
   WHERE c.conrelid = 'public.reg_class_sections'::regclass
     AND c.conname = 'reg_class_sections_meeting_count_check' AND c.contype = 'c';
  IF con_def = 'CHECK ((meeting_count >= 1))' AND con_valid THEN
    RAISE NOTICE '  ok   reg_class_sections_meeting_count_check is CHECK (meeting_count >= 1), validated';
  ELSE
    RAISE NOTICE '  FAIL reg_class_sections_meeting_count_check: %, validated %',
      coalesce(con_def, 'missing'), con_valid;
  END IF;
END $$;

-- The finding itself: a two-pattern section whose producer filled neither the
-- array nor the count. Under 20261001000100 this stored meeting_count = 1.
select pg_temp.expect_violation('an insert that omits meeting_count is rejected (meetings left as [])',
  $q$insert into public.reg_class_sections (snapshot_id, class_number, course_key, days, start_time, end_time, times_raw, meetings) values ('c0000000-0000-4000-8000-000000000007', 41001, 'ENGR 10', 'MW', '10:30', '11:45', '10:30AM-11:45AM<br>TBA', '[]')$q$,
  '23502', 'meeting_count', 'service_role');
-- Even where 1 would have been right: the producer always states the count.
select pg_temp.expect_violation('an insert that omits meeting_count is rejected (one meeting filled in)',
  $q$insert into public.reg_class_sections (snapshot_id, class_number, course_key, meetings) values ('c0000000-0000-4000-8000-000000000007', 41002, 'PHYS 50', '[{"days":"TR","start_time":"13:30","end_time":"14:45"}]')$q$,
  '23502', 'meeting_count', 'service_role');
-- What PostgREST sends for a batch object that lacks the key.
select pg_temp.expect_violation('an explicit null meeting_count is rejected',
  $q$insert into public.reg_class_sections (snapshot_id, class_number, course_key, meetings, meeting_count) values ('c0000000-0000-4000-8000-000000000007', 41003, 'ENGR 10', '[]', null)$q$,
  '23502', 'meeting_count', 'service_role');
-- 0 and below. Against [] the consistency check accepts any count, so only the
-- new constraint can refuse these.
select pg_temp.expect_violation('meeting_count = 0 is rejected',
  $q$insert into public.reg_class_sections (snapshot_id, class_number, course_key, meetings, meeting_count) values ('c0000000-0000-4000-8000-000000000007', 41004, 'ENGR 10', '[]', 0)$q$,
  '23514', 'reg_class_sections_meeting_count_check', 'service_role');
select pg_temp.expect_violation('a negative meeting_count is rejected (-1)',
  $q$insert into public.reg_class_sections (snapshot_id, class_number, course_key, meetings, meeting_count) values ('c0000000-0000-4000-8000-000000000007', 41005, 'ENGR 10', '[]', -1)$q$,
  '23514', 'reg_class_sections_meeting_count_check', 'service_role');

-- What a producer that states the count still writes. 41012 is the fully
-- online shape (Days and Times both 'TBA'), the row a count of 0 would
-- otherwise describe. 41014 is a producer that states the count but not the
-- array; the lookup can refuse it on the count alone.
select pg_temp.expect_rows('a one-meeting section with meeting_count = 1 is stored',
  $q$insert into public.reg_class_sections (snapshot_id, class_number, course_key, days, start_time, end_time, meetings, meeting_count) values ('c0000000-0000-4000-8000-000000000007', 41011, 'PHYS 50', 'TR', '13:30', '14:45', '[{"days":"TR","start_time":"13:30","end_time":"14:45","location":"SCI 142","instructor":"Staff"}]', 1)$q$,
  1, 'service_role');
select pg_temp.expect_rows('a fully online TBA section with meeting_count = 1 is stored',
  $q$insert into public.reg_class_sections (snapshot_id, class_number, course_key, mode, days, times_raw, meetings, meeting_count) values ('c0000000-0000-4000-8000-000000000007', 41012, 'AAS 1', 'Fully Online', 'TBA', 'TBA', '[{"days":"TBA","start_time":null,"end_time":null,"location":"ONLINE","instructor":"Staff"}]', 1)$q$,
  1, 'service_role');
select pg_temp.expect_rows('a two-meeting section with meeting_count = 2 is stored',
  $q$insert into public.reg_class_sections (snapshot_id, class_number, course_key, days, start_time, end_time, meetings, meeting_count) values ('c0000000-0000-4000-8000-000000000007', 41013, 'ENGR 10', 'MW', '10:30', '11:45', '[{"days":"MW","start_time":"10:30","end_time":"11:45","location":"ENG 189","instructor":"B"},{"days":"TBA","start_time":null,"end_time":null,"location":"ONLINE","instructor":"B"}]', 2)$q$,
  1, 'service_role');
select pg_temp.expect_rows('meeting_count = 2 with meetings left as [] is stored (the count alone)',
  $q$insert into public.reg_class_sections (snapshot_id, class_number, course_key, days, start_time, end_time, meetings, meeting_count) values ('c0000000-0000-4000-8000-000000000007', 41014, 'ME 30', 'MW', '09:00', '10:15', '[]', 2)$q$,
  1, 'service_role');

-- The consistency rule from 20261001000100 still holds: a filled-in array and
-- the count must agree, and `meetings` must be an array.
select pg_temp.expect_violation('meeting_count = 1 against two meetings is still rejected',
  $q$insert into public.reg_class_sections (snapshot_id, class_number, course_key, meetings, meeting_count) values ('c0000000-0000-4000-8000-000000000007', 41021, 'ENGR 10', '[{"days":"MW"},{"days":"TBA"}]', 1)$q$,
  '23514', 'reg_class_sections_meetings_check', 'service_role');
select pg_temp.expect_violation('meeting_count = 3 against two meetings is still rejected',
  $q$insert into public.reg_class_sections (snapshot_id, class_number, course_key, meetings, meeting_count) values ('c0000000-0000-4000-8000-000000000007', 41022, 'ENGR 10', '[{"days":"MW"},{"days":"TBA"}]', 3)$q$,
  '23514', 'reg_class_sections_meetings_check', 'service_role');
select pg_temp.expect_violation('meeting_count = 2 against one meeting is still rejected',
  $q$insert into public.reg_class_sections (snapshot_id, class_number, course_key, meetings, meeting_count) values ('c0000000-0000-4000-8000-000000000007', 41023, 'PHYS 50', '[{"days":"TR"}]', 2)$q$,
  '23514', 'reg_class_sections_meetings_check', 'service_role');
select pg_temp.expect_violation('a meetings value that is not an array is still rejected',
  $q$insert into public.reg_class_sections (snapshot_id, class_number, course_key, meetings, meeting_count) values ('c0000000-0000-4000-8000-000000000007', 41024, 'ENGR 10', '{"days":"MW"}', 1)$q$,
  '23514', 'reg_class_sections_meetings_check', 'service_role');

-- The update path: an upsert's DO UPDATE, or a repair script. `= default` is
-- null now that there is no default, so it cannot quietly restore a 1.
select pg_temp.expect_violation('setting meeting_count to DEFAULT is rejected (there is none)',
  $q$update public.reg_class_sections set meeting_count = default where snapshot_id = 'c0000000-0000-4000-8000-000000000007' and class_number = 41011$q$,
  '23502', 'meeting_count', 'service_role');
select pg_temp.expect_violation('updating meeting_count to 0 is rejected',
  $q$update public.reg_class_sections set meeting_count = 0 where snapshot_id = 'c0000000-0000-4000-8000-000000000007' and class_number = 41014$q$,
  '23514', 'reg_class_sections_meeting_count_check', 'service_role');

-- Nothing rejected above was stored, and nothing stored changed its count.
select pg_temp.expect_rows('snapshot c...07 holds exactly the four valid sections',
  $q$select 1 from public.reg_class_sections where snapshot_id = 'c0000000-0000-4000-8000-000000000007'$q$,
  4, 'service_role');
select pg_temp.expect_rows('each stored section kept the count its producer stated',
  $q$select 1 from public.reg_class_sections where snapshot_id = 'c0000000-0000-4000-8000-000000000007' and (class_number, meeting_count) in ((41011, 1), (41012, 1), (41013, 2), (41014, 2))$q$,
  4, 'service_role');
