"""PostgREST writes for the knowledge base.

There is no Postgres driver in this repo -- every database access goes over
PostgREST with the service key, so this module follows
`services/intern_jobs_pipeline.py`'s `_sb_headers` pattern, which is the only
precedent for it.

**Why `replace_document_chunks` is an RPC and not two requests.** Writing chunks
means deleting the old ones and inserting the new ones. Over PostgREST that is
two round trips with no transaction around them, and a crash in between leaves a
document that is present, hashed, apparently ingested and silently unretrievable
-- the worst failure shape available, because nothing reports it and the next run
sees an unchanged hash and skips the page. The plpgsql function makes it atomic
per document.

**Idempotency hashes the extracted text, not the HTML.** SJSU pages carry CSRF
nonces, build ids and "last updated" stamps that change on every fetch, so an
HTML hash would mark every page modified forever and re-embed the whole corpus
weekly for nothing.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import pathlib
from typing import Any

import httpx

logger = logging.getLogger(__name__)

SEEDS_PATH = pathlib.Path(__file__).resolve().parent / "seeds.json"

# Ingestion is offline, so it can afford to wait far longer than a chat turn.
HTTP_TIMEOUT = 30.0


class StoreError(RuntimeError):
    """A write failed. Ingestion records it and carries on with the next page."""


def _base_url() -> str:
    url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    if not url:
        raise StoreError("SUPABASE_URL is not set")
    return url


def _service_key() -> str:
    key = os.getenv("SUPABASE_SERVICE_KEY", "").strip()
    if not key:
        # Worth being loud: with no key every table here is unreadable, because
        # none of them grant anything to anon (20260930000100 §4-6).
        raise StoreError("SUPABASE_SERVICE_KEY is not set")
    return key


def _headers(prefer: str | None = None) -> dict[str, str]:
    headers = {
        "apikey": _service_key(),
        "Authorization": f"Bearer {_service_key()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def configured() -> bool:
    """Can this process write at all? Checked once before a run starts."""
    return bool(os.getenv("SUPABASE_URL", "").strip()) and bool(
        os.getenv("SUPABASE_SERVICE_KEY", "").strip()
    )


async def _request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    params: dict | None = None,
    json_body: Any = None,
    prefer: str | None = None,
) -> Any:
    res = await client.request(
        method,
        f"{_base_url()}/rest/v1/{path}",
        params=params,
        json=json_body,
        headers=_headers(prefer),
        timeout=HTTP_TIMEOUT,
    )
    if res.status_code >= 400:
        raise StoreError(f"{method} {path} -> {res.status_code} {res.text[:300]}")
    if res.status_code == 204 or not res.content:
        return None
    try:
        return res.json()
    except ValueError:
        return None


def content_hash(text: str) -> str:
    """Hash of the extracted text. See the module docstring for why not the HTML."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── Seeds and sources ─────────────────────────────────────────────────────────


def load_seeds() -> list[dict]:
    data = json.loads(SEEDS_PATH.read_text(encoding="utf-8"))
    sources = data.get("sources") or []
    if not sources:
        raise StoreError(f"{SEEDS_PATH} contains no sources")
    return sources


async def sync_seeds(client: httpx.AsyncClient) -> int:
    """Upsert seeds.json into kb_sources on `url`.

    `on_conflict=url` needs the plain unique index 20260930000100 adds -- a
    partial index cannot be inferred by PostgREST, which is why that index is not
    partial.

    Columns absent from a seed entry are deliberately left out of the payload
    rather than defaulted here, so the table's own defaults stay the single
    definition of crawl_depth, max_pages and fetch_interval_minutes. An update to
    a row a human has tuned by hand therefore does not reset their change.

    That omission is why the rows are grouped by key signature and sent as one
    request per shape. **PostgREST rejects a bulk insert whose objects do not all
    carry the same keys** -- `PGRST102, "All object keys must match"` -- so a
    single request would fail the moment one seed specifies `max_pages` and
    another does not. Filling the gaps instead would mean writing this module's
    idea of the defaults over the table's, which is the thing being avoided.
    In practice every seed has the same shape and this is one request.
    """
    groups: dict[tuple[str, ...], list[dict]] = {}
    total = 0
    for seed in load_seeds():
        row = {"url": seed["url"], "collection": seed["collection"]}
        for optional in (
            "audience_tags",
            "visibility",
            "crawl_depth",
            "allow_hosts",
            "max_pages",
            "enabled",
            "fetch_interval_minutes",
        ):
            if optional in seed:
                row[optional] = seed[optional]
        groups.setdefault(tuple(sorted(row)), []).append(row)
        total += 1

    for rows in groups.values():
        await _request(
            client,
            "POST",
            "kb_sources",
            params={"on_conflict": "url"},
            json_body=rows,
            prefer="resolution=merge-duplicates,return=minimal",
        )
    return total


