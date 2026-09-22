"""Application-lifetime resources: HTTP clients and a bounded thread pool.

Every outbound call used to build its own `httpx.AsyncClient` and tear it down
again -- four sites in services/llm.py plus the crawler -- so each one paid a
fresh TCP and TLS handshake to the same host. These are created once at startup
and closed at shutdown instead.

Two separate clients, deliberately:

* the provider client carries an Authorization header and follows redirects;
* the crawler client carries neither, and **must** keep follow_redirects=False,
  because services/web_search.py walks redirects by hand so it can re-validate
  the host at every hop. Sharing one client between them would either leak the
  provider credential to arbitrary crawled hosts or disable that SSRF guard.

Per-request timeouts still vary (a streaming completion is not a title call), so
callers pass `timeout=` on the individual request rather than getting a client
per timeout.

Blocking work runs on two bounded thread pools, so it cannot grow without limit
(cancelling an await on a thread does not stop the thread, so without a ceiling
a run of slow searches would keep spawning them):

* the blocking pool (BLOCKING_POOL_SIZE) for CPU work -- HTML parsing -- and,
  as the loop's default executor, DNS lookups;
* the search pool (SEARCH_POOL_SIZE) for the synchronous DDGS searches, which
  spend their time waiting on the network.

They used to share one pool of 8. At 25 concurrent chats that meant 50 searches
queued ahead of every page parse and DNS lookup, every turn hit the retrieval
deadline, and answers were built from search snippets instead of pages.
"""

import contextvars
import functools
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

import httpx

logger = logging.getLogger(__name__)

# Matches the crawler's per-page concurrency plus headroom for DNS and parsing.
BLOCKING_POOL_SIZE = int(os.getenv("BLOCKING_POOL_SIZE", "8"))
# Two searches per chat turn, mostly waiting on the network.
SEARCH_POOL_SIZE = int(os.getenv("SEARCH_POOL_SIZE", "32"))

CRAWL_TIMEOUT = float(os.getenv("CRAWL_TIMEOUT", "10.0"))

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
)

_provider_client: httpx.AsyncClient | None = None
_crawl_client: httpx.AsyncClient | None = None
_executor: ThreadPoolExecutor | None = None
_search_executor: ThreadPoolExecutor | None = None


def get_provider_client() -> httpx.AsyncClient | None:
    """Shared client for model-provider calls, or None outside an app lifespan."""
    return _provider_client


def get_crawl_client() -> httpx.AsyncClient | None:
    """Shared client for page fetching, or None outside an app lifespan."""
    return _crawl_client


def get_executor() -> ThreadPoolExecutor | None:
    return _executor


def get_search_executor() -> ThreadPoolExecutor | None:
    return _search_executor


async def _run_on(executor: ThreadPoolExecutor | None, fn, *args):
    import asyncio

    loop = asyncio.get_running_loop()
    if executor is not None:
        # run_in_executor, unlike to_thread, does not carry context variables
        # into the worker thread. Copy them so the request id and timing trace
        # (observability.py) are visible there too.
        ctx = contextvars.copy_context()
        return await loop.run_in_executor(executor, functools.partial(ctx.run, fn, *args))
    return await asyncio.to_thread(fn, *args)


async def run_blocking(fn, *args):
    """Run CPU-bound work (HTML parsing) on the blocking pool.

    Falls back to asyncio.to_thread when no lifespan is active, so services stay
    usable from scripts and tests.
    """
    return await _run_on(_executor, fn, *args)


async def run_search(fn, *args):
    """Run a blocking web search on the search pool, away from parsing and DNS."""
    return await _run_on(_search_executor, fn, *args)


@asynccontextmanager
async def lifespan(app):
    global _provider_client, _crawl_client, _executor, _search_executor

    import asyncio

    _provider_client = httpx.AsyncClient(
        # No default timeout: callers set one per request, since a streaming
        # completion and a title call have very different expectations.
        timeout=httpx.Timeout(connect=5.0, read=120.0, write=10.0, pool=5.0),
        limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
    )

    _crawl_client = httpx.AsyncClient(
        timeout=httpx.Timeout(CRAWL_TIMEOUT),
        follow_redirects=False,  # load-bearing: see module docstring
        headers={"User-Agent": USER_AGENT},
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    )

    _executor = ThreadPoolExecutor(
        max_workers=BLOCKING_POOL_SIZE,
        thread_name_prefix="blocking",
    )
    # Also bound anything still calling asyncio.to_thread directly (DNS lookups
    # in web_search._host_is_public).
    asyncio.get_running_loop().set_default_executor(_executor)

    _search_executor = ThreadPoolExecutor(
        max_workers=SEARCH_POOL_SIZE,
        thread_name_prefix="search",
    )

    logger.info(
        "runtime started",
        extra={"blocking_pool_size": BLOCKING_POOL_SIZE, "search_pool_size": SEARCH_POOL_SIZE},
    )

    try:
        yield
    finally:
        await _provider_client.aclose()
        await _crawl_client.aclose()
        # Don't block shutdown on a slow search thread that cannot be cancelled.
        _executor.shutdown(wait=False, cancel_futures=True)
        _search_executor.shutdown(wait=False, cancel_futures=True)
        _provider_client = None
        _crawl_client = None
        _executor = None
        _search_executor = None
        logger.info("runtime stopped")
