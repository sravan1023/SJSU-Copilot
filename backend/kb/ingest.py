"""Ingestion: turn the curated seed list into retrievable chunks.

    python -m kb.ingest --sync-seeds
    python -m kb.ingest --dry-run
    python -m kb.ingest --source https://www.sjsu.edu/registrar/
    python -m kb.ingest --force --max-pages 400

Nothing here runs inside a request, which is why `kb/` sits outside `services/`.

**Politeness is the design, not a limitation.** One global semaphore of 2, a
per-host lock, and a sleep of `max(robots crawl_delay, KB_CRAWL_DELAY)` inside
that lock. Ingestion is offline, so slowness is free and being blocked is not.
`www.sjsu.edu/robots.txt` also asks in plain English -- *"Please do not over load
the servers"*, with an ITS contact address -- so the ~1 req/s default is
honouring a stated request rather than merely being well behaved.

**`catalog.sjsu.edu` sets `crawl-delay: 120`**, which is ~30 pages an hour. It
must be run as its own job, overnight, and never inside a run that also touches
the other collections -- one slow host would otherwise stall everything behind
it. That is what `kb_ingest_jobs` and the resumable `--source` form are for.

**What is deliberately not here: a frontier.** `crawl_depth` ships at 0 and the
seed list is hand-written, because SJSU's advertised sitemap index is abandoned
(`lastmod` dates of 2013-2016, and every one of the six target collections'
sitemaps 404s). With no sitemap, discovery is either BFS or a human, and for
~30 pages a human picks better pages. The column and `extract_links` both exist
for the day that changes.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import socket
import time
from collections import Counter
from dataclasses import dataclass, field

import httpx
from dotenv import load_dotenv

from kb import embed, store
from kb.chunker import chunk_html, chunk_text
from kb.fetcher import USER_AGENT, Outcome, conditional_get
from kb.robots import RobotsCache
from services.token_budget import count_tokens

logger = logging.getLogger(__name__)

# Two at a time across the whole run, and never two to the same host at once.
MAX_CONCURRENCY = 2

# Embedding runs *after* the fetch gate is released, so without its own limit
# every in-flight page can call Google at the same moment. The first real
# ingestion did exactly that and lost 73 of 175 vectors to 429s. One at a time
# costs almost nothing -- a 50-chunk batch returns in under a second -- and makes
# the free tier's per-minute quota a non-issue.
MAX_EMBED_CONCURRENCY = 1
DEFAULT_CRAWL_DELAY = 1.0
RUN_PAGE_CAP = 400


def crawl_delay() -> float:
    try:
        return float(os.getenv("KB_CRAWL_DELAY", "").strip() or DEFAULT_CRAWL_DELAY)
    except ValueError:
        return DEFAULT_CRAWL_DELAY


@dataclass
class Stats:
    pages_fetched: int = 0
    pages_unchanged: int = 0
    pages_skipped: int = 0
    documents_written: int = 0
    chunks_written: int = 0
    embed_tokens: int = 0
    embeddings_missing: int = 0
    skip_reasons: Counter = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)

    def as_record(self) -> dict:
        # `partial` whenever anything was skipped or errored: a run that fetched
        # 20 of 29 pages is not a success, and calling it one is how a corpus
        # quietly rots. `kb_ingest_runs` is the coverage report.
        if self.errors and not self.pages_fetched and not self.pages_unchanged:
            status = "failed"
        elif self.errors or self.pages_skipped:
            status = "partial"
        else:
            status = "success"
        return {
            "status": status,
            "pages_fetched": self.pages_fetched,
            "pages_unchanged": self.pages_unchanged,
            "pages_skipped": self.pages_skipped,
            "skip_reasons": dict(self.skip_reasons),
            "documents_written": self.documents_written,
            "chunks_written": self.chunks_written,
            "embed_tokens": self.embed_tokens,
            "error_message": "; ".join(self.errors[:5])[:2000] or None,
        }


class HostPacer:
    """A lock plus a last-request timestamp per host."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}

    def lock(self, host: str) -> asyncio.Lock:
        return self._locks.setdefault(host, asyncio.Lock())

    async def wait(self, host: str, delay: float) -> None:
        elapsed = time.monotonic() - self._last.get(host, 0.0)
        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)
        self._last[host] = time.monotonic()


