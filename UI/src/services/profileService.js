/**
 * Profile, affiliation and audience-detail writes.
 *
 * These three tables are guarded by **column-level** grants, not just RLS, and
 * PostgREST rejects an entire statement if any single column it names is
 * ungranted. That makes the failure mode nasty: adding one innocuous field to
 * a write breaks every other field in the same statement, and the error says
 * only "permission denied for table". So the column lists live here, once,
 * next to the reason they are what they are.
 *
 *   profiles                    UPDATE allowlist of 10 columns, INSERT of 3
 *                               (20260917000200_profiles_privilege_guard.sql)
 *   user_affiliations           INSERT (user_id, affiliation, source) only --
 *                               `status` is NOT grantable, by design
 *   profile_audience_details    full CRUD, RLS-scoped to the owner
 *                               (20260917000100_audience_model.sql)
 */
import { supabase } from '../supabaseClient';

/**
 * Columns `authenticated` may UPDATE on `profiles`.
 *
 * Deliberately absent, and not an oversight: `role` (authority), `email` (the
 * subject of the domain gate), `id`, `created_at`, `updated_at`
 * (trigger-managed) and `department` (nothing writes it).
 */
export const PROFILE_UPDATE_ALLOWLIST = Object.freeze([
  'full_name',
  'university_id',
  'phone',
  'major',
  'minor',
  'graduation_year',
  'class_standing',
  'gpa',
  'active_audience',
  'onboarded_at',
]);

/**
 * Drop anything outside the allowlist before writing.
 *
 * Fails loudly in development rather than silently dropping a field that the
 * caller expected to be saved -- a silent drop would look exactly like a
 * successful save that did nothing.
 */
function allowedProfileColumns(patch) {
  const out = {};
  const rejected = [];
  for (const [key, value] of Object.entries(patch)) {
    if (PROFILE_UPDATE_ALLOWLIST.includes(key)) out[key] = value;
    else rejected.push(key);
  }
  if (rejected.length && import.meta.env.DEV) {
    console.error(
      `profiles: refusing to write ungranted column(s): ${rejected.join(', ')}. ` +
      'Add them to the UPDATE grant in a migration first, or PostgREST will ' +
      'reject the whole statement and every other field with it.'
    );
  }
  return out;
}

/** Patch the caller's own profile row. Returns the updated row. */
export async function updateProfile(userId, patch) {
  const { data, error } = await supabase
    .from('profiles')
    .update(allowedProfileColumns(patch))
    .eq('id', userId)
    .select()
    .single();
  if (error) throw error;
  return data;
}

/**
 * Record a declared affiliation.
 *
 * **Only three columns may be named.** `status` is not among them even though
 * the value would be `'declared'`: the grant is
 * `insert (user_id, affiliation, source)`, so naming `status` is a 403 no
 * matter what it is set to. The column default supplies it, which is what
 * verify_policies.sql section 18 asserts ("default status applied under a
 * column grant"). Verification is a deliberate act by someone holding the
 * capability, and a user declaring something about themselves is not that.
 */
export async function declareAffiliation(userId, affiliation, source = 'onboarding') {
  const { error } = await supabase
    .from('user_affiliations')
    .insert({ user_id: userId, affiliation, source });

  // A repeat declaration is not an error worth surfacing: the unique
  // constraint on (user_id, affiliation) means the row is already there.
  if (error && error.code !== '23505') throw error;
}

/** Store the audience-specific fields that have no `profiles` column. */
export async function saveAudienceDetails(userId, audience, details) {
  const { error } = await supabase
    .from('profile_audience_details')
    .upsert(
      { user_id: userId, audience, details, updated_at: new Date().toISOString() },
      { onConflict: 'user_id,audience' }
    );
  if (error) throw error;
}

/** The audience-specific fields for one audience, or {} when none are stored. */
export async function fetchAudienceDetails(userId, audience) {
  const { data, error } = await supabase
    .from('profile_audience_details')
    .select('details')
    .eq('user_id', userId)
    .eq('audience', audience)
    .maybeSingle();
  if (error) throw error;
  return data?.details ?? {};
}

/**
 * Finish onboarding: pick an audience, optionally declare an affiliation, and
 * store any audience-specific fields.
 *
 * `onboarded_at` is always set, including on a skip, so a skip is durable and
 * the visitor is not re-prompted on every load.
 */
export async function completeOnboarding(userId, { audience, affiliation, details }) {
  const profile = await updateProfile(userId, {
    active_audience: audience,
    onboarded_at: new Date().toISOString(),
  });

  // Best-effort: onboarding has already succeeded from the user's point of
  // view once the profile row is updated, and re-prompting them because a
  // secondary write failed would be worse than the missing row.
  if (affiliation) {
    await declareAffiliation(userId, affiliation).catch((err) =>
      console.warn('Could not record affiliation:', err?.message || err)
    );
  }
  if (details && Object.keys(details).length > 0) {
    await saveAudienceDetails(userId, audience, details).catch((err) =>
      console.warn('Could not save audience details:', err?.message || err)
    );
  }

  return profile;
}
