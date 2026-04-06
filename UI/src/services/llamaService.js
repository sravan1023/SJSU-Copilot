const GROQ_API_URL = 'https://api.groq.com/openai/v1/chat/completions';

const MODELS = {
  '8b': 'llama-3.3-70b-versatile',
  '70b': 'llama-3.3-70b-versatile',
};

const SYSTEM_PROMPT = `You are SJSU Copilot, a helpful AI assistant for San Jose State University students.
You help with questions about academics, campus life, degree requirements, registration, internships, and more.
Be concise, friendly, and accurate. Use markdown formatting when helpful (bold, bullet points, numbered lists).
If you don't know something specific to SJSU, say so honestly rather than making things up.`;

/**
 * Send a chat message to Llama 3 via Groq API and stream the response.
 *
 * @param {Object} options
 * @param {Array<{role: string, content: string}>} options.messages - conversation history
 * @param {string} options.model - '8b' or '70b'
 * @param {(chunk: string) => void} options.onChunk - called with each text chunk
 * @param {AbortSignal} [options.signal] - optional abort signal
 * @returns {Promise<string>} full response text
 */
export async function sendMessage({ messages, model = '8b', onChunk, signal }) {
  const apiKey = import.meta.env.VITE_GROQ_API_KEY;
  if (!apiKey) {
    throw new Error('VITE_GROQ_API_KEY is not set. Get a free key at https://console.groq.com');
  }

  const modelId = MODELS[model] || MODELS['8b'];

  const body = {
    model: modelId,
    messages: [{ role: 'system', content: SYSTEM_PROMPT }, ...messages],
    stream: true,
    temperature: 0.7,
    max_tokens: 2048,
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

  return full;
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