async def _ingest_url(
    client: httpx.AsyncClient,
    url: str,
    source: dict,
    *,
    robots: RobotsCache,
    pacer: HostPacer,
    gate: asyncio.Semaphore,
    embed_gate: asyncio.Semaphore,
    stats: Stats,
    force: bool,
    dry_run: bool,
) -> str:
    """Ingest one URL. Returns the outcome name, for kb_sources.last_status."""
    from urllib.parse import urlsplit

    host = urlsplit(url).netloc.lower()
    rules = await robots.for_url(url)
    delay = max(rules.crawl_delay or 0.0, crawl_delay())

    existing = None if dry_run else await store.get_document(client, url)
    # `--force` re-fetches unconditionally by withholding the validators, which
    # is the only way to recover from a bad extraction: the server would
    # otherwise keep answering 304 and the stored text would never be revisited.
    etag = None if force else (existing or {}).get("etag")
    last_modified = None if force else (existing or {}).get("last_modified")

    async with gate:
        async with pacer.lock(host):
            await pacer.wait(host, delay)
            result = await conditional_get(
                client,
                url,
                rules=rules,
                etag=etag,
                last_modified=last_modified,
                allowed_hosts=[host, *(source.get("allow_hosts") or [])],
            )

    if result.outcome is Outcome.NOT_MODIFIED:
        stats.pages_unchanged += 1
        if not dry_run and existing:
            await store.touch_document(client, existing["id"])
        return result.outcome.value

    if not result.ok:
        stats.pages_skipped += 1
        stats.skip_reasons[result.outcome.value] += 1
        logger.info("skipped %s: %s %s", url, result.outcome.value, result.detail or "")
        return result.outcome.value

    text = (result.text or "").strip()
    chunks = chunk_html(result.html) if result.html else chunk_text(text)
    if not chunks:
        # Fetched fine, extracted nothing chunkable. Counting it as THIN rather
        # than silently returning keeps it visible in the coverage report.
        stats.pages_skipped += 1
        stats.skip_reasons[Outcome.THIN.value] += 1
        return Outcome.THIN.value

    stats.pages_fetched += 1
    digest = store.content_hash(text)

    if dry_run:
        stats.chunks_written += len(chunks)
        sizes = sorted(c.char_count for c in chunks)
        headed = sum(1 for c in chunks if c.heading)
        print(
            f"  {url}\n"
            f"    {len(chunks)} chunks, {sizes[0]}-{sizes[-1]} chars "
            f"(median {sizes[len(sizes) // 2]}), {headed} with a heading"
        )
        return Outcome.OK.value

    if existing and existing.get("content_hash") == digest and not force:
        # 200 but byte-identical text. Refresh the validators so the next run can
        # get a cheap 304, and skip the embedding spend entirely.
        stats.pages_unchanged += 1
        stats.pages_fetched -= 1
        await store.touch_document(
            client,
            existing["id"],
            etag=result.etag,
            last_modified=result.last_modified,
        )
        return "unchanged"

    async with embed_gate:
        vectors = await embed.embed_documents(client, [c.content for c in chunks])
    stats.embeddings_missing += sum(1 for v in vectors if v is None)

    title = _title_for(result, url)
    document_id = await store.upsert_document(
        client,
        {
            "url": url,
            "title": title,
            "source": host,
            "collection": source.get("collection"),
            "audience_tags": source.get("audience_tags") or [],
            "visibility": source.get("visibility") or "public",
            "content_hash": digest,
            "etag": result.etag,
            "last_modified": result.last_modified,
            "fetched_at": store._now(),
            "last_verified_at": store._now(),
            "version": int((existing or {}).get("version") or 0) + 1,
        },
    )

    payload = []
    for chunk, vector in zip(chunks, vectors):
        tokens = count_tokens(chunk.content)
        stats.embed_tokens += tokens
        payload.append(
            {
                "chunk_index": chunk.chunk_index,
                "heading": chunk.heading,
                "content": chunk.content,
                "token_count": tokens,
                "embedding": embed.to_pgvector(vector),
                "metadata": {"url": url, "collection": source.get("collection")},
            }
        )

    written = await store.replace_chunks(client, document_id, payload)
    stats.documents_written += 1
    stats.chunks_written += written
    return Outcome.OK.value


