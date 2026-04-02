type JobQuery = {
  role?: string;
  count?: number;
  experienceLevel?: string;
};

type JobSource = {
  name: string;
  type: 'remoteok' | 'remotive' | 'arbeitnow' | 'greenhouse' | 'lever';
  url: string;
};

type NormalizedJob = {
  id: string;
  dedupe_hash: string;
  title: string;
  company: string;
  location: string | null;
  remote_status: 'remote' | 'hybrid' | 'onsite' | 'unknown';
  job_type: 'internship' | 'new_grad' | 'full_time' | 'part_time' | 'contract' | 'other';
  salary: string | null;
  experience_level: string | null;
  tags: string[];
  description: string | null;
  apply_url: string;
  source_name: string;
  source_url: string;
  posted_date: string | null;
  fetched_at: string;
};

const CORS_HEADERS = {
  'access-control-allow-origin': '*',
  'access-control-allow-headers': 'content-type, authorization',
  'access-control-allow-methods': 'GET, POST, OPTIONS',
};

const SOURCES: JobSource[] = [
  { name: 'RemoteOK', type: 'remoteok', url: 'https://remoteok.com/api' },
  { name: 'Remotive', type: 'remotive', url: 'https://remotive.com/api/remote-jobs' },
  { name: 'Arbeitnow', type: 'arbeitnow', url: 'https://www.arbeitnow.com/api/job-board-api' },
];

const json = (data: unknown, init: ResponseInit = {}) =>
  new Response(JSON.stringify(data), {
    ...init,
    headers: {
      'content-type': 'application/json; charset=utf-8',
      ...CORS_HEADERS,
      ...(init.headers || {}),
    },
  });

const parseNumber = (value: string | null, fallback: number) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

const normalizeRemoteStatus = (text: string) => {
  const value = text.toLowerCase();
  if (value.includes('remote')) return 'remote';
  if (value.includes('hybrid')) return 'hybrid';
  if (value.includes('onsite') || value.includes('on-site')) return 'onsite';
  return 'unknown';
};

const normalizeJobType = (text: string) => {
  const value = text.toLowerCase();
  if (value.includes('intern')) return 'internship';
  if (value.includes('new grad') || value.includes('new-grad') || value.includes('entry')) return 'new_grad';
  if (value.includes('part')) return 'part_time';
  if (value.includes('contract')) return 'contract';
  if (value.includes('full')) return 'full_time';
  return 'other';
};

const cleanHtml = (input: string) => input.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();

const roleMatches = (job: Pick<NormalizedJob, 'title' | 'description' | 'tags' | 'company'>, role?: string) => {
  if (!role) return true;
  const haystack = `${job.title} ${job.company} ${job.description || ''} ${(job.tags || []).join(' ')}`.toLowerCase();
  return role
    .toLowerCase()
    .split(/\s+/)
    .filter((token) => token.length > 2)
    .some((token) => haystack.includes(token));
};

const experienceMatches = (job: Pick<NormalizedJob, 'title' | 'description' | 'tags'>, experienceLevel?: string) => {
  if (!experienceLevel) return true;
  const haystack = `${job.title} ${job.description || ''} ${(job.tags || []).join(' ')}`.toLowerCase();
  if (experienceLevel === 'intern') return haystack.includes('intern');
  if (experienceLevel === 'new-grad') return ['new grad', 'graduate', 'entry', 'junior', 'associate'].some((kw) => haystack.includes(kw));
  return ['entry', 'junior', 'new grad', 'associate', 'early career', 'graduate'].some((kw) => haystack.includes(kw));
};

const hashId = async (input: string) => {
  const bytes = new TextEncoder().encode(input);
  const digest = await crypto.subtle.digest('SHA-1', bytes);
  return Array.from(new Uint8Array(digest)).map((byte) => byte.toString(16).padStart(2, '0')).join('').slice(0, 20);
};

const buildDedupeHash = async (title: string, company: string) => hashId(`${title.toLowerCase().trim()}|${company.toLowerCase().trim()}`);

const normalizeRemoteOk = async (rows: any[], source: JobSource): Promise<NormalizedJob[]> => {
  return Promise.all(
    rows.slice(1).map(async (row) => ({
      id: String(row.id || row.slug || ''),
      dedupe_hash: await buildDedupeHash(String(row.position || row.title || ''), String(row.company || '')),
      title: row.position || row.title || '',
      company: row.company || '',
      location: row.location || 'Remote',
      remote_status: normalizeRemoteStatus(`${row.location || ''} remote`),
      job_type: normalizeJobType(`${row.tags || []} ${row.position || ''}`),
      salary: null,
      experience_level: null,
      tags: Array.isArray(row.tags) ? row.tags.map((tag: string) => tag.toLowerCase()) : [],
      description: cleanHtml(row.description || ''),
      apply_url: row.url || `https://remoteok.com/${row.slug || row.id}`,
      source_name: source.name,
      source_url: source.url,
      posted_date: row.date ? String(row.date).slice(0, 10) : null,
      fetched_at: new Date().toISOString(),
    })),
  );
};

