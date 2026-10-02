<!-- Generated 2026-09-21 by supabase/tests/schema_drift.py. Live = project bsiubrodeyuzbmvmydfe (supabase db dump --linked --schema public). Repo = migrations 001..20260410 replayed on supabase/postgres 17.6.1.084, dumped with the same command. -->

# Schema drift: `live_schema_snapshot.sql` vs `replay_20260410.sql`

- Only in live: **5**
- Only in repo: **75**
- Defined differently: **4**

## Only in live

**function** (1)

- `search_documents_fts("query_text" "text", "match_count" integer DEFAULT 5)`

**grant** (3)

- `"public"."search_documents_fts"("query_text" "text", "match_count" integer) :: "anon" :: GRANT ALL`
- `"public"."search_documents_fts"("query_text" "text", "match_count" integer) :: "authenticated" :: GRANT ALL`
- `"public"."search_documents_fts"("query_text" "text", "match_count" integer) :: "service_role" :: GRANT ALL`

**index** (1)

- `idx_documents_last_verified_at / documents`

## Only in repo

**constraint** (13)

- `chat_messages / chat_messages_conversation_id_fkey`
- `chat_messages / chat_messages_pkey`
- `chat_messages / chat_messages_user_id_fkey`
- `internship_alert_preferences / internship_alert_preferences_pkey`
- `internship_alert_preferences / internship_alert_preferences_user_id_fkey`
- `internship_alerts_sent / internship_alerts_sent_listing_id_fkey`
- `internship_alerts_sent / internship_alerts_sent_pkey`
- `internship_alerts_sent / internship_alerts_sent_user_id_fkey`
- `internship_listings / internship_listings_pkey`
- `user_internship_listing_state / user_internship_listing_state_listing_id_fkey`
- `user_internship_listing_state / user_internship_listing_state_pkey`
- `user_internship_listing_state / user_internship_listing_state_user_id_fkey`
- `user_internship_listing_state / user_internship_listing_state_user_id_listing_id_key`

**function** (2)

- `compute_internship_match_score("p_title" "text", "p_company" "text", "p_location" "text", "p_description" "text", "p_role_tags" "text"[], "p_roles" "text"[], "p_preferred_locations" "text"[], "p_remote_only" boolean, "p_is_remote" boolean, "p_internships_only" boolean, "p_employment_type" "text")`
- `match_internship_listings_for_user("p_user_id" "uuid", "p_limit" integer DEFAULT 40)`

**grant** (25)

- `"public"."chat_messages" :: "anon" :: GRANT ALL`
- `"public"."chat_messages" :: "authenticated" :: GRANT ALL`
- `"public"."chat_messages" :: "service_role" :: GRANT ALL`
- `"public"."compute_internship_match_score"("p_title" "text", "p_company" "text", "p_location" "text", "p_description" "text", "p_role_tags" "text"[], "p_roles" "text"[], "p_preferred_locations" "text"[], "p_remote_only" boolean, "p_is_remote" boolean, "p_internships_only" boolean, "p_employment_type" "text") :: "anon" :: GRANT ALL`
- `"public"."compute_internship_match_score"("p_title" "text", "p_company" "text", "p_location" "text", "p_description" "text", "p_role_tags" "text"[], "p_roles" "text"[], "p_preferred_locations" "text"[], "p_remote_only" boolean, "p_is_remote" boolean, "p_internships_only" boolean, "p_employment_type" "text") :: "authenticated" :: GRANT ALL`
- `"public"."compute_internship_match_score"("p_title" "text", "p_company" "text", "p_location" "text", "p_description" "text", "p_role_tags" "text"[], "p_roles" "text"[], "p_preferred_locations" "text"[], "p_remote_only" boolean, "p_is_remote" boolean, "p_internships_only" boolean, "p_employment_type" "text") :: "service_role" :: GRANT ALL`
- `"public"."internship_alert_preferences" :: "anon" :: GRANT ALL`
- `"public"."internship_alert_preferences" :: "authenticated" :: GRANT ALL`
- `"public"."internship_alert_preferences" :: "service_role" :: GRANT ALL`
- `"public"."internship_alerts_sent" :: "anon" :: GRANT ALL`
- `"public"."internship_alerts_sent" :: "authenticated" :: GRANT ALL`
- `"public"."internship_alerts_sent" :: "service_role" :: GRANT ALL`
- `"public"."internship_listing_public" :: "anon" :: GRANT ALL`
- `"public"."internship_listing_public" :: "authenticated" :: GRANT ALL`
- `"public"."internship_listing_public" :: "service_role" :: GRANT ALL`
- `"public"."internship_listings" :: "anon" :: GRANT ALL`
- `"public"."internship_listings" :: "authenticated" :: GRANT ALL`
- `"public"."internship_listings" :: "service_role" :: GRANT ALL`
- `"public"."match_internship_listings_for_user"("p_user_id" "uuid", "p_limit" integer) :: "anon" :: GRANT ALL`
- `"public"."match_internship_listings_for_user"("p_user_id" "uuid", "p_limit" integer) :: "authenticated" :: GRANT ALL`
- `"public"."match_internship_listings_for_user"("p_user_id" "uuid", "p_limit" integer) :: "service_role" :: GRANT ALL`
- `"public"."match_internship_listings_for_user"("p_user_id" "uuid", "p_limit" integer) :: PUBLIC :: REVOKE ALL`
- `"public"."user_internship_listing_state" :: "anon" :: GRANT ALL`
- `"public"."user_internship_listing_state" :: "authenticated" :: GRANT ALL`
- `"public"."user_internship_listing_state" :: "service_role" :: GRANT ALL`

