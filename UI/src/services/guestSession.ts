/**
 * Starting a guest session.
 *
 * A visitor with no SJSU account gets an identity the server issued: they POST
 * to /api/guest/session and receive a short-lived token. The raw guest id is
 * never invented by the client -- a string the browser picks is not an
 * identity, and the backend would have no reason to believe it.
 *
 * The token lives in memory only, deliberately **not** in localStorage or a
 * cookie. Refreshing the page ends the session, which is the stated
 * requirement rather than an oversight: nothing a guest types is persisted
 * anywhere, and a credential that survived a refresh would imply otherwise.
 */
import { primeGuestToken, clearAuthToken } from './authToken';

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000';

export interface GuestSession {
  guestId: string;
  expiresIn: number;
}

export class GuestSessionError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'GuestSessionError';
    this.status = status;
  }
}

/**
 * Start a guest session and cache its token for subsequent backend calls.
 *
 * Throws rather than returning null, because the caller is a button press with
 * a user waiting: "continue as guest" that silently does nothing is worse than
 * a message.
 */
export async function startGuestSession(): Promise<GuestSession> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/guest/session`, { method: 'POST' });
  } catch {
    throw new GuestSessionError('Could not reach the assistant. Check your connection.', 0);
  }

  if (res.status === 503) {
    // The server is up but has no signing key, so it cannot issue a token
    // anyone could verify. Distinguished because it is an operator problem,
    // not something the visitor can fix by retrying.
    throw new GuestSessionError('Guest access is not available right now.', 503);
  }
  if (res.status === 429) {
    const retry = Number(res.headers.get('Retry-After') || 0);
    const wait = retry > 60 ? `${Math.ceil(retry / 60)} minutes` : `${retry || 30} seconds`;
    throw new GuestSessionError(`Too many guest sessions from here. Try again in ${wait}.`, 429);
  }
  if (!res.ok) {
    throw new GuestSessionError('Could not start a guest session.', res.status);
  }

  const body = await res.json();
  if (!body?.token) {
    throw new GuestSessionError('The server returned an unusable guest session.', res.status);
  }

  primeGuestToken(body.token, Number(body.expires_in) || 0);
  return { guestId: body.guest_id, expiresIn: Number(body.expires_in) || 0 };
}

/** Drop the cached guest token. */
export function endGuestSession(): void {
  clearAuthToken();
}
