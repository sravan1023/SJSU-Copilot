-- reg_class_sections.meeting_count: no default, and at least 1.
--
-- Review finding 6 on 20261001000100. That migration is on live, so it is
-- corrected here rather than edited.
--
-- `meeting_count` exists so the final-exam lookup (Registration B4) can refuse
-- a section with more than one meeting pattern without unpacking `meetings`.
-- That refusal is the "never guess" rule. 20261001000100 declared the column
-- `smallint not null default 1`, and its consistency check accepts an empty
-- `meetings` array against any count. Together they fail open:
--
--   * A producer that leaves `meetings` as [] and omits the count gets 1 from
--     the default. For a section that meets twice, the lookup would then compute
--     a slot from `days` and `start_time`, which hold the first pattern only.
--     That is exactly the guess the schema says it must refuse. The default
--     turns a missing value into a confident wrong one, and nothing downstream
--     can tell the two apart.
--   * Nothing rejects 0 or a negative count. The consistency check cannot catch
--     either, because the only array either can agree with is [], which it
--     accepts against any count.
--
-- Cheap now, and complete now, because the table is empty on live: no refresh
-- has written section rows yet. Once rows exist, a defaulted 1 is
-- indistinguishable from a parsed one, so dropping the default later would not
-- repair what it had already written. On an empty table the ADD CONSTRAINT scan
-- below validates nothing and its ACCESS EXCLUSIVE lock is held for no time, so
-- NOT VALID would buy nothing.
--
-- No grants change. This creates no table and no function, and
-- reg_class_sections stays service_role only, as 20261001000100 left it.
-- verify_policies.sql section 24 tests both statements.


-- 1. No default ---------------------------------------------------------------

-- The column stays NOT NULL, so an insert that omits the count now fails, as
-- does a PostgREST batch that sends it as null (PostgREST fills a key missing
-- from one object of a batch with null unless the request says
-- `Prefer: missing=default`). A failed insert fails the refresh closed, which
-- leaves readers on the previous snapshot instead of serving a guessable one.
-- Only the parser knows how many meetings a row has, so only the parser may say.
alter table public.reg_class_sections
  alter column meeting_count drop default;


-- 2. At least one meeting -----------------------------------------------------

-- `>= 1`, not `>= 0`. Every row on the Schedule of Classes has at least one
-- meeting: a fully asynchronous online class still prints 'TBA' in its Days and
-- Times cells, which the parser stores as one meeting with null times, and that
-- is how the schedule shows it. No row of the 300-row Fall 2026 fixture
-- (backend/tests/fixtures/campus/schedule-fall-2026.trimmed.html) has an empty
-- Days cell.
--
-- So 0 is never a true count. The way to produce one is `len(meetings)` over an
-- array the parser failed to fill, and a row with a count of 0 and an empty
-- array says nothing at all about when the class meets. Rejecting it fails the
-- refresh closed over a parser bug instead of storing a row that has to be
-- special-cased by every reader.
alter table public.reg_class_sections
  add constraint reg_class_sections_meeting_count_check check (meeting_count >= 1);
