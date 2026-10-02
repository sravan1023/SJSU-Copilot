-- Campus snapshots: SJSU's registration pages, kept as last-known-good tables.
--
-- Registration Info (docs/SERVICES_PLAN.md section 3) answers deadline, class
-- search, final-exam and payment lookups from SJSU's own public pages, with no
-- LLM and no live search. An offline refresh (backend/campus/, not written yet)
-- fetches those pages nightly and writes them here; the backend serves them.
-- Nothing reads or writes these tables yet.
--
-- Tables rather than an in-process cache: the class schedule alone is 3.76 MB
-- and ~7,000 rows. A process cache re-downloads it on every reload, loses the
-- last good copy on restart, has nothing to serve on a cold start during an
-- SJSU outage, and parses on the request path.
--
-- The shape is one snapshot per (source, term), and the refresh fails closed:
--
--   1. Insert a `campus_snapshots` row as `staged`, then its rows into the
--      reg_* child tables in batches.
--   2. Run the drift checks against what was staged.
--   3. One write to `campus_current` re-points (source, term) at the new
--      snapshot. Readers resolve through campus_current and nothing else, so a
--      crash or a failed check anywhere before this leaves them on the old data.
--   4. Mark the old snapshot `superseded`; prune superseded snapshots after 24 h.
--
-- ---------------------------------------------------------------------------
-- Access: service_role only
--
-- All of this is public SJSU data, but no browser reads it directly. Guests have
-- no Supabase session, so the UI goes through /api/registration/*, and the
-- backend reads with the service key, which bypasses RLS. The real gate is
-- therefore the grants: anon and authenticated hold no privilege on any table
-- below. RLS with no policies is the second wall behind it. Neither table set
-- needs a guest boundary, because nothing here is private.
--
-- What a client write would cost is the reason for both walls: a signed-in user
-- who could update campus_current could point every reader at a snapshot of
-- their choosing, and one who could write reg_term_events could publish a
-- deadline.
--
-- The revoke is not optional. 20260918000100 left default privileges that hand
-- SELECT, INSERT, UPDATE and DELETE on every new public table to anon and
-- authenticated, so a new table without it is reachable with the anon key.
-- backend/tests/test_migrations.py now checks both statements for every table
-- created after 20260930000100, and verify_policies.sql section 23 tests each
-- wall on its own.
--
-- No functions and no views, so there is no EXECUTE to revoke. service_role
-- keeps the ALL that the default privileges give it.
--
-- Plain `create table`, not `if not exists`: none of these exist on live
-- (checked against supabase/live_schema_snapshot.sql), and if one somehow did,
-- `if not exists` would skip it silently and leave whatever shape it had.
-- ---------------------------------------------------------------------------


-- 1. campus_snapshots: one fetch of one source for one term -------------------

-- `source_key` is a closed set, so a typo in the refresh fails the insert rather
-- than writing rows no reader will ever look up. `bursar` is in it now so that
-- payment due dates (Registration C) need no second migration.
--
-- `scope_key` is a term ('fall-2026') or, for the academic calendar, which SJSU
-- publishes per academic year, a year span ('ay-2026-2027'). A key in any other
-- spelling ('Fall 2026', 'fall-26') would be stored and then never matched.
create table public.campus_snapshots (
  id                 uuid primary key default gen_random_uuid(),
  source_key         text not null
                       constraint campus_snapshots_source_key_check
                       check (source_key in ('schedule', 'registrar', 'academic', 'exams', 'bursar')),
  scope_key          text not null
                       constraint campus_snapshots_scope_key_check
                       check (scope_key ~ '^((spring|summer|fall|winter)-20[0-9]{2}|ay-20[0-9]{2}-20[0-9]{2})$'),
  -- The fixed template URL, and where the bytes actually came from: the final
  -- URL after redirects, or a fixture path for a `--fixture-dir` run. Without
  -- the second, a snapshot loaded from a test fixture could pass for a live
  -- fetch.
  source_url         text not null,
  fetched_from       text,
  fetched_at         timestamptz not null default now(),
  -- The page's own "Last Updated" footer, which every card shows. A date, not a
  -- timestamptz: PostgREST would read '2026-09-18' into a timestamptz as
  -- midnight UTC, which is the previous evening in San Jose.
  page_last_updated  date,
  -- Lets a refresh recognise an unchanged page and stage nothing.
  content_hash       text not null,
  row_count          int not null default 0
                       constraint campus_snapshots_row_count_check check (row_count >= 0),
  -- The table header as parsed (the first drift check), and the result of every
  -- check, so a rejected snapshot records why it was rejected.
  header             jsonb,
  checks             jsonb not null default '{}',
  -- Bookkeeping for the refresh and the admin status route. It is NOT what
  -- readers trust: campus_current is. A crash between the flip and the status
  -- update can leave the two disagreeing, and section 2 is written so that the
  -- disagreement can never lose data.
  status             text not null default 'staged'
                       constraint campus_snapshots_status_check
                       check (status in ('staged', 'current', 'superseded', 'rejected')),
  created_at         timestamptz not null default now(),
  -- Redundant with the primary key. It exists only to be the target of
  -- campus_current's composite foreign key.
  constraint campus_snapshots_id_source_scope_key unique (id, source_key, scope_key)
);

-- The refresh asks two questions of history, and both lead with (source, term):
-- how many rows did the previous snapshot have (the >= 70% drift check), and
-- which superseded snapshots are old enough to prune.
create index idx_campus_snapshots_source_scope_created
  on public.campus_snapshots (source_key, scope_key, created_at desc);


-- 2. campus_current: the pointer every reader resolves through ----------------

-- One row per (source, term). The refresh's only write here is the flip, so a
-- reader sees the whole old snapshot or the whole new one, never a mixture.
--
-- The foreign key is composite, (snapshot_id, source_key, scope_key), so the
-- pointer for schedule/fall-2026 can only name a schedule/fall-2026 snapshot. A
-- single-column key would accept any snapshot id, and a refresh bug that
-- crossed two terms would serve one term's classes as another's with nothing to
-- say so.
--
-- ON DELETE RESTRICT, so that pruning can never delete what readers are on:
--
--   * CASCADE would delete the pointer along with the snapshot. Readers would
--     find no current snapshot and serve an empty state: the outage this whole
--     design exists to prevent.
--   * SET NULL cannot apply to a not-null column, and a null pointer would be
--     the same outage anyway.
--   * The default, NO ACTION, would also refuse today. RESTRICT says so
--     explicitly and is checked immediately, even if the constraint is later
--     made deferrable, so no transaction can pass through a state in which the
--     current snapshot is gone.
--
-- A prune statement that matches the current snapshot therefore fails as a
-- whole and deletes nothing. That is loud rather than partial, and it means the
-- refresh's prune must exclude the ids campus_current names; `status` alone is
-- not a safe filter (see section 1).
create table public.campus_current (
  source_key   text not null,
  scope_key    text not null,
  snapshot_id  uuid not null,
  -- When a refresh last confirmed this pointer, including a run that found the
  -- page unchanged and staged nothing. The snapshot's fetched_at alone would
  -- make a page that hasn't changed in a month look a month stale.
  verified_at  timestamptz not null default now(),
  primary key (source_key, scope_key),
  -- At most one pointer per snapshot. The composite key already implies it,
  -- since a snapshot has exactly one (source, term). Declaring it makes the
  -- RESTRICT check on every prune an index probe.
  constraint campus_current_snapshot_id_key unique (snapshot_id),
  constraint campus_current_snapshot_fkey
    foreign key (snapshot_id, source_key, scope_key)
    references public.campus_snapshots (id, source_key, scope_key)
    on delete restrict
);


-- 3. campus_refresh_runs: one row per refresh ---------------------------------

-- Shaped like kb_ingest_runs. A failed drift check produces a run here and no
-- flip, so this is where "why is the schedule three days old" gets answered.
-- Per-source detail (bytes, truncated, fetch and parse ms, rows, failed checks,
-- swapped) goes in `stats`.
create table public.campus_refresh_runs (
  id           uuid primary key default gen_random_uuid(),
  started_at   timestamptz not null default now(),
  finished_at  timestamptz,
  -- 'running' until the run records a result, so a run that crashed shows up
  -- as one that never finished rather than as nothing at all.
  outcome      text not null default 'running'
                 constraint campus_refresh_runs_outcome_check
                 check (outcome in ('running', 'success', 'partial', 'failed')),
  stats        jsonb not null default '{}',
  error        text
);

create index idx_campus_refresh_runs_started_at
  on public.campus_refresh_runs (started_at desc);


-- 4. reg_class_sections: the Schedule of Classes ------------------------------

-- One row per section, ~7,000 for a fall term. The printed columns are typed
-- where class search filters on them, and the whole row is kept in `raw`, so a
-- parser fix can be re-derived from stored snapshots.
--
-- `satisfies_raw` is the cell as printed ('GE: UD 2/5'). `satisfies` is the
-- normalised tag list; a cell naming two areas is tagged with both, so a search
-- for either matches. The raw string is always kept and shown.
--
-- Nothing constrains the contents of `satisfies`. The page's values are more
-- varied than any list written in advance ('GE: 5A+5C', 'GE:3B+US23',
-- 'GE: 1Bor4', 'AI: US1', 'PE: PhysEd', 'GWAR', measured on Fall 2026), and the
-- tag vocabulary belongs to the parser, not to a migration.
--
-- One row per section, even when it meets more than once. On the Fall 2026
-- page, 473 of 6,980 rows carry several meetings in one row, as values separated
-- by <br> in the Days, Times, Instructor, Location and Notes cells (up to six,
-- often mixed with TBA, as in 'MW  TBA'):
--
--   * `meetings` holds all of them, as an array of
--     {days, start_time, end_time, location, instructor}. Times are null for
--     TBA.
--   * `days`, `start_time` and `end_time` repeat the FIRST meeting as scalars,
--     so class search can index and filter on them.
--   * `meeting_count` lets the final-exam lookup refuse a multi-pattern section
--     without unpacking the array. That refusal is the "never guess" rule, so
--     the count must agree with the array whenever the array is filled in: a
--     count of 1 against two meetings would make the lookup compute a slot from
--     the first pattern alone, which is a wrong answer and not an "as of" one.
--     An empty array is allowed, so a producer that does not fill it in falls
--     back on the count alone.
--
-- `instructor` is the cell as printed: it may hold several names, with repeats
-- ('A / A'), or 'Staff'. Names only; the schedule's mailto links are never
-- stored.
--
-- Apart from that consistency rule, only identity is constrained here (the
-- unique key below). Values such as `open_seats` are SJSU's nightly figures,
-- shown "as of" the snapshot. The parser's drift checks tolerate a bad cell,
-- and a check constraint would fail a whole night's refresh over one.
--
-- The page itself has duplicate class numbers (PHYS 50 section 25, 43104,
-- appears twice with identical cells). The unique key stays: the parser drops
-- exact duplicates and treats non-identical ones as drift, so a duplicate that
-- reaches this table is a parser bug and should fail the batch.
create table public.reg_class_sections (
  id             uuid primary key default gen_random_uuid(),
  snapshot_id    uuid not null references public.campus_snapshots (id) on delete cascade,
  class_number   int not null,
  course_key     text not null,
  section        text,
  mode           text,
  title          text,
  satisfies_raw  text,
  satisfies      text[] not null default '{}',
  units_min      numeric,
  units_max      numeric,
  type           text,
  -- The first meeting. All of them are in `meetings`.
  days           text,
  start_time     time,
  end_time       time,
  times_raw      text,
  meetings       jsonb not null default '[]',
  meeting_count  smallint not null default 1,
  instructor     text,
  location       text,
  start_date     date,
  end_date       date,
  open_seats     int,
  notes          text,
  raw            jsonb not null default '{}',
  -- CASE, not AND: PostgreSQL does not promise to evaluate AND left to right,
  -- and jsonb_array_length raises on a non-array instead of returning false.
  constraint reg_class_sections_meetings_check
    check (
      case when jsonb_typeof(meetings) = 'array'
           then jsonb_array_length(meetings) in (0, meeting_count)
           else false
      end
    )
);

-- Every class query runs inside one snapshot, the current one for its term.
--
--   * The unique key is the final-exam lookup by class number, the conflict
--     target for a re-run batch, and the index the cascade uses when a snapshot
--     is pruned. Unique per snapshot, not globally: the same class number
--     appears in every night's snapshot of a term.
--   * (snapshot_id, course_key, section) serves `course=` and
--     `course=&section=`.
--   * GIN on `satisfies` serves `ge=`, which PostgREST sends as
--     `satisfies @> '{...}'`.
create unique index uq_reg_class_sections_snapshot_class
  on public.reg_class_sections (snapshot_id, class_number);

create index idx_reg_class_sections_snapshot_course
  on public.reg_class_sections (snapshot_id, course_key, section);

create index idx_reg_class_sections_satisfies
  on public.reg_class_sections using gin (satisfies);


-- 5. reg_term_events: dated rows from the calendars and the bursar -------------

-- One table for three sources, told apart by `category`: the registrar's term
-- calendar ('registrar'), the academic calendar ('academic') and the bursar's
-- payment due dates ('payment'). Their rows have the same shape, a label and a
-- date or range, and the deadlines API reads them together.
--
-- `label_raw` and `date_raw` are the cells word for word, because a card shows
-- the source row exactly as SJSU printed it. `start_date` and `end_date` are
-- what the date normaliser inferred (the pages print no year). `event_key` is
-- the stable name a chat card matches on, null for a row the alias table does
-- not recognise.
--
-- This is the only check on a parsed value in this migration. These dates are
-- the answer, printed on a card as absolute dates, and an end before its start
-- means year inference went wrong across a December/January boundary. Failing
-- the insert fails the refresh closed, which leaves readers on yesterday's
-- correct calendar instead of publishing a wrong deadline.
create table public.reg_term_events (
  id           uuid primary key default gen_random_uuid(),
  snapshot_id  uuid not null references public.campus_snapshots (id) on delete cascade,
  category     text not null
                 constraint reg_term_events_category_check
                 check (category in ('registrar', 'academic', 'payment')),
  label_raw    text not null,
  date_raw     text not null,
  start_date   date,
  end_date     date,
  event_key    text,
  constraint reg_term_events_date_order_check
    check (end_date is null or (start_date is not null and end_date >= start_date))
);

-- Leads with snapshot_id for the cascade; event_key is what a card looks up.
create index idx_reg_term_events_snapshot_event
  on public.reg_term_events (snapshot_id, event_key);


-- 6. reg_exam_rules: the final-exam schedule ----------------------------------

-- SJSU publishes finals as rules, not per section. Classes that meet on one of
-- a group of day patterns and start within a time range take their final in a
-- given slot. The final-exam API matches a section's `days` and `start_time`
-- against these rules.
--
-- Some rows are not rules of that shape: a common final shared by several MATH
-- courses, or online classes with no set exam time. Those are flagged
-- `is_exception`, with the courses they name in `course_keys` and their wording
-- in `note`. A class with two meeting patterns matches no single rule, and the
-- API answers "can't compute" rather than guessing.
--
-- `raw` keeps the whole printed row, so the parser can evolve without a
-- migration: anything it learns to extract later can be re-derived from stored
-- snapshots.
create table public.reg_exam_rules (
  id               uuid primary key default gen_random_uuid(),
  snapshot_id      uuid not null references public.campus_snapshots (id) on delete cascade,
  -- The day-pattern group: every meeting pattern this rule covers, for example
  -- {MW,MWF,M,W}. Empty for an exception keyed by course instead.
  day_patterns     text[] not null default '{}',
  -- The range of class start times the rule covers, inclusive.
  start_time_from  time,
  start_time_to    time,
  exam_date        date,
  exam_start       time,
  exam_end         time,
  is_exception     boolean not null default false,
  course_keys      text[] not null default '{}',
  note             text,
  raw              jsonb not null default '{}'
);

-- A term has a few dozen rules, read whole. This is for the cascade.
create index idx_reg_exam_rules_snapshot
  on public.reg_exam_rules (snapshot_id);


-- 7. Service role only --------------------------------------------------------

-- RLS on with zero policies: no role but service_role, which bypasses RLS, sees
-- or writes a row. Same as kb_sources in 20260930000100.
alter table public.campus_snapshots    enable row level security;
alter table public.campus_current      enable row level security;
alter table public.campus_refresh_runs enable row level security;
alter table public.reg_class_sections  enable row level security;
alter table public.reg_term_events     enable row level security;
alter table public.reg_exam_rules      enable row level security;

-- And the outer wall: take back the CRUD the default privileges just granted.
-- Whole privileges only, because backend/tests/test_migrations.py bans the
-- column-level form, which is a silent no-op against a table-level grant. No
-- `from public` is needed: tables, unlike functions, grant nothing to PUBLIC.
revoke all on public.campus_snapshots    from anon, authenticated;
revoke all on public.campus_current      from anon, authenticated;
revoke all on public.campus_refresh_runs from anon, authenticated;
revoke all on public.reg_class_sections  from anon, authenticated;
revoke all on public.reg_term_events     from anon, authenticated;
revoke all on public.reg_exam_rules      from anon, authenticated;
