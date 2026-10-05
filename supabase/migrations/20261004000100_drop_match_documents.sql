-- Drop public.match_documents.
--
-- It has never had a caller. 001 created it for a retrieval path that was never
-- built, 20260915000000 recreated it to match live, and 20260918000100 revoked
-- it from everyone but service_role. Knowledge-base reads go through
-- match_kb_hybrid (20260930000100), and nothing in UI/src, backend/ or
-- supabase/functions/ calls match_documents.
--
-- No grants change: this creates no table and no function. verify_policies.sql
-- section 20 checks that the function is gone.

drop function if exists public.match_documents(public.vector, integer, double precision);
