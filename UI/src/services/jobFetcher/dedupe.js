import { buildDedupeHash } from './normalize';

const toKey = (value) => `${value || ''}`.trim().toLowerCase();

const normalizeTitleForFuzzy = (title = '') =>
  toKey(title)
    .replace(/\b(internship|intern|new\s*grad|full\s*time|part\s*time|engineer|developer)\b/g, '')
    .replace(/[^a-z0-9\s]/g, '')
    .replace(/\s+/g, ' ')
    .trim();

const makeFuzzySignature = (title, company) => {
  const cleanTitle = normalizeTitleForFuzzy(title);
  const cleanCompany = toKey(company).replace(/\b(inc|llc|corp|corporation|ltd)\b/g, '').trim();
  return `${cleanTitle}|${cleanCompany}`;
};

export const dedupeJobs = (jobs) => {
  const byApplyUrl = new Set();
  const byHash = new Set();
  const byFuzzy = new Set();
  const unique = [];

  for (const job of jobs) {
    const applyKey = toKey(job.apply_url);
    const hashKey = job.dedupe_hash || buildDedupeHash(job.title, job.company);
    const fuzzyKey = makeFuzzySignature(job.title, job.company);

    if (applyKey && byApplyUrl.has(applyKey)) continue;
    if (byHash.has(hashKey)) continue;
    if (fuzzyKey && byFuzzy.has(fuzzyKey)) continue;

    if (applyKey) byApplyUrl.add(applyKey);
    byHash.add(hashKey);
    if (fuzzyKey) byFuzzy.add(fuzzyKey);
    unique.push({ ...job, dedupe_hash: hashKey });
  }

  return unique;
};