SOURCE_COLUMNS = (
    "id,url,collection,audience_tags,visibility,crawl_depth,allow_hosts,"
    "max_pages,enabled,fetch_interval_minutes,last_fetched_at,last_status"
)


async def list_sources(
    client: httpx.AsyncClient, *, urls: list[str] | None = None
) -> list[dict]:
    params: dict[str, Any] = {"select": SOURCE_COLUMNS, "order": "collection,url"}
    if urls:
        quoted = ",".join(f'"{u}"' for u in urls)
        params["url"] = f"in.({quoted})"
    else:
        params["enabled"] = "is.true"
    return await _request(client, "GET", "kb_sources", params=params) or []


async def mark_source_fetched(
    client: httpx.AsyncClient, source_id: str, status: str
) -> None:
    await _request(
        client,
        "PATCH",
        "kb_sources",
        params={"id": f"eq.{source_id}"},
        json_body={"last_fetched_at": _now(), "last_status": status[:200]},
        prefer="return=minimal",
    )


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


# ── Documents and chunks ──────────────────────────────────────────────────────

DOCUMENT_COLUMNS = "id,url,title,content_hash,etag,last_modified,version,fetched_at"


async def get_document(client: httpx.AsyncClient, url: str) -> dict | None:
    rows = await _request(
        client,
        "GET",
        "documents",
        params={"select": DOCUMENT_COLUMNS, "url": f"eq.{url}", "limit": "1"},
    )
    return rows[0] if rows else None


async def upsert_document(client: httpx.AsyncClient, row: dict) -> str:
    """Insert or update one document by `url`, returning its id.

    `return=representation` rather than a second GET: the id is needed
    immediately for replace_document_chunks, and PostgREST will hand it back from
    the same request.
    """
    rows = await _request(
        client,
        "POST",
        "documents",
        params={"on_conflict": "url"},
        json_body=[row],
        prefer="resolution=merge-duplicates,return=representation",
    )
    if not rows or not rows[0].get("id"):
        raise StoreError(f"upsert of {row.get('url')} returned no id")
    return rows[0]["id"]


async def touch_document(client: httpx.AsyncClient, document_id: str, **fields) -> None:
    """Record that a document was re-checked and found unchanged.

    This is the 304 and hash-unchanged path: no parse, no embedding, no chunk
    write. It is most of a weekly run, and it is the reason conditional GET is
    worth implementing by hand.
    """
    payload = {"last_verified_at": _now(), "fetched_at": _now()}
    payload.update({k: v for k, v in fields.items() if v is not None})
    await _request(
        client,
        "PATCH",
        "documents",
        params={"id": f"eq.{document_id}"},
        json_body=payload,
        prefer="return=minimal",
    )


async def replace_chunks(
    client: httpx.AsyncClient, document_id: str, chunks: list[dict]
) -> int:
    """Swap a document's chunks atomically. Returns the number written."""
    written = await _request(
        client,
        "POST",
        "rpc/replace_document_chunks",
        json_body={"p_document_id": document_id, "p_chunks": chunks},
    )
    return int(written or 0)


# ── Run records ───────────────────────────────────────────────────────────────


async def start_run(client: httpx.AsyncClient, source_id: str | None = None) -> str | None:
    """Open a kb_ingest_runs row. Returns None if the row could not be created.

    A failure here must not abort the run: the record is the coverage report, not
    the work. Losing the report is bad; refusing to crawl because the report
    could not be opened is worse.
    """
    try:
        rows = await _request(
            client,
            "POST",
            "kb_ingest_runs",
            json_body=[{"source_id": source_id, "status": "partial"}],
            prefer="return=representation",
        )
        return rows[0]["id"] if rows else None
    except StoreError as exc:
        logger.warning("could not open an ingest run record: %s", exc)
        return None


