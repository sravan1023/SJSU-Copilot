"""
Tests for the retrieval deadline, partial results, the bounded thread pool, and
the shared lifespan HTTP clients.

Run from backend/ with:
    python -m tests.test_retrieval_budget

No network: _safe_stream_get and the DDGS search are both stubbed.
"""
import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import runtime  # noqa: E402
from services import web_search  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []


def _check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        FAILURES.append((label, detail))
        print(f"  FAIL {label}  {detail}")


HTML = "<html><body><main>" + ("campus parking information. " * 50) + "</main></body></html>"


# ── 1. Crawl budget ───────────────────────────────────────────────────────────


def test_crawl_returns_partial_results():
    print("\n[1.1] a slow page is abandoned, a fast one is kept")

    async def fake_get(client, url):
        if "slow" in url:
            await asyncio.sleep(5)
        return url, HTML

    async def drive():
        with patch.object(web_search, "_safe_stream_get", fake_get):
            start = time.monotonic()
            pages = await web_search.crawl_sources(
                ["https://sjsu.edu/fast", "https://sjsu.edu/slow"], budget=0.4
            )
            return pages, time.monotonic() - start

    pages, elapsed = asyncio.run(drive())
    by_url = {p["url"]: p["content"] for p in pages}

    _check("both urls accounted for", len(pages) == 2, str(by_url.keys()))
    _check("fast page has content", len(by_url["https://sjsu.edu/fast"]) > 0)
    _check("slow page returns empty, not an error", by_url["https://sjsu.edu/slow"] == "")
    _check(
        "returned at the budget, not the slow page's timeout",
        elapsed < 2.0,
        f"took {elapsed:.2f}s",
    )


def test_zero_budget_yields_no_content():
    print("\n[1.2] an exhausted budget skips crawling entirely")

    async def fake_get(client, url):
        return url, HTML

    async def drive():
        with patch.object(web_search, "_safe_stream_get", fake_get):
            return await web_search.crawl_sources(["https://sjsu.edu/a"], budget=0.0)

    pages = asyncio.run(drive())
    _check("url still accounted for", len(pages) == 1)
    _check("content empty", pages[0]["content"] == "")


def test_fetch_failure_does_not_fail_the_batch():
    print("\n[1.3] one page raising does not lose the others")

    async def fake_get(client, url):
        if "bad" in url:
            raise RuntimeError("connection reset")
        return url, HTML

    async def drive():
        with patch.object(web_search, "_safe_stream_get", fake_get):
            return await web_search.crawl_sources(
                ["https://sjsu.edu/bad", "https://sjsu.edu/good"], budget=5
            )

    pages = asyncio.run(drive())
    by_url = {p["url"]: p["content"] for p in pages}
    _check("good page survived", len(by_url["https://sjsu.edu/good"]) > 0)
    _check("bad page is empty", by_url["https://sjsu.edu/bad"] == "")


# ── 2. Search budget ──────────────────────────────────────────────────────────


def test_search_keeps_whichever_finished():
    print("\n[2.1] a slow search is abandoned and the fast one is still used")

    def fake_search(query):
        if "site:sjsu.edu" in query:
            return [{"title": "SJSU", "url": "https://sjsu.edu/a", "snippet": "s"}]
        time.sleep(5)
        return [{"title": "Other", "url": "https://example.com/b", "snippet": "s"}]

    async def drive():
        with patch.object(web_search, "search_web", fake_search):
            start = time.monotonic()
            results = await web_search._search_within("parking", 0.5)
            return results, time.monotonic() - start

    results, elapsed = asyncio.run(drive())
    _check("sjsu result kept", any("sjsu.edu" in r["url"] for r in results), str(results))
    _check("slow result excluded", not any("example.com" in r["url"] for r in results))
    _check("returned at the budget", elapsed < 2.0, f"took {elapsed:.2f}s")


def test_build_rag_prompt_falls_back_to_snippets():
    print("\n[2.2] uncrawled sources still contribute their search snippet")

    def fake_search(query):
        return [
            {
                "title": "Parking at SJSU",
                "url": "https://sjsu.edu/parking",
                "snippet": "Visitor parking is available in the North Garage.",
            }
        ]

    async def slow_get(client, url):
        await asyncio.sleep(5)
        return url, HTML

    async def no_rewrite(question):
        return question

    async def drive():
        with patch.object(web_search, "search_web", fake_search), \
             patch.object(web_search, "_safe_stream_get", slow_get), \
             patch.object(web_search, "rewrite_query_for_sjsu", no_rewrite), \
             patch.object(web_search, "RETRIEVAL_DEADLINE", 0.5):
            start = time.monotonic()
            prompt, sources = await web_search.build_rag_prompt(
                [{"role": "user", "content": "where can visitors park at SJSU"}]
            )
            return prompt, sources, time.monotonic() - start

    prompt, sources, elapsed = asyncio.run(drive())

    _check("a prompt was still produced", prompt is not None)
    _check("snippet text used as context", prompt and "North Garage" in prompt, str(prompt)[:120])
    _check("source is still cited", len(sources) == 1, str(sources))
    _check("respected the deadline", elapsed < 2.0, f"took {elapsed:.2f}s")


