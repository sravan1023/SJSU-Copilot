/**
 * LLM Service — calls the FastAPI backend instead of Groq directly.
 *
 * The backend handles: system prompt compilation, Groq streaming,
 * post-generation validation, and repair rewrites.
 */

import { authedJsonHeaders } from './authToken';

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000';

/**
 * The backend understands two model keys, 'fast' and 'quality'. The concrete
 * model id behind each is server configuration (MODEL_FAST / MODEL_QUALITY), so it is deliberately
 * not duplicated here — the previous copy of this map claimed two different
 * options while pointing both at the same 70B model.
 *
 * The legacy '8b'/'70b' keys are still accepted by the backend.
 */
const DEFAULT_MODEL_KEY = 'quality';

/**
 * Turn a non-2xx response into something a person can act on.
 *
 * The three access statuses are named, because they mean genuinely different
 * things and the UI's right response differs for each:
 *
 *   401  the token is missing or has expired      -> start over
 *   403  the token is fine, the account is not    -> sign in
 *   429  the token and account are fine, slow down
 *
 * Until Phase 2 all three came out as "Backend error (4xx): ..." alongside
 * genuine 500s, so a guest hitting an account-only endpoint read the same as a
 * crash. 403 in particular must not read as "try again".
 */
async function describeHttpError(res) {
  let raw = '';
  try {
    raw = await res.text();
  } catch {
    // body unreadable — status alone will have to do
  }

  let detail = raw.slice(0, 300);
  try {
    const body = JSON.parse(raw);
    if (typeof body?.detail === 'string') {
      detail = body.detail;
    } else if (Array.isArray(body?.detail)) {
      // 422 from the request-bound validators
      detail = body.detail.map(d => d?.msg).filter(Boolean).join('; ') || detail;
    }
  } catch {
    // not JSON — keep the raw text
  }

  if (res.status === 401) {
    return 'Your session has expired. Reload the page to continue.';
  }
  if (res.status === 403) {
    return 'Sign in with an SJSU account to use this.';
  }
  if (res.status === 429) {
    const retry = Number(res.headers.get('Retry-After') || 0);
    const wait = retry > 60 ? `${Math.ceil(retry / 60)} minutes` : `${retry || 30} seconds`;
    return `You're sending messages faster than the assistant can keep up. Try again in ${wait}.`;
  }

  return `Backend error (${res.status})${detail ? `: ${detail}` : ''}`;
}

/**
 * An Error that keeps the stream's timings and request id, so a failed turn is
 * still measurable (telemetryService) and can be matched to the server's log.
 */
function streamFailure(message, timings, requestId, status = null) {
  const error = new Error(message);
  error.timings = timings;
  error.requestId = requestId;
  // The status is carried so a caller can branch on it -- offering a sign-in
  // for 403, say -- rather than matching on the message text.
  error.status = status;
  return error;
}

/** Normalise an SSE error payload, which may be a string or a {code, retry_after} object. */
function describeStreamError(error) {
  if (typeof error === 'string') return error;
  if (error?.code === 'rate_limited') {
    const wait = error.retry_after ? ` Retry in ${error.retry_after}s.` : '';
    return `The model provider is rate limited.${wait}`;
  }
  return error?.message || 'The model stopped unexpectedly.';
}

/**
 * Send a chat message via the backend and stream the response.
 *
 * @param {Object} options
 * @param {Array<{role: string, content: string}>} options.messages - conversation history
 * @param {string} options.model - 'fast' or 'quality'
 * @param {(chunk: string) => void} options.onChunk - called with each text chunk
 * @param {(text: string) => void} [options.onReplace] - called if validators replace the response
 * @param {(status: string) => void} [options.onStatus] - progress before the first answer token
 * @param {AbortSignal} [options.signal] - optional abort signal
 * @param {Object} [options.behavior] - behavior settings
 * @param {string} [options.memoryPrompt] - memory context to inject
 * @returns {Promise<Object>} validator metadata, plus `timings`
 */