def _title_for(result, url: str) -> str:
    """A document title, from the markup if there is one.

    `documents.title` is `not null`, and it is what the citation renders, so a
    URL is a poor but acceptable last resort -- an empty string is not an option.
    """
    html = result.html or ""
    if html:
        import re

        match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        if match:
            title = re.sub(r"\s+", " ", match.group(1)).strip()
            # SJSU suffixes nearly every page with the university name.
            for suffix in (" | San José State University", " | SJSU"):
                if title.endswith(suffix):
                    title = title[: -len(suffix)].strip()
            if title:
                return title[:300]
    return url


async def run_ingestion(
    source_urls: list[str] | None = None,
    *,
    force: bool = False,
    max_pages: int = RUN_PAGE_CAP,
    dry_run: bool = False,
) -> dict:
    """Crawl, chunk, embed and store. Returns the kb_ingest_runs record."""
    stats = Stats()
    if not dry_run and not store.configured():
        return {**Stats(errors=["SUPABASE_URL or SUPABASE_SERVICE_KEY is not set"]).as_record()}

    if not embed.available():
        # Not fatal: the keyword arm of the fusion works without vectors, so an
        # ingestion with no GOOGLE_API_KEY still produces a searchable corpus.
        # Said loudly because the degradation is otherwise invisible.
        logger.warning("GOOGLE_API_KEY is not set -- storing chunks with no embeddings")

    async with httpx.AsyncClient(
        follow_redirects=False, headers={"User-Agent": USER_AGENT}
    ) as client:
        if dry_run:
            # A dry run reads seeds.json directly and never touches the database,
            # so chunking can be inspected before the migration is on live and
            # without a service key in the environment.
            seeds = store.load_seeds()
            if source_urls:
                wanted = set(source_urls)
                sources = [s for s in seeds if s["url"] in wanted] or [
                    {"url": u, "collection": None} for u in source_urls
                ]
            else:
                sources = seeds
        else:
            sources = await store.list_sources(client, urls=source_urls)
        if not sources:
            stats.errors.append("no enabled sources matched")
            return stats.as_record()

        run_id = None if dry_run else await store.start_run(client)
        robots = RobotsCache(client=client)
        pacer = HostPacer()
        gate = asyncio.Semaphore(MAX_CONCURRENCY)
        embed_gate = asyncio.Semaphore(MAX_EMBED_CONCURRENCY)

        budget = min(max_pages, RUN_PAGE_CAP)
        tasks = []
        for source in sources[:budget]:
            tasks.append(
                _ingest_url(
                    client,
                    source["url"],
                    source,
                    robots=robots,
                    pacer=pacer,
                    gate=gate,
                    embed_gate=embed_gate,
                    stats=stats,
                    force=force,
                    dry_run=dry_run,
                )
            )

        for source, outcome in zip(sources, await asyncio.gather(*tasks, return_exceptions=True)):
            if isinstance(outcome, BaseException):
                # One bad page must not end the run; the corpus is worth more
                # partially built than not at all.
                stats.pages_skipped += 1
                stats.skip_reasons["exception"] += 1
                stats.errors.append(f"{source['url']}: {type(outcome).__name__}: {outcome}")
                logger.warning("ingest failed for %s", source["url"], exc_info=outcome)
            elif not dry_run and source.get("id"):
                # The real outcome, not an unconditional "ok". A source whose page
                # was robots-denied or 404 would otherwise read as healthy in
                # kb_sources, which is the table someone checks to find out why a
                # question has no answer.
                await store.mark_source_fetched(client, source["id"], outcome or "unknown")

        record = stats.as_record()
        if not dry_run:
            await store.finish_run(client, run_id, record)
        return record


