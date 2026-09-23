/**
 * Typed access to the audience configuration.
 *
 * `audiences.json` is the single source of truth and is also read by
 * `backend/audiences.py`, so the prompt half and the UI half cannot drift.
 * `backend/tests/test_audiences.py` asserts the two sides agree and that the
 * four ids match the database check constraints.
 *
 * This file is `.ts` rather than `.js` on purpose: `tsconfig.app.json` includes
 * only `src/**​/*.ts(x)`, so a `.js` config module would be type-checked by
 * nothing -- which is already true of every `.jsx` component and is not worth
 * extending.
 */
import raw from './audiences.json';

export type AudienceId = 'student' | 'alumni' | 'guest' | 'faculty';

/** The six values of the `affiliation_kind` enum. A claim, not an experience. */
export type AffiliationKind =
  | 'student'
  | 'alumni'
  | 'faculty'
  | 'staff'
  | 'applicant'
  | 'community';

export interface Suggestion {
  title: string;
  subtitle: string;
  prompt: string;
}

export interface ProfileField {
  name: string;
  label: string;
  type: 'text' | 'select' | 'month' | 'number' | 'tel' | 'email';
  icon?: string;
  placeholder?: string;
  options?: string[];
  /** Spans both columns of the two-column grid. */
  span?: number;
  maxLength?: number;
  min?: number;
  max?: number;
  step?: number;
  /**
   * The `profiles` column this field writes to. Present only for fields that
   * predate the audience model; everything else goes to
   * `profile_audience_details.details` as jsonb.
   *
   * A column named here MUST be in the UPDATE allowlist granted by
   * supabase/migrations/20260917000200_profiles_privilege_guard.sql, because
   * PostgREST rejects the whole statement if any one named column is ungranted.
   */
  column?: string;
}

export interface Audience {
  id: AudienceId;
  label: string;
  blurb: string;
  welcomeHeadline: string;
  /** Which `affiliation_kind` values fold into this audience. */
  affiliations: AffiliationKind[];
  /** What onboarding declares for this audience; null writes no row. */
  declaresAffiliation: AffiliationKind | null;
  sourceCollections: string[];
  offices: string[];
  promptContext: string;
  suggestions: Suggestion[];
  profileFields: ProfileField[];
}

interface AudienceConfig {
  default: AudienceId;
  audiences: Record<AudienceId, Audience>;
  affiliationToAudience: Record<AffiliationKind, AudienceId>;
}

const config = raw as unknown as AudienceConfig;

export const AUDIENCES: Record<AudienceId, Audience> = config.audiences;
export const DEFAULT_AUDIENCE: AudienceId = config.default;
export const AUDIENCE_IDS: AudienceId[] = Object.keys(AUDIENCES) as AudienceId[];
export const AFFILIATION_TO_AUDIENCE: Record<AffiliationKind, AudienceId> =
  config.affiliationToAudience;

/**
 * Resolve an audience id from whatever the caller has, falling back rather
 * than throwing. `profiles.active_audience` is `not null default 'student'`,
 * but a guest has no profile row at all and an older row may predate a value.
 */
export function resolveAudience(id: string | null | undefined): Audience {
  if (id && Object.prototype.hasOwnProperty.call(AUDIENCES, id)) {
    return AUDIENCES[id as AudienceId];
  }
  return AUDIENCES[DEFAULT_AUDIENCE];
}

/** True when `id` is one of the four the database will accept. */
export function isAudienceId(id: string | null | undefined): id is AudienceId {
  return !!id && Object.prototype.hasOwnProperty.call(AUDIENCES, id);
}

/** The audience a declared affiliation belongs to. */
export function audienceForAffiliation(affiliation: AffiliationKind): AudienceId {
  return AFFILIATION_TO_AUDIENCE[affiliation] ?? DEFAULT_AUDIENCE;
}

/**
 * The audiences a person may pick during onboarding, in display order.
 * Guest is included: someone may sign up and still only want public
 * information.
 */
export const ONBOARDING_ORDER: AudienceId[] = ['student', 'alumni', 'faculty', 'guest'];
