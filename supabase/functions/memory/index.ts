// ============================================================
// Memory Edge Function
//
// HTTP endpoints for the memory system.
// Called by the chat service layer during the request lifecycle.
//
// Endpoints:
//   POST /retrieve  — get memory context for a conversation
//   POST /process   — extract and store memories after an exchange
// ============================================================

import { createClient } from 'https://esm.sh/@supabase/supabase-js@2';
import { MemoryService } from './service.ts';

const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'POST,OPTIONS',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
};

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...CORS_HEADERS },
  });
}

/**
 * Minimal LLM call wrapper.
 * Replace this with your actual LLM provider call.
 */
async function llmCall(systemPrompt: string, userPrompt: string): Promise<string> {
  const apiKey = Deno.env.get('ANTHROPIC_API_KEY') ?? Deno.env.get('OPENAI_API_KEY');
  if (!apiKey) throw new Error('No LLM API key configured');

  // Using Anthropic Claude as the extraction model
  const anthropicKey = Deno.env.get('ANTHROPIC_API_KEY');
  if (anthropicKey) {
    const resp = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': anthropicKey,
        'anthropic-version': '2023-06-01',
      },
      body: JSON.stringify({
        model: 'claude-haiku-4-5-20251001',
        max_tokens: 1024,
        system: systemPrompt,
        messages: [{ role: 'user', content: userPrompt }],
      }),
    });
    const data = await resp.json();
    return data.content?.[0]?.text ?? '{}';
  }

  // Fallback: OpenAI
  const resp = await fetch('https://api.openai.com/v1/chat/completions', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${apiKey}`,
    },
    body: JSON.stringify({
      model: 'gpt-4o-mini',
      messages: [
        { role: 'system', content: systemPrompt },
        { role: 'user', content: userPrompt },
      ],
      response_format: { type: 'json_object' },
    }),
  });
  const data = await resp.json();
  return data.choices?.[0]?.message?.content ?? '{}';
}

Deno.serve(async (request: Request) => {
  if (request.method === 'OPTIONS') {
    return new Response(null, { headers: CORS_HEADERS });
  }

  try {
    const authHeader = request.headers.get('authorization');
    if (!authHeader) {
      return jsonResponse(401, { error: 'Missing authorization header' });
    }

    // Create Supabase client with the user's JWT
    const supabase = createClient(
      Deno.env.get('SUPABASE_URL')!,
      Deno.env.get('SUPABASE_ANON_KEY')!,
      { global: { headers: { authorization: authHeader } } },
    );

    // Get the authenticated user
    const { data: { user }, error: authError } = await supabase.auth.getUser();
    if (authError || !user) {
      return jsonResponse(401, { error: 'Unauthorized' });
    }

    const service = new MemoryService(supabase, llmCall);
    const url = new URL(request.url);
    const path = url.pathname.split('/').pop();

    if (request.method !== 'POST') {
      return jsonResponse(405, { error: 'Method not allowed' });
    }

    const body = await request.json();

    switch (path) {
      case 'retrieve': {
        // Retrieve memory context for a conversation
        const { conversation_id } = body;
        if (!conversation_id) {
          return jsonResponse(400, { error: 'conversation_id required' });
        }
        const result = await service.retrieveAndCompose(user.id, conversation_id);
        return jsonResponse(200, result);
      }

      case 'process': {
        // Process a user-assistant exchange
        const { conversation_id, message_id, user_message, assistant_message } = body;
        if (!conversation_id || !message_id || !user_message || !assistant_message) {
          return jsonResponse(400, {
            error: 'conversation_id, message_id, user_message, and assistant_message required',
          });
        }
        await service.processExchange(
          user.id,
          conversation_id,
          message_id,
          user_message,
          assistant_message,
        );
        return jsonResponse(200, { success: true });
      }

      case 'assign-project': {
        // Assign a conversation to a project (and promote high-value memories)
        const { conversation_id, project_id, promote_min_importance } = body;
        if (!conversation_id || !project_id) {
          return jsonResponse(400, { error: 'conversation_id and project_id required' });
        }
        await service.assignConversationToProject(
          conversation_id,
          project_id,
          promote_min_importance ?? 7,
        );
        return jsonResponse(200, { success: true });
      }

      case 'promote': {
        // Manually promote a conversation memory to project scope
        const { memory_id, project_id } = body;
        if (!memory_id || !project_id) {
          return jsonResponse(400, { error: 'memory_id and project_id required' });
        }
        const newId = await service.promoteMemory(memory_id, project_id);
        return jsonResponse(200, { success: true, promoted_memory_id: newId });
      }

      default:
        return jsonResponse(404, { error: `Unknown endpoint: ${path}` });
    }
  } catch (err) {
    console.error('Memory function error:', err);
    return jsonResponse(500, { error: 'Internal server error' });
  }
});