def _summarise(record: dict, stats_note: str = "") -> None:
    print("\n-- ingestion --")
    for key in (
        "status",
        "pages_fetched",
        "pages_unchanged",
        "pages_skipped",
        "documents_written",
        "chunks_written",
        "embed_tokens",
    ):
        print(f"  {key:<18} {record.get(key)}")
    if record.get("skip_reasons"):
        print(f"  skip_reasons       {record['skip_reasons']}")
    if record.get("error_message"):
        print(f"  error              {record['error_message']}")
    if stats_note:
        print(stats_note)


async def _main() -> int:
    parser = argparse.ArgumentParser(description="Build the SJSU knowledge base.")
    parser.add_argument("--sync-seeds", action="store_true", help="upsert kb/seeds.json into kb_sources")
    parser.add_argument("--dry-run", action="store_true", help="fetch and chunk, write nothing")
    parser.add_argument("--source", action="append", dest="sources", help="limit to this URL (repeatable)")
    parser.add_argument("--force", action="store_true", help="ignore etag/hash and re-embed")
    parser.add_argument("--max-pages", type=int, default=RUN_PAGE_CAP)
    parser.add_argument("--enqueue", action="store_true", help="queue jobs instead of crawling")
    parser.add_argument("--worker", action="store_true", help="claim and run one queued job")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    # main.py does this for the server; a CLI entry point has to do it itself, or
    # GOOGLE_API_KEY and the Supabase credentials in backend/.env are invisible
    # and the run silently stores chunks with no embeddings.
    load_dotenv()

    if args.sync_seeds:
        async with httpx.AsyncClient() as client:
            count = await store.sync_seeds(client)
        print(f"synced {count} seeds into kb_sources")
        if not (args.dry_run or args.sources or args.enqueue or args.worker):
            return 0

    if args.enqueue:
        async with httpx.AsyncClient() as client:
            sources = await store.list_sources(client, urls=args.sources)
            queued = await store.enqueue_jobs(client, [s["id"] for s in sources])
        print(f"queued {queued} jobs")
        return 0

    if args.worker:
        worker = f"{socket.gethostname()}:{os.getpid()}"
        async with httpx.AsyncClient() as client:
            job = await store.claim_job(client, worker)
            if not job:
                print("no claimable jobs")
                return 0
            sources = await store.list_sources(client)
            url = next((s["url"] for s in sources if s["id"] == job["source_id"]), None)
        if not url:
            print("claimed a job whose source is gone")
            return 1
        record = await run_ingestion([url], force=args.force, max_pages=args.max_pages)
        async with httpx.AsyncClient() as client:
            await store.finish_job(
                client,
                job["id"],
                status="done" if record["status"] != "failed" else "failed",
                error=record.get("error_message"),
            )
        _summarise(record)
        return 0 if record["status"] != "failed" else 1

    record = await run_ingestion(
        args.sources, force=args.force, max_pages=args.max_pages, dry_run=args.dry_run
    )
    _summarise(record)
    return 0 if record["status"] != "failed" else 1


def _cli() -> int:
    try:
        return asyncio.run(_main())
    except store.StoreError as exc:
        # Same treatment as kb.eval_retrieval: the usual cause is the migration
        # not being on the project, and a stack trace does not say so.
        print(f"\n{exc}\n")
        if any(m in str(exc) for m in ("42703", "PGRST202", "PGRST205", "404", "does not exist")):
            print(
                "This almost certainly means supabase/migrations/"
                "20260930000100_kb_hybrid_retrieval.sql is not on the project yet.\n"
                "Apply it with:\n"
                "    npx supabase --agent no db push\n"
            )
        return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