export async function sendMessage({ messages, model = DEFAULT_MODEL_KEY, onChunk, onReplace, onStatus, signal, behavior, memoryPrompt, audience }) {
  // Marks for the latency work. `headers` is the dead-air metric: how long the
  // browser waits for response headers, which is where retrieval used to sit.
  const timings = { send: performance.now() };

  // Normally a cache read costing one microtask. It only awaits real work when
  // the token is near expiry, and `auth` is marked so that cost shows up as
  // itself rather than inflating the `headers` dead-air metric below.
  const headers = await authedJsonHeaders();
  timings.auth = performance.now();

  const res = await fetch(`${API_BASE}/api/chat`, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      messages,
      model,
      behavior: behavior || null,
      memory_prompt: memoryPrompt || null,
      // Selects prompt text and which domains retrieval prefers. The server
      // does not trust it for anything authorization-shaped.
      audience: audience || null,
    }),
    signal,
  });

  timings.headers = performance.now();

  if (!res.ok) {
    throw streamFailure(await describeHttpError(res), timings, null, res.status);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let streamError = null;
  // Sent in the first status frame, so it is known even if the stream fails.
  let requestId = null;
  let validatorMeta = {
    validatorsRun: [],
    validatorsPassed: true,
    repairsApplied: [],
    sources: [],
  };

  read: while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop();

    for (const line of lines) {
      const trimmed = line.trim();
      // Keepalive frames are SSE comments (': ping') and fall out here too.
      if (!trimmed || !trimmed.startsWith('data: ')) continue;

      // Parsing is separated from dispatch on purpose. These used to share one
      // try block, so a thrown error frame was caught by the malformed-chunk
      // handler and dropped — every upstream failure looked like an empty but
      // successful answer, which the caller then persisted.
      let parsed;
      try {
        parsed = JSON.parse(trimmed.slice(6));
      } catch {
        continue; // genuinely malformed SSE chunk
      }

      if (parsed.error) {
        streamError = parsed.error;
        break read;
      }

      if (typeof parsed.token === 'string') {
        if (timings.firstToken === undefined) timings.firstToken = performance.now();
        onChunk?.(parsed.token);
      } else if (typeof parsed.replace === 'string') {
        onReplace?.(parsed.replace);
      } else if (typeof parsed.status === 'string') {
        if (timings.firstStatus === undefined) timings.firstStatus = performance.now();
        if (!requestId && typeof parsed.request_id === 'string') requestId = parsed.request_id;
        onStatus?.(parsed.status);
      } else if (parsed.done) {
        validatorMeta = {
          validatorsRun: parsed.validators_run || [],
          validatorsPassed: parsed.validators_passed ?? true,
          repairsApplied: parsed.repairs_applied || [],
          sources: parsed.sources || [],
          requestId: parsed.request_id || requestId,
        };
      }
    }
  }

  timings.done = performance.now();

  if (streamError) {
    // Release the connection; we are not reading the rest of the stream.
    reader.cancel().catch(() => {});
    throw streamFailure(describeStreamError(streamError), timings, requestId);
  }

  return { ...validatorMeta, timings };
}

/**
 * Generate a short chat title from the first user message.
 * Non-streaming, returns a plain string (3-6 words).
 */
export async function generateTitle(userMessage) {
  try {
    const res = await fetch(`${API_BASE}/api/generate-title`, {
      method: 'POST',
      headers: await authedJsonHeaders(),
      body: JSON.stringify({ message: userMessage }),
    });

    if (!res.ok) return null;

    const data = await res.json();
    return data.title || null;
  } catch {
    return null;
  }
}

/**
 * Fetch the auto-detected behavior for a conversation.
 * Used by behavior panels to show the baseline.
 */
export async function fetchAutoBehavior(messages) {
  try {
    const res = await fetch(`${API_BASE}/api/auto-behavior`, {
      method: 'POST',
      headers: await authedJsonHeaders(),
      body: JSON.stringify({ messages }),
    });

    if (!res.ok) return null;

    const data = await res.json();
    return data;
  } catch {
    return null;
  }
}

export { DEFAULT_MODEL_KEY };