**index** (9)

- `internship_alerts_sent_dedupe_idx / internship_alerts_sent`
- `internship_alerts_sent_user_idx / internship_alerts_sent`
- `internship_listings_canonical_url_key / internship_listings`
- `internship_listings_posted_at_idx / internship_listings`
- `internship_listings_role_category_idx / internship_listings`
- `internship_listings_source_idx / internship_listings`
- `internship_listings_title_trgm_idx / internship_listings`
- `user_internship_listing_state_listing_idx / user_internship_listing_state`
- `user_internship_listing_state_user_idx / user_internship_listing_state`

**policy** (12)

- `Users can insert own messages / chat_messages`
- `Users can view own messages / chat_messages`
- `internship_alert_preferences_delete_own / internship_alert_preferences`
- `internship_alert_preferences_insert_own / internship_alert_preferences`
- `internship_alert_preferences_select_own / internship_alert_preferences`
- `internship_alert_preferences_update_own / internship_alert_preferences`
- `internship_alerts_sent_select_own / internship_alerts_sent`
- `internship_listings_select_authenticated / internship_listings`
- `user_internship_listing_state_delete_own / user_internship_listing_state`
- `user_internship_listing_state_insert_own / user_internship_listing_state`
- `user_internship_listing_state_select_own / user_internship_listing_state`
- `user_internship_listing_state_update_own / user_internship_listing_state`

**rls** (5)

- `chat_messages`
- `internship_alert_preferences`
- `internship_alerts_sent`
- `internship_listings`
- `user_internship_listing_state`

**table** (5)

- `chat_messages`
- `internship_alert_preferences`
- `internship_alerts_sent`
- `internship_listings`
- `user_internship_listing_state`

**trigger** (3)

- `trg_internship_alert_preferences_updated_at / internship_alert_preferences`
- `trg_internship_listings_updated_at / internship_listings`
- `trg_user_internship_listing_state_updated_at / user_internship_listing_state`

**view** (1)

- `internship_listing_public`

## Defined differently

**function** `match_documents("query_embedding" "public"."vector", "match_count" integer DEFAULT 5, "match_threshold" double precision DEFAULT 0.78)`

- live: CREATE OR REPLACE FUNCTION "public"."match_documents"("query_embedding" "public"."vector", "match_count" integer DEFAULT 5, "match_threshold" double precision DEFAULT 0.78) RETURNS TABLE("id" "uuid", "document_id" "uuid", "chunk_index" integer, "content" "text", "metadata" "jsonb", "title" "text", "url" "text", "source" "text", "document_type" "text", "similarity" double precision, "last_verified_at" timestamp with time zone) LANGUAGE "plpgsql" STABLE AS $$ begin return query select dc.id, dc.document_id, dc.chunk_index, dc.content, dc.metadata, d.title, d.url, d.source, d.document_type, 1 - (dc.embedding <=> query_embedding) as similarity, d.last_verified_at from public.document_chunks dc join public.documents d on d.id = dc.document_id where 1 - (dc.embedding <=> query_embedding) > match_threshold order by dc.embedding <=> query_embedding limit match_count; end; $$
- repo: CREATE OR REPLACE FUNCTION "public"."match_documents"("query_embedding" "public"."vector", "match_count" integer DEFAULT 5, "match_threshold" double precision DEFAULT 0.78) RETURNS TABLE("id" "uuid", "document_id" "uuid", "content" "text", "metadata" "jsonb", "similarity" double precision) LANGUAGE "plpgsql" STABLE AS $$ begin return query select dc.id, dc.document_id, dc.content, dc.metadata, 1 - (dc.embedding <=> query_embedding) as similarity from public.document_chunks dc where 1 - (dc.embedding <=> query_embedding) > match_threshold order by dc.embedding <=> query_embedding limit match_count; end; $$

**table** `documents`

- column only in live: last_verified_at timestamp with time zone
- column only in live: content_hash "text"

**table** `messages`

- column only in live: citations "jsonb" DEFAULT '[]'::"jsonb" NOT NULL
- column only in live: retrieval_meta "jsonb" DEFAULT '{}'::"jsonb" NOT NULL

**table** `profiles`

- column only in repo: target_roles "text"[] DEFAULT '{}'::"text"[] NOT NULL
- column only in repo: preferred_locations "text"[] DEFAULT '{}'::"text"[] NOT NULL
- column only in repo: remote_only boolean DEFAULT false NOT NULL
- column only in repo: alert_frequency "text" DEFAULT 'daily'::"text" NOT NULL

