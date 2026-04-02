const REMOTE_STATUSES = new Set(['remote', 'hybrid', 'onsite', 'unknown']);
const JOB_TYPES = new Set(['internship', 'new_grad', 'full_time', 'part_time', 'contract', 'other']);

const toSlug = (value = '') =>
  value
    .toString()
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/(^-|-$)/g, '');

const parseDateOnly = (value) => {
  if (!value) return null;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed.toISOString().split('T')[0];
};

const normalizeRemoteStatus = (value) => {
  const normalized = toSlug(value);
  if (REMOTE_STATUSES.has(normalized)) return normalized;
  if (normalized.includes('remote')) return 'remote';
  if (normalized.includes('hybrid')) return 'hybrid';
  if (normalized.includes('on-site') || normalized.includes('onsite')) return 'onsite';
  return 'unknown';
};

const normalizeJobType = (value) => {
  const normalized = toSlug(value);
  if (JOB_TYPES.has(normalized)) return normalized;
  if (normalized.includes('intern')) return 'internship';
  if (normalized.includes('new-grad') || normalized.includes('entry')) return 'new_grad';
  if (normalized.includes('full')) return 'full_time';
  if (normalized.includes('part')) return 'part_time';
  if (normalized.includes('contract')) return 'contract';
  return 'other';
};

export const buildDedupeHash = (title = '', company = '') => {
  const clean = `${title}`.trim().toLowerCase() + '|' + `${company}`.trim().toLowerCase();
  let hash = 0;
  for (let i = 0; i < clean.length; i += 1) {
    hash = (hash << 5) - hash + clean.charCodeAt(i);
    hash |= 0;
  }
  return `h_${Math.abs(hash)}`;
};

export const normalizeJob = ({ job, source }) => {
  const safeTitle = (job.title || job.position || 'Untitled role').toString().trim();
  const safeCompany = (job.company || source.name || 'Unknown Company').toString().trim();
  const applyUrl = (job.apply_url || job.url || source.source_url || '').toString().trim();
  const idBase = `${safeTitle}-${safeCompany}-${applyUrl || Date.now()}`;

  return {
    external_id: job.id || job.external_id || toSlug(idBase),
    title: safeTitle,
    company: safeCompany,
    location: (job.location || '').toString().trim() || null,
    remote_status: normalizeRemoteStatus(job.remote_status || job.remote || ''),
    job_type: normalizeJobType(job.job_type || job.type || ''),
    salary: (job.salary || '').toString().trim() || null,
    experience_level: (job.experience_level || job.experience || '').toString().trim() || null,
    tags: Array.isArray(job.tags)
      ? job.tags.filter(Boolean).map((tag) => `${tag}`.toLowerCase())
      : [],
    description: (job.description || job.summary || '').toString().trim() || null,
    apply_url: applyUrl,
    source_name: source.name,
    source_url: source.source_url,
    posted_date: parseDateOnly(job.posted_date || job.pubDate || job.published_at),
    fetched_at: new Date().toISOString(),
    dedupe_hash: buildDedupeHash(safeTitle, safeCompany),
  };
};
