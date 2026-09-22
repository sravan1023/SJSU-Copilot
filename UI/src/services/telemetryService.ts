/**
 * Client-side chat timings.
 *
 * The server can time retrieval and generation, but not what the user actually
 * waits through: how long until their own message appears, how long the
 * Supabase saves take, when the first answer token lands. Each send (or
 * regenerate / edit) records those as milliseconds since the user pressed send.
 *
 * Events are logged with console.debug in development and sent in batches to
 * the backend's /api/telemetry, which writes them to its log next to the
 * server's own "request timings" line for the same request_id.
 *
 * Nothing here records message text.
 */

const API_BASE: string = import.meta.env.VITE_API_BASE || 'http://localhost:8000';

const MAX_BATCH = 20; // the backend accepts up to 50 per request
const FLUSH_DELAY_MS = 10_000;

export type TurnKind = 'send' | 'regenerate' | 'edit';
export type TurnOutcome = 'ok' | 'error' | 'aborted';

export interface TurnTiming {
  kind: TurnKind;
  outcome: TurnOutcome;
  request_id: string | null;
  model: string | null;
  marks: Record<string, number>;
}

/** Absolute performance.now() marks from llamaService.sendMessage. */
export interface StreamTimings {
  send?: number;
  headers?: number;
  firstStatus?: number;
  firstToken?: number;
  done?: number;
}

const STREAM_MARKS: Array<[keyof StreamTimings, string]> = [
  ['send', 'request_sent'],
  ['headers', 'headers'],
  ['firstStatus', 'first_status'],
  ['firstToken', 'first_token'],
  ['done', 'stream_done'],
];

let queue: TurnTiming[] = [];
let flushTimer: ReturnType<typeof setTimeout> | null = null;

function record(event: TurnTiming): void {
  if (import.meta.env.DEV) console.debug('[timings]', event.kind, event.outcome, event.marks);

  queue.push(event);
  if (queue.length >= MAX_BATCH) {
    void flush();
  } else if (!flushTimer) {
    flushTimer = setTimeout(() => void flush(), FLUSH_DELAY_MS);
  }
}

/** Send whatever is queued. Failures are dropped: this is measurement, not data. */
async function flush(): Promise<void> {
  if (flushTimer) {
    clearTimeout(flushTimer);
    flushTimer = null;
  }
  if (queue.length === 0) return;

  const pending = queue;
  queue = [];
  for (let i = 0; i < pending.length; i += MAX_BATCH) {
    try {
      await fetch(`${API_BASE}/api/telemetry`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ events: pending.slice(i, i + MAX_BATCH) }),
        keepalive: true, // lets the pagehide flush finish after the tab closes
      });
    } catch {
      // backend unreachable; nothing to do
    }
  }
}

export interface FinishOptions {
  outcome: TurnOutcome;
  requestId?: string | null;
  model?: string | null;
  stream?: StreamTimings | null;
}

/**
 * Start timing one turn. Call mark() as each step completes and finish()
 * once at the end; later calls to either are ignored after finish().
 */
export function startTurn(kind: TurnKind) {
  const t0 = performance.now();
  const marks: Record<string, number> = {};
  let finished = false;
  const since = (t: number) => Math.max(0, Math.round((t - t0) * 10) / 10);

  return {
    mark(name: string): void {
      if (!finished && !(name in marks)) marks[name] = since(performance.now());
    },
    finish({ outcome, requestId = null, model = null, stream = null }: FinishOptions): void {
      if (finished) return;
      finished = true;
      if (stream) {
        for (const [key, name] of STREAM_MARKS) {
          const value = stream[key];
          if (typeof value === 'number') marks[name] = since(value);
        }
      }
      marks.total = since(performance.now());
      record({ kind, outcome, request_id: requestId, model, marks });
    },
  };
}

if (typeof window !== 'undefined') {
  window.addEventListener('pagehide', () => {
    void flush();
  });
}
