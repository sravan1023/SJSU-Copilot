/**
 * The current Supabase access token, cached in memory.
 *
 * Every request to the FastAPI backend now needs an `Authorization: Bearer`
 * header, and `sendMessage` is on the latency-critical path. Calling
 * `supabase.auth.getSession()` per send would put an async storage read (and
 * sometimes a refresh round trip) between the user pressing Enter and the
 * request leaving the browser -- the same class of serial wait the Phase 3 work
 * removed.
 *
 * So the token is kept here instead, primed from the `onAuthStateChange`
 * subscription App.jsx already owns. supabase-js refreshes on its own and
 * fires TOKEN_REFRESHED, so the cache stays fresh without polling, and the
 * common path costs one object read.
 */
import { supabase } from '../supabaseClient';
import type { Session } from '@supabase/supabase-js';

/** Refresh this long before `expires_at` rather than racing it. */
const REFRESH_WINDOW_S = 60;

export type TokenKind = 'user' | 'guest';

let cachedToken: string | null = null;
let cachedExpiresAt: number | null = null; // unix seconds
let cachedKind: TokenKind | null = null;
let inflight: Promise<string | null> | null = null;

/**
 * Adopt the token from a Supabase session. Safe to call with null (sign-out)
 * and safe to call repeatedly with the same session.
 */
export function primeAuthToken(session: Session | null): void {
  cachedToken = session?.access_token ?? null;
  cachedExpiresAt = session?.expires_at ?? null;
  cachedKind = session?.access_token ? 'user' : null;
}

/**
 * Adopt a guest token from POST /api/guest/session.
 *
 * This exists because the refresh path below cannot serve a guest: it asks
 * supabase-js for a session, and a guest has none, so a stale guest token
 * would be replaced with null on the first expiry check and the visitor would
 * be silently signed out mid-conversation.
 */
export function primeGuestToken(token: string, expiresInSeconds: number): void {
  cachedToken = token;
  cachedExpiresAt = Math.floor(Date.now() / 1000) + expiresInSeconds;
  cachedKind = 'guest';
}

export function clearAuthToken(): void {
  cachedToken = null;
  cachedExpiresAt = null;
  cachedKind = null;
  inflight = null;
}

/** Which kind of token is cached, if any. */
export function getTokenKind(): TokenKind | null {
  return cachedKind;
}

function stillFresh(): boolean {
  if (!cachedToken || !cachedExpiresAt) return false;
  return cachedExpiresAt - Date.now() / 1000 > REFRESH_WINDOW_S;
}

/**
 * The token to send, or null when there is none.
 *
 * The hit path does no IO at all -- that is the point of this module. It only
 * falls through to `getSession()` when the cache is empty or close to expiry,
 * and concurrent callers share one in-flight lookup so several queued sends
 * (or a tab regaining focus) cannot stampede.
 */
export async function getAuthToken(): Promise<string | null> {
  if (stillFresh()) return cachedToken;

  // A guest has no Supabase session to refresh from, and asking for one would
  // return null and clobber a token that may still be valid. An expired guest
  // token is handled where it belongs -- the backend answers 401 and the app
  // starts a new guest session, which is what "a refresh ends the session"
  // means.
  if (cachedKind === 'guest') return cachedToken;

  if (!inflight) {
    inflight = (async () => {
      try {
        const { data } = await supabase.auth.getSession();
        primeAuthToken(data.session);
        return cachedToken;
      } catch {
        // Signed out, offline, or storage unavailable. The caller sends no
        // header and the backend decides -- better than failing the send here.
        return null;
      } finally {
        inflight = null;
      }
    })();
  }

  return inflight;
}

/**
 * JSON headers for a backend call, with the bearer token when there is one.
 *
 * The header is omitted entirely rather than sent as `Bearer null`, so an
 * unauthenticated request looks unauthenticated to the server.
 */
export async function authedJsonHeaders(): Promise<Record<string, string>> {
  const token = await getAuthToken();
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}
