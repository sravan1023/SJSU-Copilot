import { runValidators } from './validatorService.js';
import { compilePolicy } from './policyCompiler.js';

const GROQ_API_URL = 'https://api.groq.com/openai/v1/chat/completions';

const MODELS = {
  '8b': 'llama-3.3-70b-versatile',
  '70b': 'llama-3.3-70b-versatile',
};

const BASE_SYSTEM_PROMPT = `You are SJSU Copilot, a helpful AI assistant for San Jose State University students.
You help with questions about academics, campus life, degree requirements, registration, internships, and more.
If you don't know something specific to SJSU, say so honestly rather than making things up.`;

/** Build the full system prompt via the policy compiler. */
function buildSystemPrompt(behavior) {
  const { prompt } = compilePolicy(BASE_SYSTEM_PROMPT, behavior);
  return prompt;
}

// BOUNDARIES constant kept for reference — the policy compiler owns the
// canonical Hard instruction list internally in policyCompiler.js.
const BOUNDARIES = null; // superseded by policyCompiler.js

/**
 * Resolve LLM generation parameters from behavior settings.
 * Maps response_length and priority_stack to temperature and max_tokens.
 *
 * @param {Object|null} behavior
 * @returns {{ temperature: number, max_tokens: number }}
 */
function resolveParams(behavior) {
  let temperature = 0.7;
  let max_tokens = 2048;

  if (!behavior) return { temperature, max_tokens };

  // Length drives base token budget and temperature
  switch (behavior.response_length) {
    case 'concise':
      max_tokens = 512;
      temperature = 0.5;
      break;
    case 'detailed':
      max_tokens = 4096;
      // temperature stays 0.7
      break;
    // 'balanced' — defaults above
  }

  // Priority stack adjustments (position-weighted)
  const stack = Array.isArray(behavior.priority_stack) ? behavior.priority_stack : [];

  const creativityIdx = stack.indexOf('creativity');
  if (creativityIdx >= 0 && creativityIdx < 3) {
    temperature += 0.15;
  }

  const accuracyIdx = stack.indexOf('accuracy');
  if (accuracyIdx >= 0 && accuracyIdx < 2) {
    temperature -= 0.1;
  }

  const speedIdx = stack.indexOf('speed');
  if (speedIdx >= 0 && speedIdx < 2) {
    max_tokens = Math.floor(max_tokens * 0.6);
  }

  return {
    temperature: Math.max(0.3, Math.min(1.0, temperature)),
    max_tokens,
  };
}

/**
 * Non-streaming repair call — rewrites a response according to a repair prompt.
 * Used by the Level 1 validator to condense over-long responses.
 *
 * @param {string} repairPrompt
 * @param {string} apiKey
 * @param {string} modelId
 * @param {AbortSignal} [signal]
 * @returns {Promise<string|null>}
 */
async function repairResponse(repairPrompt, apiKey, modelId, signal) {
  const res = await fetch(GROQ_API_URL, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify({
      model: modelId,
      messages: [
        { role: 'system', content: 'You are a response editor. Follow the instruction exactly and return only the rewritten text.' },
        { role: 'user', content: repairPrompt },
      ],
      stream: false,
      temperature: 0.3,
      max_tokens: 512,
    }),
    signal,
  });
  if (!res.ok) return null;
  const data = await res.json();
  return data.choices?.[0]?.message?.content?.trim() || null;
}

/**
 * Send a chat message to Llama 3 via Groq API and stream the response.
 *
 * @param {Object} options
 * @param {Array<{role: string, content: string}>} options.messages - conversation history
 * @param {string} options.model - '8b' or '70b'
 * @param {(chunk: string) => void} options.onChunk - called with each text chunk
 * @param {(text: string) => void} [options.onReplace] - called after validation if the final text differs from the stream
 * @param {AbortSignal} [options.signal] - optional abort signal
 * @returns {Promise<string>} final response text (may differ from streamed text after validation)
 */
export async function sendMessage({ messages, model = '8b', onChunk, onReplace, signal, behavior }) {
  const apiKey = import.meta.env.VITE_GROQ_API_KEY;
  if (!apiKey) {
    throw new Error('VITE_GROQ_API_KEY is not set. Get a free key at https://console.groq.com');
  }

  const modelId = MODELS[model] || MODELS['8b'];
  const { temperature, max_tokens } = resolveParams(behavior);

  const body = {
    model: modelId,
    messages: [{ role: 'system', content: buildSystemPrompt(behavior) }, ...messages],
    stream: true,
    temperature,
    max_tokens,
  };

  const res = await fetch(GROQ_API_URL, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify(body),
    signal,
  });

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`Groq API error (${res.status}): ${err}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let full = '';
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop(); // keep incomplete line in buffer

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed || !trimmed.startsWith('data: ')) continue;
      const data = trimmed.slice(6);
      if (data === '[DONE]') continue;

      try {
        const parsed = JSON.parse(data);
        const content = parsed.choices?.[0]?.delta?.content;
        if (content) {
          full += content;
          onChunk?.(content);
        }
      } catch {
        // skip malformed chunks
      }
    }
  }

  // ── Post-generation validation ───────────────────────────────────────────
  const validatorMeta = { validatorsRun: [], validatorsPassed: true, repairsApplied: [] };

  if (behavior && full) {
    const result = runValidators(full, behavior);
    validatorMeta.validatorsRun  = result.violations.map(v => v.rule);
    validatorMeta.validatorsPassed = result.action === null;

    if (result.action === 'rewrite' && result.repairPrompt) {
      // Level 1: auto-rewrite (non-streaming repair call, transparent to user)
      try {
        const repaired = await repairResponse(result.repairPrompt, apiKey, modelId, signal);
        if (repaired) {
          onReplace?.(repaired);
          full = repaired;
          validatorMeta.repairsApplied = ['rewrite'];
        }
      } catch {
        // Repair failed — fall through and keep the original streamed response
      }
    } else if (result.action === 'warn' && result.warningText) {
      // Level 2: append warning below the response
      const withWarning = full + result.warningText;
      onReplace?.(withWarning);
      full = withWarning;
      validatorMeta.repairsApplied = result.violations
        .filter(v => v.severity === 'medium')
        .map(v => v.rule);
    }
  }

  return validatorMeta;
}

/**
 * Generate a short chat title from the first user message.
 * Non-streaming, returns a plain string (3-6 words).
 */
export async function generateTitle(userMessage) {
  const apiKey = import.meta.env.VITE_GROQ_API_KEY;
  if (!apiKey) return null;

  const res = await fetch(GROQ_API_URL, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify({
      model: MODELS['8b'],
      messages: [
        {
          role: 'system',
          content: 'Generate a short chat title (3-6 words, no quotes, no punctuation at the end) that summarizes the user\'s message. Reply with ONLY the title, nothing else.',
        },
        { role: 'user', content: userMessage },
      ],
      stream: false,
      temperature: 0.4,
      max_tokens: 30,
    }),
  });

  if (!res.ok) return null;

  const data = await res.json();
  const title = data.choices?.[0]?.message?.content?.trim();
  return title || null;
}

export { MODELS };
