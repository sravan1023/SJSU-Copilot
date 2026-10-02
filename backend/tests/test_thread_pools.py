"""
A burst of web searches must not starve HTML parsing or DNS lookups.

Found in the mock benchmark: searches (two per chat turn, blocking) shared one
pool of 8 threads with parsing and DNS. At 25 concurrent turns every turn hit
the retrieval deadline and answers fell back to search snippets.

Run from backend/ with:
    python -m pytest tests/test_thread_pools.py
"""
import asyncio
import threading
import time
from unittest.mock import patch

import runtime
from services import web_search


def test_saturated_search_pool_does_not_delay_parsing_or_dns():
    release = threading.Event()

    def stuck_search(query):
        release.wait(10)  # a slow search engine
        return []

    async def go():
        async with runtime.lifespan(None):
            # Occupy every search thread, plus a queue behind them.
            searches = [
                asyncio.ensure_future(runtime.run_search(stuck_search, f"q{i}"))
                for i in range(runtime.SEARCH_POOL_SIZE + 10)
            ]
            await asyncio.sleep(0.2)

            started = time.perf_counter()
            text = await asyncio.wait_for(
                runtime.run_blocking(web_search._extract_main_text, "<main><p>Office hours</p></main>"),
                timeout=2,
            )
            parse_s = time.perf_counter() - started

            started = time.perf_counter()
            # DNS goes through the loop's default executor (web_search._host_is_public).
            await asyncio.wait_for(asyncio.to_thread(lambda: "dns"), timeout=2)
            dns_s = time.perf_counter() - started

            release.set()
            await asyncio.gather(*searches)
            return text, parse_s, dns_s

    text, parse_s, dns_s = asyncio.run(go())
    assert text == "Office hours"
    assert parse_s < 1, f"parsing waited {parse_s:.2f}s behind searches"
    assert dns_s < 1, f"DNS waited {dns_s:.2f}s behind searches"


def test_retrieval_searches_run_on_the_search_pool():
    used = []

    async def fake_run_search(fn, *args):
        used.append("search")
        return []

    async def fake_run_blocking(fn, *args):
        used.append("blocking")
        return fn(*args)

    async def go():
        with patch.object(runtime, "run_search", fake_run_search), \
             patch.object(runtime, "run_blocking", fake_run_blocking):
            await web_search._search_within("SJSU parking", budget=1.0)

    asyncio.run(go())
    assert used == ["search", "search"]
