"""
Job Fetcher — ported from UI/src/services/jobFetcher/

Fetches intern/new-grad job listings from:
  1. Cloudflare Worker (JOB_FETCHER_API_URL env var) — primary path
  2. Job sources table in Supabase — fallback (API and RSS sources)

Normalizes, dedupes, and persists results to the job_listings table.
"""
import hashlib
import os
import re
from datetime import datetime, timezone
from typing import Any
from xml.etree import ElementTree

import httpx

# ── Env ───────────────────────────────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
JOB_FETCHER_API_URL = os.getenv("JOB_FETCHER_API_URL", "")
PERSIST_WORKER_RESULTS = os.getenv("JOB_FETCHER_PERSIST_WORKER_RESULTS", "false").lower() == "true"

FETCH_TIMEOUT = 15.0

# ── Normalize helpers ─────────────────────────────────────────────────────────

_REMOTE_STATUSES = {"remote", "hybrid", "onsite", "unknown"}
_JOB_TYPES = {"internship", "new_grad", "full_time", "part_time", "contract", "other"}


def _to_slug(value: str) -> str:
    s = str(value).strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def _parse_date_only(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        return value
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            n = float(value)
            ts = n if n > 1e10 else n * 1000
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except (ValueError, OSError, OverflowError):
        return None


def _normalize_remote_status(value: str) -> str:
    slug = _to_slug(value)
    if slug in _REMOTE_STATUSES:
        return slug
    if "remote" in slug:
        return "remote"
    if "hybrid" in slug:
        return "hybrid"
    if "on-site" in slug or "onsite" in slug:
        return "onsite"
    return "unknown"


def _normalize_job_type(value: str) -> str:
    slug = _to_slug(value)
    if slug in _JOB_TYPES:
        return slug
    if "intern" in slug:
        return "internship"
    if "new-grad" in slug or "entry" in slug:
        return "new_grad"
    if "full" in slug:
        return "full_time"
    if "part" in slug:
        return "part_time"
    if "contract" in slug:
        return "contract"
    return "other"


def build_dedupe_hash(title: str, company: str) -> str:
    clean = f"{title}".strip().lower() + "|" + f"{company}".strip().lower()
    return "h_" + hashlib.md5(clean.encode()).hexdigest()[:12]


def normalize_job(job: dict, source: dict) -> dict:
    title = str(job.get("title") or job.get("position") or "Untitled role").strip()
    company = str(job.get("company") or source.get("name") or "Unknown Company").strip()
    apply_url = str(job.get("apply_url") or job.get("url") or source.get("source_url") or "").strip()
    id_base = f"{title}-{company}-{apply_url or datetime.now().isoformat()}"

    return {
        "external_id": job.get("id") or job.get("external_id") or _to_slug(id_base),
        "title": title,
        "company": company,
        "location": str(job.get("location") or "").strip() or None,
        "remote_status": _normalize_remote_status(str(job.get("remote_status") or job.get("remote") or "")),
        "job_type": _normalize_job_type(str(job.get("job_type") or job.get("type") or "")),
        "salary": str(job.get("salary") or "").strip() or None,
        "experience_level": str(job.get("experience_level") or job.get("experience") or "").strip() or None,
        "tags": [str(t).lower() for t in (job.get("tags") or []) if t],
        "description": str(job.get("description") or job.get("summary") or "").strip() or None,
        "apply_url": apply_url,
        "source_name": source.get("name"),
        "source_url": source.get("source_url"),
        "posted_date": _parse_date_only(job.get("posted_date") or job.get("pubDate") or job.get("published_at")),
        "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
        "dedupe_hash": build_dedupe_hash(title, company),
    }


# ── Dedupe ────────────────────────────────────────────────────────────────────

def _normalize_title_for_fuzzy(title: str) -> str:
    t = title.strip().lower()
    t = re.sub(r"\b(internship|intern|new\s*grad|full\s*time|part\s*time|engineer|developer)\b", "", t)
    t = re.sub(r"[^a-z0-9\s]", "", t)
    return re.sub(r"\s+", " ", t).strip()


def _fuzzy_signature(title: str, company: str) -> str:
    clean_title = _normalize_title_for_fuzzy(title)
    clean_company = re.sub(r"\b(inc|llc|corp|corporation|ltd)\b", "", company.strip().lower()).strip()
    return f"{clean_title}|{clean_company}"


def dedupe_jobs(jobs: list[dict]) -> list[dict]:
    by_url: set[str] = set()
    by_hash: set[str] = set()
    by_fuzzy: set[str] = set()
    unique = []

    for job in jobs:
        url_key = str(job.get("apply_url") or "").strip().lower()
        hash_key = job.get("dedupe_hash") or build_dedupe_hash(job.get("title", ""), job.get("company", ""))
        fuzzy_key = _fuzzy_signature(job.get("title", ""), job.get("company", ""))

        if url_key and url_key in by_url:
            continue
        if hash_key in by_hash:
            continue
        if fuzzy_key and fuzzy_key in by_fuzzy:
            continue

        if url_key:
            by_url.add(url_key)
        by_hash.add(hash_key)
        if fuzzy_key:
            by_fuzzy.add(fuzzy_key)

        unique.append({**job, "dedupe_hash": hash_key})

    return unique


# ── Adapters ──────────────────────────────────────────────────────────────────

def _get_by_path(obj: Any, path: str | None) -> Any:
    if not path:
        return obj
    for key in path.split("."):
        if obj is None:
            return None
        if isinstance(obj, dict):
            obj = obj.get(key)
        else:
            return None
    return obj


def _build_url_with_query(source: dict, query: dict | None) -> str:
    url = source["source_url"]
    config = source.get("config") or {}
    query_map = config.get("query_map") or {}
    defaults = config.get("query_defaults") or {}

    params = dict(defaults)
    if query:
        if query.get("role") and query_map.get("role"):
            params[query_map["role"]] = query["role"]
        if query.get("count") and query_map.get("count"):
            params[query_map["count"]] = str(query["count"])

    if params:
        from urllib.parse import urlencode, urlparse, urlunparse, parse_qs
        parsed = urlparse(url)
        existing = parse_qs(parsed.query, keep_blank_values=True)
        existing.update({k: [v] for k, v in params.items()})
        flat = {k: v[0] for k, v in existing.items()}
        url = urlunparse(parsed._replace(query=urlencode(flat)))

    return url


def _matches_role(job: dict, role: str | None) -> bool:
    if not role:
        return True
    haystack = " ".join([
        str(job.get("title") or ""),
        str(job.get("description") or ""),
        str(job.get("company") or ""),
        " ".join(job.get("tags") or []),
    ]).lower()
    tokens = [t.strip() for t in role.lower().split() if len(t.strip()) > 2]
    if not tokens:
        return True
    return any(t in haystack for t in tokens)


def _matches_experience(job: dict, level: str | None) -> bool:
    if not level:
        return True
    haystack = " ".join([
        str(job.get("title") or ""),
        str(job.get("description") or ""),
        " ".join(job.get("tags") or []),
    ]).lower()
    if level == "intern":
        return "intern" in haystack
    if level in ("new-grad", "entry-level"):
        kws = ["new grad", "graduate", "entry", "junior", "associate", "early career"]
        return any(kw in haystack for kw in kws)
    return True


def _apply_query_filters(jobs: list[dict], query: dict | None) -> list[dict]:
    if not query:
        return jobs
    filtered = [j for j in jobs if _matches_role(j, query.get("role")) and _matches_experience(j, query.get("experienceLevel"))]
    count = max(1, min(100, int(query.get("count") or 30)))
    return filtered[:count]


async def _run_api_adapter(client: httpx.AsyncClient, source: dict, query: dict | None) -> list[dict]:
    url = _build_url_with_query(source, query)
    resp = await client.get(url, timeout=FETCH_TIMEOUT)
    resp.raise_for_status()
    body = resp.json()
    config = source.get("config") or {}
    rows = _get_by_path(body, config.get("response_path")) or body
    items = rows if isinstance(rows, list) else []
    field_map = config.get("field_map") or {}

    mapped = []
    for item in items:
        merged = {**item}
        for target, path in field_map.items():
            merged[target] = _get_by_path(item, path)
        if (merged.get("title") or merged.get("position")) and (merged.get("apply_url") or merged.get("url")):
            mapped.append(merged)

    return _apply_query_filters(mapped, query)


async def _run_rss_adapter(client: httpx.AsyncClient, source: dict) -> list[dict]:
    resp = await client.get(source["source_url"], timeout=FETCH_TIMEOUT)
    resp.raise_for_status()
    config = source.get("config") or {}
    root = ElementTree.fromstring(resp.text)
    items = root.findall(".//item")
    jobs = []
    for item in items:
        def _t(tag: str) -> str:
            el = item.find(tag)
            return el.text.strip() if el is not None and el.text else ""
        jobs.append({
            "title": _t("title"),
            "description": _t("description"),
            "url": _t("link"),
            "company": config.get("default_company") or source.get("name", ""),
            "location": config.get("default_location") or "",
            "remote_status": config.get("default_remote_status") or "unknown",
            "job_type": config.get("default_job_type") or "other",
            "tags": config.get("tag_map") or [],
            "pubDate": _t("pubDate"),
        })
    return jobs


async def fetch_jobs_from_source(client: httpx.AsyncClient, source: dict, query: dict | None) -> list[dict]:
    if not source.get("enabled"):
        return []
    stype = source.get("source_type", "")
    if stype == "api":
        return await _run_api_adapter(client, source, query)
    if stype == "rss":
        return await _run_rss_adapter(client, source)
    return []  # html/mock sources require a dedicated scraper


# ── Supabase helpers ──────────────────────────────────────────────────────────

def _sb_headers() -> dict:
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


async def _get_job_sources(client: httpx.AsyncClient) -> list[dict]:
    resp = await client.get(
        f"{SUPABASE_URL}/rest/v1/job_sources",
        params={"select": "*", "order": "created_at.desc"},
        headers=_sb_headers(),
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json()


async def _get_existing_matches(client: httpx.AsyncClient, dedupe_hashes: list[str]) -> list[dict]:
    if not dedupe_hashes:
        return []
    resp = await client.get(
        f"{SUPABASE_URL}/rest/v1/job_listings",
        params={"select": "id,apply_url,dedupe_hash", "dedupe_hash": f"in.({','.join(dedupe_hashes)})"},
        headers=_sb_headers(),
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json()


async def _insert_job_listings(client: httpx.AsyncClient, rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    sanitized = []
    seen_urls: set[str] = set()
    for row in rows:
        url_key = str(row.get("apply_url") or "").strip().lower()
        if not url_key or url_key in seen_urls:
            continue
        seen_urls.add(url_key)
        copy = {k: v for k, v in row.items() if k not in ("id", "dedupe_hash")}
        sanitized.append(copy)

    resp = await client.post(
        f"{SUPABASE_URL}/rest/v1/job_listings",
        json=sanitized,
        params={"on_conflict": "apply_url"},
        headers={**_sb_headers(), "Prefer": "resolution=ignore-duplicates,return=representation"},
        timeout=15.0,
    )
    resp.raise_for_status()
    return resp.json()


async def _create_fetch_run(client: httpx.AsyncClient, status: str, started_at: str) -> str | None:
    resp = await client.post(
        f"{SUPABASE_URL}/rest/v1/job_fetch_runs",
        json={"source_id": None, "status": status, "started_at": started_at},
        headers=_sb_headers(),
        timeout=10.0,
    )
    resp.raise_for_status()
    rows = resp.json()
    return rows[0]["id"] if rows else None


async def _update_fetch_run(client: httpx.AsyncClient, run_id: str, updates: dict) -> None:
    await client.patch(
        f"{SUPABASE_URL}/rest/v1/job_fetch_runs",
        json=updates,
        params={"id": f"eq.{run_id}"},
        headers=_sb_headers(),
        timeout=10.0,
    )


# ── Main entry point ──────────────────────────────────────────────────────────

async def run_job_fetch_cycle(query: dict | None = None) -> dict:
    """
    Fetch jobs from the configured source (Worker or Supabase sources),
    dedupe, and persist to job_listings.

    Returns a summary dict with counts.
    """
    now = datetime.now(tz=timezone.utc)
    jobs: list[dict] = []

    async with httpx.AsyncClient() as client:
        # 1. Fetch raw jobs
        if JOB_FETCHER_API_URL:
            from urllib.parse import urlencode, urlparse, urlunparse
            url = JOB_FETCHER_API_URL
            params = {}
            if query:
                if query.get("role"):
                    params["role"] = query["role"]
                if query.get("count"):
                    params["count"] = str(query["count"])
                if query.get("experienceLevel"):
                    params["experienceLevel"] = query["experienceLevel"]
            if params:
                parsed = urlparse(url)
                from urllib.parse import urlencode
                url = urlunparse(parsed._replace(query=urlencode(params)))

            resp = await client.get(url, timeout=FETCH_TIMEOUT)
            resp.raise_for_status()
            body = resp.json()
            jobs = body.get("jobs", []) if isinstance(body, dict) else body

            if not PERSIST_WORKER_RESULTS:
                return {
                    "ranAt": now.isoformat(),
                    "source": "worker",
                    "fetched": len(jobs),
                    "inserted": 0,
                    "deduped": 0,
                    "persisted": False,
                    "jobs": jobs,
                }
        else:
            # Fallback: pull from job_sources in Supabase
            sources = await _get_job_sources(client)
            for source in sources:
                if not source.get("enabled"):
                    continue
                next_fetch = source.get("next_fetch_at")
                if next_fetch and datetime.fromisoformat(next_fetch.replace("Z", "+00:00")) > now:
                    continue
                try:
                    raw = await fetch_jobs_from_source(client, source, query)
                    jobs.extend([normalize_job(j, source) for j in raw])
                except Exception:
                    pass
            jobs = dedupe_jobs(jobs)

        # 2. Filter out invalid jobs
        valid_jobs = [
            {**j, "fetched_at": now.isoformat()}
            for j in jobs
            if j.get("title") and j.get("company") and j.get("apply_url")
        ]

        if not valid_jobs:
            return {"ranAt": now.isoformat(), "source": "sources", "fetched": 0, "inserted": 0, "deduped": 0, "persisted": True}

        # 3. Create run record
        run_id = await _create_fetch_run(client, "partial", now.isoformat())

        # 4. Check existing hashes
        hashes = [j.get("dedupe_hash") or build_dedupe_hash(j["title"], j["company"]) for j in valid_jobs]
        existing = await _get_existing_matches(client, hashes)
        existing_set = {row["dedupe_hash"] for row in existing}
        new_jobs = [j for j in valid_jobs if (j.get("dedupe_hash") or build_dedupe_hash(j["title"], j["company"])) not in existing_set]

        # 5. Insert
        inserted = await _insert_job_listings(client, new_jobs)

        # 6. Close run record
        if run_id:
            await _update_fetch_run(client, run_id, {
                "status": "success",
                "fetched_count": len(valid_jobs),
                "inserted_count": len(inserted),
                "deduped_count": max(0, len(valid_jobs) - len(new_jobs)),
                "completed_at": datetime.now(tz=timezone.utc).isoformat(),
            })

        return {
            "ranAt": now.isoformat(),
            "source": "worker" if JOB_FETCHER_API_URL else "sources",
            "fetched": len(valid_jobs),
            "inserted": len(inserted),
            "deduped": max(0, len(valid_jobs) - len(new_jobs)),
            "persisted": True,
        }
