-- Audience model: who a person says they are, and what has been verified.
--
-- The app is being widened from a student-only assistant to one serving
-- students, alumni, guests and faculty. Three concepts are kept deliberately
-- apart, because collapsing them is how "pick Faculty from a dropdown" turns
-- into real access:
--
--   * profiles.active_audience -- personalization only. The user picks it, the
--     user changes it, and it grants nothing.
--   * user_affiliations        -- a claim about a relationship to the
--     university. Users DECLARE; only the service role verifies.
--   * conversations.audience   -- a snapshot of which audience a conversation
--     was held under, so history stays readable after a switch.
--
-- Nothing in the application reads these yet. Phase 2 (onboarding, the audience
-- switcher, per-audience prompts and source collections) is what consumes them;
-- this migration exists first so that work does not also have to land schema.

-- 1. Enumerations ------------------------------------------------------------

-- There is no `create type if not exists`. The exception class is narrow on
-- purpose: 001_initial_schema.sql:73 wraps its trigger creation in `when
-- others`, which is how a missing enforce_sjsu_domain trigger went unnoticed
-- for months. Catch only the case actually being tolerated.
do $$
begin
  create type public.affiliation_kind as enum (
    'student', 'alumni', 'faculty', 'staff', 'applicant', 'community'
  );
exception when duplicate_object then null;
end $$;

do $$
begin
  create type public.verification_status as enum (
    'declared', 'pending', 'verified', 'expired', 'revoked'
  );
exception when duplicate_object then null;
end $$;

-- 2. profiles: personalization columns ---------------------------------------

-- text + check rather than an enum, unlike the two above. This is a UI-facing
-- personalization field that will gain values, and `alter type ... add value`
-- replays awkwardly; profiles.role (001:85) sets the precedent on this table.
-- The security-relevant closed sets get enums; this one does not.
--
-- `not null default` is a metadata-only change on PostgreSQL 11+, so existing
-- rows read as 'student' without a table rewrite and without a backfill.
alter table public.profiles
  add column if not exists active_audience text not null default 'student',
  add column if not exists onboarded_at timestamptz;

-- `add constraint if not exists` does not exist; drop-then-add is the same
-- idiom used for policies and triggers elsewhere in this set.
alter table public.profiles
  drop constraint if exists profiles_active_audience_check;
alter table public.profiles
  add constraint profiles_active_audience_check
  check (active_audience in ('student', 'alumni', 'guest', 'faculty'));

-- 3. user_affiliations -------------------------------------------------------

-- One person may hold several at once: an alum who now teaches is both.
create table if not exists public.user_affiliations (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  affiliation public.affiliation_kind not null,
  status      public.verification_status not null default 'declared',
  source      text,
  verified_at timestamptz,
  expires_at  timestamptz,
  created_at  timestamptz not null default now(),
  constraint uq_user_affiliations_user_affiliation unique (user_id, affiliation)
);

alter table public.user_affiliations enable row level security;

-- Two independent walls, and both are wanted.
--
-- The column grants mean a user cannot NAME status, verified_at or expires_at
-- in a statement at all, so the column default applies on insert and the value
-- is untouchable on update. PostgREST turns that into a clean 403.
--
-- The policies below repeat the rule, so it survives someone later restoring a
-- table-wide grant from the dashboard or a careless migration.
revoke all on public.user_affiliations from anon;
revoke all on public.user_affiliations from authenticated;
grant select on public.user_affiliations to authenticated;
grant insert (user_id, affiliation, source) on public.user_affiliations to authenticated;
grant update (affiliation, source) on public.user_affiliations to authenticated;
grant delete on public.user_affiliations to authenticated;

drop policy if exists "user_affiliations_select_own" on public.user_affiliations;
create policy "user_affiliations_select_own"
  on public.user_affiliations
  for select
  to authenticated
  using (user_id = auth.uid());

drop policy if exists "user_affiliations_insert_own_declared" on public.user_affiliations;
create policy "user_affiliations_insert_own_declared"
  on public.user_affiliations
  for insert
  to authenticated
  with check (user_id = auth.uid() and status = 'declared');

-- `status = 'declared'` appears in USING as well as WITH CHECK, and that is
-- load-bearing rather than belt-and-braces.
--
-- With only WITH CHECK, a user whose 'student' row had been VERIFIED by staff
-- could run `update ... set affiliation = 'faculty'` on it: status is unchanged
-- so the check passes, and they would then hold a verified faculty affiliation.
-- USING is what makes an already-verified row invisible to the update.
drop policy if exists "user_affiliations_update_own_declared" on public.user_affiliations;
create policy "user_affiliations_update_own_declared"
  on public.user_affiliations
  for update
  to authenticated
  using (user_id = auth.uid() and status = 'declared')
  with check (user_id = auth.uid() and status = 'declared');

-- Same reasoning for delete: without the status predicate a user could shed a
-- 'revoked' or 'expired' marker by deleting the row and declaring a fresh one.
drop policy if exists "user_affiliations_delete_own_declared" on public.user_affiliations;
create policy "user_affiliations_delete_own_declared"
  on public.user_affiliations
  for delete
  to authenticated
  using (user_id = auth.uid() and status = 'declared');

