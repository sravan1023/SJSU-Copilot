-- Fix two defects in the behavior_settings scope work (20260407030000).
--
-- 1. BROKEN SIGNUP ON A CLEAN REPLAY.
--
--    20260407010000 creates behavior_settings with `constraint
--    uq_behavior_settings_user unique (user_id)`, and the
--    create_default_behavior_settings() trigger relies on it via
--    `on conflict (user_id) do nothing`.
--
--    20260407030000 then drops that constraint and replaces it with one over
--    (user_id, project_id, conversation_id). Nothing is left that is unique on
--    (user_id) alone, so the ON CONFLICT clause no longer matches any index and
--    raises:
--
--        there is no unique or exclusion constraint matching the
--        ON CONFLICT specification
--
--    The trigger fires AFTER INSERT on profiles, so the error aborts the
--    profile insert -- i.e. account creation fails. This is latent on the
--    deployed database (which was built by hand and may still carry the old
--    constraint) but would break signup immediately on a clean replay.
--
--    Replaced with an explicit existence check, which does not depend on the
--    shape of any unique index.
--
-- 2. THE SCOPE CONSTRAINT DOES NOT ACTUALLY ENFORCE UNIQUENESS.
--
--    20260407030000 comments that "COALESCE is used so that NULLs in
--    project_id / conversation_id don't defeat uniqueness" -- but the
--    constraint is over the raw nullable columns. Under PostgreSQL's default
--    NULLS DISTINCT semantics two rows with both columns NULL never conflict,
--    so a user could accumulate unlimited duplicate global rows.
--
--    resolveEffectiveBehavior picks the first match, so duplicates would make
--    a user's settings non-deterministic between requests.

-- ── 1. Trigger function no longer depends on a (user_id) unique index ────────

create or replace function public.create_default_behavior_settings()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
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

-- ── 2. Scope uniqueness that survives NULLs ─────────────────────────────────

-- Remove any duplicates the broken constraint allowed through, keeping the most
-- recently updated row for each scope.
delete from public.behavior_settings a
using public.behavior_settings b
where a.user_id = b.user_id
  and coalesce(a.project_id, '00000000-0000-0000-0000-000000000000'::uuid)
      = coalesce(b.project_id, '00000000-0000-0000-0000-000000000000'::uuid)
  and coalesce(a.conversation_id, '00000000-0000-0000-0000-000000000000'::uuid)
      = coalesce(b.conversation_id, '00000000-0000-0000-0000-000000000000'::uuid)
  and (a.updated_at, a.id) < (b.updated_at, b.id);

alter table public.behavior_settings
  drop constraint if exists uq_behavior_settings_scope;

drop index if exists public.uq_behavior_settings_scope_idx;

-- An expression index rather than a constraint: constraints cannot be built
-- over expressions, and NULLS NOT DISTINCT would still leave the three-column
-- form matching nothing for the ON CONFLICT above.
create unique index uq_behavior_settings_scope_idx
  on public.behavior_settings (
    user_id,
    coalesce(project_id, '00000000-0000-0000-0000-000000000000'::uuid),
    coalesce(conversation_id, '00000000-0000-0000-0000-000000000000'::uuid)
  );
