import { authedJsonHeaders } from '../../services/authToken';

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000';

export interface Snapshot {
  term: string;
  source_url: string | null;
  page_last_updated: string | null;
  fetched_at: string | null;
  verified_at: string | null;
  stale: boolean;
}

export interface TermChoice {
  term: string | null;
  label: string | null;
  how: string;
  loaded: boolean;
}

export interface TermsPayload {
  today: string;
  terms: { term: string; label: string; snapshot: Snapshot | null }[];
  this: TermChoice;
  next: TermChoice;
}

export interface DeadlineEvent {
  category: 'registrar' | 'academic';
  label_raw: string;
  date_raw: string;
  start_date: string | null;
  end_date: string | null;
  event_keys: string[];
  passed: boolean;
}

export interface DeadlinesPayload {
  term: string;
  label: string;
  status: 'ok' | 'not_loaded';
  how: string;
  snapshot: Snapshot | null;
  academic_snapshot: Snapshot | null;
  events: DeadlineEvent[];
}

/** status 0 means the request never got an answer (network error). */
export class RegistrationApiError extends Error {
  status: number;
  retryAfter: number;
  constructor(status: number, message: string, retryAfter = 0) {
    super(message);
    this.name = 'RegistrationApiError';
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const headers = await authedJsonHeaders();
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { headers, signal });
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e;
    throw new RegistrationApiError(0, 'Could not reach the server');
  }
  if (!res.ok) {
    throw new RegistrationApiError(
      res.status,
      `Request failed (${res.status})`,
      Number(res.headers.get('Retry-After') || 0),
    );
  }
  try {
    return (await res.json()) as T;
  } catch {
    throw new RegistrationApiError(0, 'Unreadable response from the server');
  }
}

export function fetchTerms(signal?: AbortSignal): Promise<TermsPayload> {
  return getJson<TermsPayload>('/api/registration/terms', signal);
}

export function fetchDeadlines(term?: string, signal?: AbortSignal): Promise<DeadlinesPayload> {
  const q = term ? `?term=${encodeURIComponent(term)}` : '';
  return getJson<DeadlinesPayload>(`/api/registration/deadlines${q}`, signal);
}