-- No policy grants status, verified_at or expires_at. Verification is a
-- service_role write, behind a capability, and never a user action.

create index if not exists idx_user_affiliations_user
  on public.user_affiliations (user_id);

-- 4. profile_audience_details ------------------------------------------------

-- One jsonb table rather than a dozen more nullable columns on profiles: the
-- per-audience fields differ completely (an alum has a graduation year and an
-- employer, a faculty member has a department and a research area) and would
-- otherwise be mostly-null columns that every audience has to ignore.
create table if not exists public.profile_audience_details (
  user_id    uuid not null references auth.users(id) on delete cascade,
  audience   text not null,
  details    jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  primary key (user_id, audience),
  constraint profile_audience_details_audience_check
    check (audience in ('student', 'alumni', 'guest', 'faculty')),
  -- This blob is user-written and Phase 2 feeds it into the model's prompt.
  -- Bound it here rather than trusting every future caller to. details::text
  -- is immutable and so legal in a check; pg_column_size is not.
  constraint profile_audience_details_size_check
    check (length(details::text) <= 8192)
);

alter table public.profile_audience_details enable row level security;

revoke all on public.profile_audience_details from anon;
revoke all on public.profile_audience_details from authenticated;
grant select, insert, update, delete on public.profile_audience_details to authenticated;

drop policy if exists "profile_audience_details_select_own" on public.profile_audience_details;
create policy "profile_audience_details_select_own"
  on public.profile_audience_details
  for select
  to authenticated
  using (user_id = auth.uid());

drop policy if exists "profile_audience_details_insert_own" on public.profile_audience_details;
create policy "profile_audience_details_insert_own"
  on public.profile_audience_details
  for insert
  to authenticated
  with check (user_id = auth.uid());

-- WITH CHECK as well as USING, per 20260916000400: without it a user can update
-- a row they own and reassign user_id to someone else on the way out.
drop policy if exists "profile_audience_details_update_own" on public.profile_audience_details;
create policy "profile_audience_details_update_own"
  on public.profile_audience_details
  for update
  to authenticated
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

drop policy if exists "profile_audience_details_delete_own" on public.profile_audience_details;
create policy "profile_audience_details_delete_own"
  on public.profile_audience_details
  for delete
  to authenticated
  using (user_id = auth.uid());

-- Reuses public.handle_updated_at() from 001_initial_schema.sql:29-35.
drop trigger if exists on_profile_audience_details_updated on public.profile_audience_details;
create trigger on_profile_audience_details_updated
  before update on public.profile_audience_details
  for each row execute function public.handle_updated_at();

-- 5. conversations.audience --------------------------------------------------

-- Nullable, because every conversation that already exists predates audiences
-- and there is no honest value to backfill.
--
-- NOTE: nothing writes this column yet. The write belongs with the audience
-- switcher in Phase 2. It is also invisible to the sidebar until it is added to
-- the two explicit select lists, in UI/src/services/chatService.js
-- (fetchConversations) and UI/src/services/projectService.js
-- (fetchProjectConversations) -- neither uses `select('*')`.
alter table public.conversations
  add column if not exists audience text;

alter table public.conversations
  drop constraint if exists conversations_audience_check;
alter table public.conversations
  add constraint conversations_audience_check
  check (audience is null or audience in ('student', 'alumni', 'guest', 'faculty'));

-- 6. Backfill: declared, never verified --------------------------------------

-- Every existing account gets a declared student affiliation, NOT a verified
-- one -- even though every address is @sjsu.edu today.
--
-- The domain restriction is not evidence: enforce_sjsu_email (001:58-76) is
-- created inside `do ... exception when others ... raise notice`, so it may
-- never have been created at all, and it was `before insert` only, blind to an
-- email UPDATE. A client-side gate is not proof of anything either.
--
-- Verification is a deliberate act by someone with the capability, and this
-- migration is not that someone.
insert into public.user_affiliations (user_id, affiliation, status, source)
select p.id,
       'student'::public.affiliation_kind,
       'declared'::public.verification_status,
       'legacy_backfill'
  from public.profiles p
-- Not the `on conflict (user_id)` that backend/tests/test_migrations.py bans:
-- that check is a literal substring match against the shape that broke signup
-- in 20260407030000. This names the (user_id, affiliation) constraint created
-- above, which nothing later drops.
on conflict (user_id, affiliation) do nothing;

-- 7. profiles.role census ----------------------------------------------------

-- BUILD_PLAN's instruction is to emit the list rather than convert it. Because
-- profiles.role has been client-writable since day one (001:85-88 is
-- row-scoped but not column-scoped), no value in it is trustworthy -- including
-- 'advisor' and 'admin'. Grants are seeded by hand, after review, into the
-- admin_grants table created by the next migration.
--
-- Notices reach the verification harness output, which is why this is a notice
-- and not a table.
do $$
declare
  row_ record;
begin
  raise notice 'profiles.role census -- client-writable since day one, so these are claims, not facts:';
  for row_ in
    select role, count(*) as n from public.profiles group by role order by role
  loop
    raise notice '  role=% count=%', row_.role, row_.n;
  end loop;
  raise notice 'No grants are seeded from these. Seed admin_grants by hand after review.';
end $$;
