-- Security hardening: job fetcher and internship pipeline permissions.

-- 1. job_sources
--
-- 002_job_fetcher.sql:107-110 granted every authenticated user `for all` on
-- job_sources. backend/services/job_fetcher.py reads source_url from this table
-- and fetches it with the service role, so a user-writable source_url is a
-- stored SSRF primitive. The same policy also let any user disable or delete the
-- entire feed.
--
-- The select policy from 002:103 remains and is all the UI needs.
drop policy if exists "Authenticated users can manage job sources" on public.job_sources;

-- 2. job_listings
--
-- 002:116 let any authenticated user insert listings with an attacker-controlled
-- apply_url, which is then displayed to every other user.
drop policy if exists "Authenticated users can insert jobs" on public.job_listings;

-- 3. job_fetch_runs
--
-- 002:142 let any authenticated user forge pipeline log entries.
drop policy if exists "Authenticated users can insert fetch logs" on public.job_fetch_runs;

-- 4. update_pipeline_marker_if_expected
--
-- 20260401_intern_jobs_pipeline.sql:68 is security definer with no REVOKE, so
-- anon and authenticated could execute it and corrupt
-- pipeline_state.previous_top_url -- making the next pipeline run either
-- re-alert on every listing or skip all of them.
revoke all on function public.update_pipeline_marker_if_expected(text, text, text, uuid) from public;
revoke all on function public.update_pipeline_marker_if_expected(text, text, text, uuid) from anon;
revoke all on function public.update_pipeline_marker_if_expected(text, text, text, uuid) from authenticated;
grant execute on function public.update_pipeline_marker_if_expected(text, text, text, uuid) to service_role;

-- 5. internship_listing_public: no longer applies.
--
-- This used to set security_invoker on the anon-readable view from
-- 20260331_internship_alerts.sql. That model was never applied to the live
-- project and is retired by 20260915000000_reconcile_live.sql, which drops the
-- view, so there is nothing left to harden.

-- 6. Seeded mock data
--
-- 002:157-191 seeds an enabled 'SJSU Career Mock Feed' whose two fake jobs point
-- at example.com. Disable it rather than deleting, so the row stays available as
-- a fixture for local development.
update public.job_sources
   set enabled = false
 where name = 'SJSU Career Mock Feed';
