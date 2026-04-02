const getByPath = (obj, path) => {
  if (!path) return obj;
  return path.split('.').reduce((acc, key) => (acc ? acc[key] : undefined), obj);
};

const fetchWithTimeout = async (url, options = {}, timeoutMs = 10000) => {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { ...options, signal: controller.signal });
    return response;
  } finally {
    clearTimeout(timeoutId);
  }
};

const mapFields = (rawJob, fieldMap = {}) => {
  const mapped = {};
  Object.entries(fieldMap).forEach(([targetKey, sourcePath]) => {
    mapped[targetKey] = getByPath(rawJob, sourcePath);
  });
  return mapped;
};

const buildUrlWithQuery = (source, query = null) => {
  const url = new URL(source.source_url);
  const queryMap = source.config?.query_map || {};
  const defaults = source.config?.query_defaults || {};

  Object.entries(defaults).forEach(([key, value]) => {
    if (value !== undefined && value !== null && `${value}`.length > 0) {
      url.searchParams.set(key, value);
    }
  });

  if (query?.role && queryMap.role) {
    url.searchParams.set(queryMap.role, query.role);
  }

  if (query?.count && queryMap.count) {
    url.searchParams.set(queryMap.count, String(query.count));
  }

  return url.toString();
};

const matchesRole = (job, role) => {
  if (!role) return true;
  const haystack = `${job.title || ''} ${job.description || ''} ${job.company || ''} ${(job.tags || []).join(' ')}`.toLowerCase();
  const roleTokens = role
    .toLowerCase()
    .split(/\s+/)
    .map((token) => token.trim())
    .filter((token) => token.length > 2);

  if (!roleTokens.length) return true;
  return roleTokens.some((token) => haystack.includes(token));
};

const matchesExperience = (job, experienceLevel) => {
  if (!experienceLevel) return true;
  const haystack = `${job.title || ''} ${job.description || ''} ${(job.tags || []).join(' ')}`.toLowerCase();

  if (experienceLevel === 'intern') {
    return haystack.includes('intern');
  }

  if (experienceLevel === 'new-grad') {
    return ['new grad', 'graduate', 'entry', 'junior', 'associate'].some((kw) => haystack.includes(kw));
  }

  if (experienceLevel === 'entry-level') {
    return ['entry', 'junior', 'new grad', 'associate', 'early career', 'graduate'].some((kw) => haystack.includes(kw));
  }

  return true;
};

const applyQueryFilters = (jobs, query = null) => {
  if (!query) return jobs;

  const filtered = jobs.filter((job) => matchesRole(job, query.role) && matchesExperience(job, query.experienceLevel));
  const safeCount = Math.max(1, Math.min(100, Number(query.count) || 30));
  return filtered.slice(0, safeCount);
};

const runApiAdapter = async (source, options = {}) => {
  const endpoint = buildUrlWithQuery(source, options.query);
  const res = await fetchWithTimeout(endpoint, {}, 10000);
  if (!res.ok) throw new Error(`API source failed (${res.status})`);
  const body = await res.json();

  const responsePath = source.config?.response_path;
  const rows = getByPath(body, responsePath) || body;
  const list = Array.isArray(rows) ? rows : [];
  const fieldMap = source.config?.field_map || {};

  const mapped = list
    .map((item) => ({ ...item, ...mapFields(item, fieldMap) }))
    .filter((item) => (item.title || item.position) && (item.apply_url || item.url));

  return applyQueryFilters(mapped, options.query);
};

const runRssAdapter = async (source) => {
  const res = await fetchWithTimeout(source.source_url, {}, 10000);
  if (!res.ok) throw new Error(`RSS source failed (${res.status})`);
  const text = await res.text();

  const parser = new DOMParser();
  const xml = parser.parseFromString(text, 'application/xml');
  const items = Array.from(xml.querySelectorAll('item'));

  return items.map((item) => ({
    title: item.querySelector('title')?.textContent || '',
    description: item.querySelector('description')?.textContent || '',
    url: item.querySelector('link')?.textContent || '',
    company: source.config?.default_company || source.name,
    location: source.config?.default_location || '',
    remote_status: source.config?.default_remote_status || 'unknown',
    job_type: source.config?.default_job_type || 'other',
    tags: source.config?.tag_map || [],
    pubDate: item.querySelector('pubDate')?.textContent || '',
  }));
};

const runHtmlOrMockAdapter = async (source) => {
  if (source.source_type === 'mock') {
    return [];
  }

  if (source.source_type === 'html' && source.source_url) {
    return [
      {
        title: `Configure scraper for ${source.name}`,
        company: source.name,
        description: 'HTML scraping source added. Configure a backend/edge scraper adapter for production.',
        url: source.source_url,
        location: 'N/A',
        remote_status: 'unknown',
        job_type: 'other',
        tags: ['todo', 'scraper'],
      },
    ];
  }

  return [];
};

export const fetchJobsFromSource = async (source, options = {}) => {
  if (!source.enabled) return [];

  switch (source.source_type) {
    case 'api':
      return runApiAdapter(source, options);
    case 'rss':
      return runRssAdapter(source);
    case 'html':
    case 'mock':
      return runHtmlOrMockAdapter(source, options);
    default:
      return [];
  }
};