async def finish_run(client: httpx.AsyncClient, run_id: str | None, stats: dict) -> None:
    if not run_id:
        return
    payload = {
        "status": stats.get("status", "partial"),
        "pages_fetched": stats.get("pages_fetched", 0),
        "pages_unchanged": stats.get("pages_unchanged", 0),
        "pages_skipped": stats.get("pages_skipped", 0),
        "skip_reasons": stats.get("skip_reasons", {}),
        "documents_written": stats.get("documents_written", 0),
        "chunks_written": stats.get("chunks_written", 0),
        "embed_tokens": stats.get("embed_tokens", 0),
        "error_message": (stats.get("error_message") or None),
        "completed_at": _now(),
    }
    try:
        await _request(
            client,
            "PATCH",
            "kb_ingest_runs",
            params={"id": f"eq.{run_id}"},
            json_body=payload,
            prefer="return=minimal",
        )
    except StoreError as exc:
        logger.warning("could not close ingest run %s: %s", run_id, exc)


# ── Job queue ─────────────────────────────────────────────────────────────────
#
# `for update skip locked` is the claim protocol the plan calls for, and it
# cannot be expressed over PostgREST -- there is no SQL text channel here. What
# this does instead is a conditional PATCH: filter on the row still being
# claimable, and let PostgREST report how many rows it actually changed. Two
# workers racing both issue the PATCH, but only one matches the filter, because
# the other has already moved `status` and `locked_until` out from under it.
#
# That is weaker than `skip locked` in one specific way: it is optimistic rather
# than blocking, so a loser gets zero rows back and has to ask for the next job.
# For a weekly crawl with one worker that is a non-issue, and it needs no
# database function. If a second worker ever runs, revisit this.


async def enqueue_jobs(client: httpx.AsyncClient, source_ids: list[str]) -> int:
    if not source_ids:
        return 0
    await _request(
        client,
        "POST",
        "kb_ingest_jobs",
        json_body=[{"source_id": sid, "status": "pending"} for sid in source_ids],
        prefer="return=minimal",
    )
    return len(source_ids)


async def claim_job(
    client: httpx.AsyncClient, worker: str, *, lease_minutes: int = 120
) -> dict | None:
    """Claim the oldest claimable job, or return None.

    A `running` job whose `locked_until` has passed is claimable again -- that is
    what makes a crash recoverable. `catalog.sjsu.edu` at crawl-delay 120 is a
    ~10-hour job, so the lease has to be generous or a long crawl steals its own
    work back mid-run.
    """
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    candidates = await _request(
        client,
        "GET",
        "kb_ingest_jobs",
        params={
            "select": "id,source_id,status,attempts,locked_until",
            "or": f"(status.eq.pending,and(status.eq.running,locked_until.lt.{now.isoformat()}))",
            "order": "created_at",
            "limit": "5",
        },
    ) or []

    lease = (now + timedelta(minutes=lease_minutes)).isoformat()
    for candidate in candidates:
        # The filter repeats the claimable condition, so a row another worker
        # took between the GET and here simply does not match.
        params = {
            "id": f"eq.{candidate['id']}",
            "or": f"(status.eq.pending,and(status.eq.running,locked_until.lt.{now.isoformat()}))",
        }
        rows = await _request(
            client,
            "PATCH",
            "kb_ingest_jobs",
            params=params,
            json_body={
                "status": "running",
                "locked_by": worker[:200],
                "locked_until": lease,
                "attempts": int(candidate.get("attempts") or 0) + 1,
            },
            prefer="return=representation",
        )
        if rows:
            return rows[0]
    return None


async def finish_job(
    client: httpx.AsyncClient, job_id: str, *, status: str, error: str | None = None
) -> None:
    await _request(
        client,
        "PATCH",
        "kb_ingest_jobs",
        params={"id": f"eq.{job_id}"},
        json_body={
            "status": status,
            "error": (error or None) and error[:2000],
            "locked_by": None,
            "locked_until": None,
        },
        prefer="return=minimal",
    )
