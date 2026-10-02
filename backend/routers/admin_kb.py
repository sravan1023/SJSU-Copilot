"""Triggering knowledge-base ingestion over HTTP.

Shaped after `routers/jobs.py`, for the same reason: this drives a write pipeline
with the Supabase service role, so it is gated on a grant in
`public.admin_grants` rather than on merely being signed in. A guest is refused
with 403 -- they are authenticated but can never hold a grant, because grants are
seeded by hand against an account. If `admin_grants` is unreadable the dependency
fails closed with 503.

The `run_ingestion` capability needs no migration: `admin_grants.capability` is a
pattern check (`^[a-z][a-z0-9_]{1,63}$`, 20260917000200:34-35), not an enum. Seed
the grant by hand the way `run_jobs` was.

**Deliberately not copied: `intern-jobs-alert/index.ts`'s auth**, which fails
open.

**A crawl is far longer than an HTTP request.** The full seed list at ~1 req/s is
a minute or two, which is survivable inline, but `catalog.sjsu.edu` at
`crawl-delay: 120` is a ~10-hour job and can never be. So the default here is to
enqueue and return, and inline execution has to be asked for explicitly with
`wait=true`. A caller who asks to wait gets whatever their own client's timeout
allows; that is their choice to make, not this endpoint's.
"""
import socket
import os

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth import require_capability
from kb import ingest, store

router = APIRouter(tags=["knowledge-base"])

RUN_INGESTION = Depends(require_capability("run_ingestion"))


class IngestRequest(BaseModel):
    sources: list[str] | None = Field(
        default=None, description="Limit to these seed URLs. Omit for every enabled source."
    )
    force: bool = Field(default=False, description="Ignore etag and content hash; re-embed.")
    max_pages: int = Field(default=ingest.RUN_PAGE_CAP, ge=1, le=ingest.RUN_PAGE_CAP)
    wait: bool = Field(
        default=False,
        description="Run inline and return the coverage report. Only safe for small runs.",
    )


@router.post("/admin/kb/ingest", dependencies=[RUN_INGESTION])
async def trigger_ingest(req: IngestRequest = IngestRequest()):
    if not store.configured():
        # 503 rather than 500: the service is correctly built and incorrectly
        # configured, and the distinction is what tells an operator where to look.
        raise HTTPException(
            status_code=503,
            detail="SUPABASE_URL or SUPABASE_SERVICE_KEY is not configured",
        )

    if req.wait:
        record = await ingest.run_ingestion(
            req.sources, force=req.force, max_pages=req.max_pages
        )
        return {"ok": record["status"] != "failed", "mode": "inline", "run": record}

    try:
        async with httpx.AsyncClient() as client:
            sources = await store.list_sources(client, urls=req.sources)
            queued = await store.enqueue_jobs(client, [s["id"] for s in sources])
    except store.StoreError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return {
        "ok": True,
        "mode": "queued",
        "queued": queued,
        # Named so the reply is actionable: nothing in this process drains the
        # queue, and saying so is better than letting a caller assume it does.
        "drain_with": "python -m kb.ingest --worker",
    }


@router.get("/admin/kb/status", dependencies=[RUN_INGESTION])
async def kb_status():
    """The coverage report: what is in the corpus and when it was last refreshed."""
    if not store.configured():
        raise HTTPException(status_code=503, detail="Supabase is not configured")
    try:
        async with httpx.AsyncClient() as client:
            sources = await store.list_sources(client)
            runs = await store._request(
                client,
                "GET",
                "kb_ingest_runs",
                params={
                    "select": "id,status,pages_fetched,pages_unchanged,pages_skipped,"
                    "skip_reasons,documents_written,chunks_written,started_at,completed_at",
                    "order": "started_at.desc",
                    "limit": "5",
                },
            )
            documents = await store._request(
                client, "GET", "documents", params={"select": "id", "limit": "1"},
                prefer="count=exact",
            )
    except store.StoreError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return {
        "sources_enabled": len(sources),
        "documents_sampled": len(documents or []),
        "worker": f"{socket.gethostname()}:{os.getpid()}",
        "recent_runs": runs or [],
    }