const normalizeRemotive = async (body: any, source: JobSource): Promise<NormalizedJob[]> => {
  const jobs = Array.isArray(body.jobs) ? body.jobs : [];
  return Promise.all(
    jobs.map(async (row: any) => ({
      id: String(row.id || row.url || ''),
      title: row.title || '',
      company: row.company_name || '',
      location: row.candidate_required_location || 'Remote',
      remote_status: normalizeRemoteStatus(`${row.candidate_required_location || ''} remote`),
      job_type: normalizeJobType(`${row.job_type || ''} ${row.title || ''}`),
      salary: row.salary || null,
      experience_level: null,
      tags: Array.isArray(row.tags) ? row.tags.map((tag: string) => tag.toLowerCase()) : [],
      description: cleanHtml(row.description || ''),
      apply_url: row.url || '',
      dedupe_hash: await buildDedupeHash(String(row.title || ''), String(row.company_name || '')),
      source_name: source.name,
      source_url: source.url,
      posted_date: row.publication_date ? String(row.publication_date).slice(0, 10) : null,
      fetched_at: new Date().toISOString(),
    })),
  );
};

const normalizeArbeitnow = async (body: any, source: JobSource): Promise<NormalizedJob[]> => {
  const jobs = Array.isArray(body.data) ? body.data : [];
  return Promise.all(
    jobs.map(async (row: any) => ({
      id: String(row.slug || row.url || ''),
      title: row.title || '',
      company: row.company_name || '',
      location: row.location || 'Remote',
      remote_status: normalizeRemoteStatus(`${row.location || ''} remote`),
      job_type: normalizeJobType(`${row.title || ''} ${row.tags || []}`),
      salary: null,
      experience_level: null,
      tags: Array.isArray(row.tags) ? row.tags.map((tag: string) => tag.toLowerCase()) : [],
      description: cleanHtml(row.description || ''),
      apply_url: row.url || '',
      dedupe_hash: await buildDedupeHash(String(row.title || ''), String(row.company_name || '')),
      source_name: source.name,
      source_url: source.url,
      posted_date: row.created_at ? String(row.created_at).slice(0, 10) : null,
      fetched_at: new Date().toISOString(),
    })),
  );
};

const fetchSource = async (source: JobSource) => {
  const response = await fetch(source.url, {
    headers: {
      'user-agent': 'SJSU-Job-Fetcher/1.0',
      accept: 'application/json,text/plain,*/*',
    },
  });
  if (!response.ok) throw new Error(`${source.name} failed (${response.status})`);
  const body = await response.json();

  switch (source.type) {
    case 'remoteok':
      return normalizeRemoteOk(body, source);
    case 'remotive':
      return normalizeRemotive(body, source);
    case 'arbeitnow':
      return normalizeArbeitnow(body, source);
    default:
      return [];
  }
};

const dedupeJobs = (jobs: NormalizedJob[]) => {
  const seen = new Set<string>();
  const unique: NormalizedJob[] = [];

  for (const job of jobs) {
    const key = `${job.apply_url.toLowerCase()}|${job.title.toLowerCase()}|${job.company.toLowerCase()}`;
    if (seen.has(key)) continue;
    seen.add(key);
    unique.push(job);
  }

  return unique;
};

const filterJobs = (jobs: NormalizedJob[], query: JobQuery) => {
  const role = query.role?.trim();
  const level = query.experienceLevel?.trim();
  return jobs.filter((job) => roleMatches(job, role) && experienceMatches(job, level));
};

async function handleFetch(request: Request) {
  const url = new URL(request.url);
  const query: JobQuery = {
    role: url.searchParams.get('role') || undefined,
    count: parseNumber(url.searchParams.get('count'), 30),
    experienceLevel: url.searchParams.get('experienceLevel') || undefined,
  };

  const sourceResults = await Promise.allSettled(SOURCES.map((source) => fetchSource(source)));
  const jobs = sourceResults
    .flatMap((result, index) => (result.status === 'fulfilled' ? result.value : []))
    .filter(Boolean) as NormalizedJob[];

  const filtered = dedupeJobs(filterJobs(jobs, query)).slice(0, Math.max(1, Math.min(100, query.count || 30)));

  return json({
    query,
    sourceCount: SOURCES.length,
    jobs: filtered,
    fetched_at: new Date().toISOString(),
    sources: SOURCES.map((source, index) => ({
      name: source.name,
      ok: sourceResults[index].status === 'fulfilled',
      error: sourceResults[index].status === 'rejected' ? String(sourceResults[index].reason) : null,
    })),
  });
}

export default {
  async fetch(request: Request) {
    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: CORS_HEADERS });
    }

    const url = new URL(request.url);
    if (url.pathname === '/fetch' && request.method === 'GET') {
      try {
        return await handleFetch(request);
      } catch (error) {
        return json({ error: error instanceof Error ? error.message : 'Worker fetch failed' }, { status: 500 });
      }
    }

    return json({ ok: true, name: 'sjsu-job-fetcher' });
  },
};
