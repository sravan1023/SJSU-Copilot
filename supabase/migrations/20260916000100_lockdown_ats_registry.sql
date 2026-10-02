-- Security hardening: lock down public.ats_registry.
--
-- 20260410_ats_registry.sql created this table but never enabled row level
-- security. Supabase grants anon and authenticated full DML on new tables in
-- the public schema, so with RLS off any holder of the anon key -- which ships
-- in the browser bundle (UI/src/supabaseClient.js) -- could read, insert,
-- update, or delete rows without authenticating at all.
--
-- backend/services/job_fetcher.py reads this table to decide which ATS boards
-- to probe, so a writable registry is an injection point into a pipeline that
-- runs with the service role.
--
-- The fetcher connects as service_role, which bypasses RLS, so it is unaffected
-- by the policies below.

alter table public.ats_registry enable row level security;

-- Defence in depth: RLS alone would deny the writes, but the default grants
-- should not be there either.
revoke all on public.ats_registry from anon;
revoke all on public.ats_registry from authenticated;
grant select on public.ats_registry to authenticated;

drop policy if exists "ats_registry_select_authenticated" on public.ats_registry;
create policy "ats_registry_select_authenticated"
  on public.ats_registry
  for select
  to authenticated
  using (true);

-- No insert/update/delete policy: writes are service_role only.