# ── 3. Event loop is not blocked by HTML parsing ──────────────────────────────


def test_html_parsing_does_not_block_the_loop():
    print("\n[3.1] HTML extraction runs off the event loop")

    def slow_extract(html):
        time.sleep(0.5)  # stands in for BeautifulSoup over a large page
        return "extracted"

    async def fake_get(client, url):
        return url, HTML

    async def drive():
        ticks = 0

        async def heartbeat():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.02)
                ticks += 1

        with patch.object(web_search, "_safe_stream_get", fake_get), \
             patch.object(web_search, "_extract_main_text", slow_extract):
            beat = asyncio.ensure_future(heartbeat())
            pages = await web_search.crawl_sources(["https://sjsu.edu/a"], budget=5)
            beat.cancel()
        return pages, ticks

    pages, ticks = asyncio.run(drive())

    _check("page was extracted", pages[0]["content"] == "extracted")
    # A blocking call on the loop would starve the heartbeat entirely.
    _check(
        "event loop stayed responsive during extraction",
        ticks >= 10,
        f"only {ticks} heartbeats in ~0.5s of parsing",
    )


# ── 4. Shared runtime resources ───────────────────────────────────────────────


def test_lifespan_creates_and_closes_clients():
    print("\n[4.1] lifespan wires up and tears down shared resources")

    async def drive():
        _check("no provider client before startup", runtime.get_provider_client() is None)
        _check("no crawl client before startup", runtime.get_crawl_client() is None)

        async with runtime.lifespan(None):
            provider = runtime.get_provider_client()
            crawl = runtime.get_crawl_client()
            _check("provider client created", provider is not None)
            _check("crawl client created", crawl is not None)
            _check("clients are distinct", provider is not crawl)
            _check(
                "crawler does not follow redirects",
                crawl.follow_redirects is False,
                "manual redirect walking re-validates each hop",
            )
            _check(
                "crawler carries no provider credential",
                "authorization" not in {k.lower() for k in crawl.headers},
                str(list(crawl.headers)),
            )
            _check("executor created", runtime.get_executor() is not None)
            _check(
                "executor is bounded",
                runtime.get_executor()._max_workers == runtime.BLOCKING_POOL_SIZE,
            )
            _check("search pool created", runtime.get_search_executor() is not None)
            _check(
                "search pool is separate from the blocking pool",
                runtime.get_search_executor() is not runtime.get_executor(),
            )
            _check(
                "search pool is bounded",
                runtime.get_search_executor()._max_workers == runtime.SEARCH_POOL_SIZE,
            )

        _check("provider client cleared after shutdown", runtime.get_provider_client() is None)
        _check("crawl client cleared after shutdown", runtime.get_crawl_client() is None)
        _check("executor cleared after shutdown", runtime.get_executor() is None)
        _check("search pool cleared after shutdown", runtime.get_search_executor() is None)

    asyncio.run(drive())


def test_provider_client_is_reused_across_calls():
    print("\n[4.2] provider calls share one client under a lifespan")

    async def drive():
        async with runtime.lifespan(None):
            from services import llm

            async with llm._provider_client() as first:
                async with llm._provider_client() as second:
                    _check("same client instance reused", first is second)
                    _check("it is the shared one", first is runtime.get_provider_client())
            # Leaving the context must not close the shared client.
            _check("shared client still open after use", not runtime.get_provider_client().is_closed)

    asyncio.run(drive())


def run():
    test_crawl_returns_partial_results()
    test_zero_budget_yields_no_content()
    test_fetch_failure_does_not_fail_the_batch()
    test_search_keeps_whichever_finished()
    test_build_rag_prompt_falls_back_to_snippets()
    test_html_parsing_does_not_block_the_loop()
    test_lifespan_creates_and_closes_clients()
    test_provider_client_is_reused_across_calls()

    print("\n" + "=" * 60)
    print(f"  Passed: {PASS}")
    print(f"  Failed: {FAIL}")
    print("=" * 60)
    for label, detail in FAILURES:
        print(f"  - {label}: {detail}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(run())
