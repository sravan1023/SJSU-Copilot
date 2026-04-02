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

function isAuthorized(request: Request): boolean {
  const expectedToken = Deno.env.get('PIPELINE_TRIGGER_TOKEN');
  if (!expectedToken) {
    return true;
  }

  const providedToken = request.headers.get('x-pipeline-token');
  return providedToken === expectedToken;
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
