import { executeInternJobsPipeline } from './pipeline.ts';

const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type, x-pipeline-token',
};

function jsonResponse(status: number, body: Record<string, unknown>): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      'Content-Type': 'application/json',
      ...CORS_HEADERS,
    },
  });
}

/** Constant-time string compare, so a caller cannot probe the token byte by byte. */
function tokensMatch(provided: string, expected: string): boolean {
  const a = new TextEncoder().encode(provided);
  const b = new TextEncoder().encode(expected);
  // Compare a fixed number of bytes regardless of length, then fold the length
  // difference into the result.
  let diff = a.length ^ b.length;
  const len = Math.max(a.length, b.length);
  for (let i = 0; i < len; i++) {
    diff |= (a[i] ?? 0) ^ (b[i] ?? 0);
  }
  return diff === 0;
}

function isAuthorized(request: Request): boolean {
  const expectedToken = Deno.env.get('PIPELINE_TRIGGER_TOKEN');
  // Fail closed. This function runs the pipeline with SUPABASE_SERVICE_ROLE_KEY
  // (see pipeline.ts), so an unset secret previously meant any unauthenticated
  // POST could trigger a full service-role run.
  if (!expectedToken) {
    console.error(
      'PIPELINE_TRIGGER_TOKEN is not set; refusing to run the pipeline. ' +
        'Set the secret with: supabase secrets set PIPELINE_TRIGGER_TOKEN=<value>',
    );
    return false;
  }

  const providedToken = request.headers.get('x-pipeline-token');
  if (!providedToken) return false;

  return tokensMatch(providedToken, expectedToken);
}

Deno.serve(async (request: Request) => {
  if (request.method === 'OPTIONS') {
    return new Response('ok', {
      status: 200,
      headers: CORS_HEADERS,
    });
  }

  if (request.method === 'GET') {
    return jsonResponse(200, {
      ok: true,
      service: 'intern-jobs-alert',
      timestamp: new Date().toISOString(),
    });
  }

  if (request.method !== 'POST') {
    return jsonResponse(405, {
      ok: false,
      error: 'Method not allowed. Use POST to run the pipeline.',
    });
  }

  if (!isAuthorized(request)) {
    return jsonResponse(401, {
      ok: false,
      error: 'Unauthorized request.',
    });
  }

  try {
    const result = await executeInternJobsPipeline();
    return jsonResponse(200, {
      ok: true,
      result,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error';
    return jsonResponse(500, {
      ok: false,
      error: message,
    });
  }
});
