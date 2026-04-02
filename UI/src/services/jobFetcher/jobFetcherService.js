import {
  createJobFetchRun,
  getExistingJobMatches,
  insertJobListings,
  updateJobFetchRun,
} from '../../supabaseHelpers';
import { buildDedupeHash } from './normalize';

const WORKER_API_URL = import.meta.env.VITE_JOB_FETCHER_API_URL;

const fetchWithTimeout = async (url, options = {}, timeoutMs = 15000) => {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timeoutId);
  }
};

const getWorkerJobs = async ({ query }) => {
  const url = new URL(WORKER_API_URL);
  if (query?.role) url.searchParams.set('role', query.role);
  if (query?.count) url.searchParams.set('count', String(query.count));
  if (query?.experienceLevel) url.searchParams.set('experienceLevel', query.experienceLevel);

  const response = await fetchWithTimeout(url.toString(), {}, 15000);
  if (!response.ok) throw new Error(`Worker fetch failed (${response.status})`);
  const body = await response.json();
  return Array.isArray(body.jobs) ? body.jobs : [];
};

const fetchBrowserFallbackJobs = async ({ query }) => {
  const { getJobSources } = await import('../../supabaseHelpers');
  const { fetchJobsFromSource } = await import('./adapters');
  const { dedupeJobs } = await import('./dedupe');
  const { normalizeJob } = await import('./normalize');

  const now = new Date();
  const { data: sources, error: sourcesError } = await getJobSources();
  if (sourcesError) throw sourcesError;

  const eligibleSources = (sources || []).filter((source) => source.enabled && (!source.next_fetch_at || new Date(source.next_fetch_at).getTime() <= now.getTime()));

  const jobsBySource = [];
  for (const source of eligibleSources) {
    const rawJobs = await fetchJobsFromSource(source, { query });
    jobsBySource.push(...rawJobs.map((job) => normalizeJob({ job, source })));
  }

  return dedupeJobs(jobsBySource);
};

export const runJobFetcherOnce = async ({ query = null } = {}) => {
  const now = new Date();
  const results = [];

  let jobs = [];
  let sourceCount = 0;

  try {
    if (WORKER_API_URL) {
      jobs = await getWorkerJobs({ query });
      sourceCount = 1;
      results.push({ sourceName: 'Cloudflare Worker', fetched: jobs.length, inserted: 0, deduped: 0, status: 'success' });
    } else {
      jobs = await fetchBrowserFallbackJobs({ query });
      sourceCount = 0;
      results.push({ sourceName: 'Browser fallback', fetched: jobs.length, inserted: 0, deduped: 0, status: 'success' });
    }
  } catch (error) {
    const message = error?.message || 'Unknown fetch error';
    results.push({ sourceName: WORKER_API_URL ? 'Cloudflare Worker' : 'Browser fallback', fetched: 0, inserted: 0, deduped: 0, status: 'failed', error: message });
    throw error;
  }

  const persistWorkerJobs = import.meta.env.VITE_JOB_FETCHER_PERSIST_WORKER_RESULTS === 'true';
  if (WORKER_API_URL && !persistWorkerJobs) {
    return {
      ranAt: now.toISOString(),
      sourceCount,
      results,
      jobs,
      persisted: false,
    };
  }

  const { data: run, error: runError } = await createJobFetchRun({
    sourceId: null,
    status: 'partial',
    startedAt: now.toISOString(),
  });
  if (runError) throw runError;

  const runId = run?.id || null;
  const finalJobs = jobs
    .filter((job) => job.title && job.company && job.apply_url)
    .map((job) => ({ ...job, fetched_at: now.toISOString() }));

  const dedupeHashes = finalJobs.map((job) => job.dedupe_hash || buildDedupeHash(job.title, job.company));
  const { data: existingRows, error: existingError } = await getExistingJobMatches({ dedupeHashes });
  if (existingError) throw existingError;

  const existingHashSet = new Set((existingRows || []).map((row) => row.dedupe_hash));
  const insertableJobs = finalJobs.filter((job) => !existingHashSet.has(job.dedupe_hash || buildDedupeHash(job.title, job.company)));

  const { data: insertedRows, error: insertError } = await insertJobListings(insertableJobs);
  if (insertError) throw insertError;

  if (runId) {
    await updateJobFetchRun(runId, {
      status: 'success',
      fetched_count: finalJobs.length,
      inserted_count: insertedRows?.length || 0,
      deduped_count: Math.max(0, finalJobs.length - insertableJobs.length),
      completed_at: new Date().toISOString(),
    });
  }

  return {
    ranAt: now.toISOString(),
    sourceCount,
    results,
    jobs: finalJobs,
    persisted: true,
  };
};

export const runDueJobFetchCycle = async () => runJobFetcherOnce();
