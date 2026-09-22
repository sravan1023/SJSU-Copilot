-- Move authority off public.profiles and onto a table users cannot write.
--
-- 001_initial_schema.sql:85-88 scopes the profiles UPDATE policy to the owner's
-- row but not to particular columns, so any signed-in user can set their own
-- profiles.role to 'admin'. The blast radius has been zero only because nothing
-- reads role -- which is exactly the problem: the moment any policy or endpoint
-- gates on it, a self-grant made months ago becomes real.
--
-- The structural fix is not to make role harder to write. It is to stop
-- treating role as authority at all. Capabilities move to admin_grants, which
-- has no write policy whatsoever, and role becomes a frozen legacy display
-- field. supabase/tests/verify_policies.sql section 12 has asserted the
-- escalation SUCCEEDS since 2026-09-16, as a deliberate failing-by-design
-- regression test; this migration is what flips it.
--
-- Depends on 20260917000100_audience_model.sql: the UPDATE allowlist below
-- names active_audience and onboarded_at, which that migration adds. Filename
-- order guarantees it runs first.

-- 1. admin_grants ------------------------------------------------------------

create table if not exists public.admin_grants (
  user_id    uuid not null references auth.users(id) on delete cascade,
  capability text not null,
  granted_by uuid references auth.users(id) on delete set null,
  granted_at timestamptz not null default now(),
  expires_at timestamptz,
  -- The primary key's index also serves backend/auth.py's lookup, which filters
  -- on user_id alone. No separate index is needed.
  primary key (user_id, capability),
  -- Capabilities are code identifiers, compared exactly. A typo or a stray
  -- space would silently never match, which is the worst way for an
  -- authorization check to fail. Make it fail at seed time instead.
  constraint admin_grants_capability_check
    check (capability ~ '^[a-z][a-z0-9_]{1,63}$')
);

alter table public.admin_grants enable row level security;

revoke all on public.admin_grants from anon;
revoke all on public.admin_grants from authenticated;
grant select on public.admin_grants to authenticated;

drop policy if exists "admin_grants_select_own" on public.admin_grants;
create policy "admin_grants_select_own"
  on public.admin_grants
  for select
  to authenticated
  using (user_id = auth.uid());

-- No insert, update or delete policy exists, and that is the entire point. A
-- user who could write here could grant themselves any capability, which would
-- rebuild the hole this migration closes. Grants are service_role only.

-- 2. has_grant ---------------------------------------------------------------

-- For use inside RLS policies. security definer so a policy can consult a table
-- the calling user cannot read, paired with a pinned search_path per the
-- convention in 20260916000500 and 20260916000600.
create or replace function public.has_grant(p_capability text)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
      from public.admin_grants g
     where g.user_id = auth.uid()
       and g.capability = p_capability
       and (g.expires_at is null or g.expires_at > now())
  );
$$;

revoke all on function public.has_grant(text) from public;
revoke all on function public.has_grant(text) from anon;
grant execute on function public.has_grant(text) to authenticated;

-- 3. Freeze the privilege columns on profiles --------------------------------

-- PostgreSQL has no column-level REVOKE against a table-level GRANT. Live holds
--     GRANT ALL ON TABLE public.profiles TO authenticated
-- (supabase/live_schema_snapshot.sql:1682), so the obvious statement
--     revoke update (role) on public.profiles from authenticated;
-- emits `WARNING: no privileges could be revoked for column "role"` and changes
-- nothing at all. The table-level privilege has to go, with an explicit
-- allowlist granted back.
--
-- verify_policies.sql section 12 asserts the resulting ACL by reading
-- information_schema.column_privileges, rather than trusting that these
-- statements did what they look like they do.
revoke update on public.profiles from authenticated;
revoke update on public.profiles from anon;

-- Exactly the columns UI/src/components/UserProfile.jsx:78-87 writes, plus the
-- two personalization columns from 20260917000100.
--
-- Deliberately absent: role (authority), email (the subject of the domain
-- gate), id, created_at, updated_at (trigger-managed), and department (nothing
-- writes it).
grant update (
  full_name,
  university_id,
  phone,
  major,
  minor,
  graduation_year,
  class_standing,
  gpa,
  active_audience,
  onboarded_at
) on public.profiles to authenticated;

-- The same hole exists on INSERT. "Users can insert own profile" (001:90-92)
-- plus a table-wide grant lets a user create their own row with role='admin'.
-- Hard to reach today, because handle_new_user() (001:41-52) is a security
-- definer trigger that pre-creates the row and the primary key blocks a second
-- insert -- but it costs one statement to close.
revoke insert on public.profiles from authenticated;
revoke insert on public.profiles from anon;
-- Exactly what ensureProfile (UI/src/supabaseHelpers.js:37-41) names.
grant insert (id, email, full_name) on public.profiles to authenticated;

-- No delete policy exists for profiles either, so the grant is dead weight that
-- would become live the moment someone added one.
revoke delete on public.profiles from authenticated;
revoke delete on public.profiles from anon;

-- 4. Backstop trigger --------------------------------------------------------

-- The grants above are the real control. This is the second wall, for the case
-- where a future migration or a dashboard click restores a table-wide grant.
--
-- security INVOKER, not definer: it only reads OLD and NEW and needs no extra
-- privilege. search_path is still pinned so auth.role() resolves predictably.
create or replace function public.freeze_profile_privilege_columns()
returns trigger
language plpgsql
set search_path = public
as $$
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

-- BEFORE ROW triggers fire in name order, so on_profiles_updated (001:36-38)
-- runs before this one. They touch disjoint columns, so the order does not
-- matter; noted only so nobody has to work it out again.
drop trigger if exists trg_freeze_profile_privilege_columns on public.profiles;
create trigger trg_freeze_profile_privilege_columns
  before update on public.profiles
  for each row execute function public.freeze_profile_privilege_columns();
