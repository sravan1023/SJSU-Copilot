"""
Run the real backend app against a fake provider and fake retrieval.

The FastAPI app, router, token budget, SSE framing, citation handling, thread
pool and connection pools are all the real ones. Only the outside world is
replaced, inside this process:
  - Groq       -> bench/mock_upstream.py, on its own thread and event loop
  - web search -> a blocking sleep on the bounded pool, like DDGS, then canned
                  sjsu.edu results
  - page fetch -> an async sleep, then a canned ~40 KB HTML page, so the
                  BeautifulSoup extract still does real work

Production code has no bypass switch for any of this; the SSRF guards in
web_search are untouched and simply not reached.

Usage, from backend/:
    python -m bench.serve_mock --log bench/results/server-mock.jsonl
Then point bench.run_bench at http://127.0.0.1:8100.
"""
import argparse
import asyncio
import logging
import os
import random
import sys
import threading
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# ~45 KB of HTML but only ~1.8 KB of main text, like a real campus page: most of
# the bytes are navigation and scripts that the extractor has to parse and drop.
# Five of these fit the retrieval context limit, as five real sources did.
PAGE_HTML = (
    "<html><head><title>SJSU bench page</title><script>"
    + "var menu = " + '{"item": "Admissions and Aid", "href": "/admissions/"},' * 400
    + "</script></head><body><nav>"
    + '<a href="/departments/">Colleges and Departments</a> ' * 300
    + "</nav><main>"
    + "<p>Curricular practical training lets F-1 students take internships that are part "
      "of their curriculum. Students must be enrolled full time, have a written offer, "
      "and register for the internship course before the start date.</p>" * 8
    + "</main><footer>footer</footer></body></html>"
)

SEARCH_RESULTS = [
    {
        "url": f"https://www.sjsu.edu/bench/page-{i}",
        "title": f"SJSU bench page {i}",
        "snippet": "Curricular practical training for F-1 students at SJSU.",
    }
    for i in range(8)
]


def _parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=8100, help="backend port")
    p.add_argument("--upstream-port", type=int, default=8101, help="mock provider port")
    p.add_argument("--log", type=Path, help="also write the JSON log here (for report.py)")
    p.add_argument("--backend-dir", type=Path,
                   help="serve another checkout's backend/ instead (e.g. an older commit, for before/after)")
    p.add_argument("--search-ms", type=float, default=1800, help="per search call (two run per turn)")
    p.add_argument("--crawl-ms", type=float, default=700, help="per page fetch")
    p.add_argument("--ttft-ms", type=float, default=450, help="provider time to first token")
    p.add_argument("--token-ms", type=float, default=2, help="provider delay between tokens")
    p.add_argument("--tokens", type=int, default=300, help="tokens per answer")
    p.add_argument("--rewrite-ms", type=float, default=450, help="provider non-streaming latency")
    p.add_argument("--jitter", type=float, default=0.3, help="+/- fraction on every delay")
    p.add_argument("--rate-limit-rate", type=float, default=0.0, help="share of provider calls answered 429")
    return p.parse_args()


def _jittered(ms: float, jitter: float) -> float:
    return max(0.0, ms * random.uniform(1 - jitter, 1 + jitter)) / 1000


def run():
    args = _parse_args()

    # Must be set before the app is imported: llm.py reads them at import.
    os.environ["GROQ_API_URL"] = f"http://127.0.0.1:{args.upstream_port}/openai/v1/chat/completions"
    os.environ["GROQ_API_KEY"] = "mock-key"

    # run_bench.py opens one guest session and drives every request through it,
    # so a concurrency sweep is one principal making N simultaneous calls --
    # exactly what the limiter exists to stop. Measuring latency is not abuse,
    # so it is off here and only here.
    os.environ["RATE_LIMIT_ENABLED"] = "false"
    # Local-only signing key, so the harness can mint the session it needs
    # without a real .env. Never reaches a network.
    os.environ.setdefault("GUEST_JWT_SECRET", "bench-mock-guest-secret-not-a-real-key")

    import uvicorn

    from bench import mock_upstream
    from logging_config import JsonFormatter

    if args.backend_dir:
        # The other checkout's modules (main, routers, services) must win the
        # import, so take this backend off the path first.
        here = {str(BACKEND), "", os.getcwd()}
        sys.path[:] = [p for p in sys.path if p not in here]
        sys.path.insert(0, str(args.backend_dir.resolve()))

    import main as backend
    from services import llm, web_search

    # Older checkouts hard-code the provider URL instead of reading GROQ_API_URL.
    llm.GROQ_API_URL = os.environ["GROQ_API_URL"]

    mock_upstream.CONFIG.ttft_ms = args.ttft_ms
    mock_upstream.CONFIG.token_ms = args.token_ms
    mock_upstream.CONFIG.tokens = args.tokens
    mock_upstream.CONFIG.nonstream_ms = args.rewrite_ms
    mock_upstream.CONFIG.jitter = args.jitter
    mock_upstream.CONFIG.rate_limit_rate = args.rate_limit_rate

    def fake_search(query):
        time.sleep(_jittered(args.search_ms, args.jitter))  # blocking, like DDGS
        return [dict(r) for r in SEARCH_RESULTS]

    async def fake_fetch(client, url):
        await asyncio.sleep(_jittered(args.crawl_ms, args.jitter))
        return url, PAGE_HTML

    web_search.search_web = fake_search
    web_search._safe_stream_get = fake_fetch

    if args.log:
        args.log.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(args.log, encoding="utf-8")
        handler.setFormatter(JsonFormatter())
        logging.getLogger().addHandler(handler)

    class _ThreadServer(uvicorn.Server):
        def install_signal_handlers(self):
            pass

    upstream = _ThreadServer(uvicorn.Config(
        mock_upstream.app, host="127.0.0.1", port=args.upstream_port,
        log_level="warning", log_config=None, access_log=False,
    ))
    threading.Thread(target=upstream.run, daemon=True).start()
    deadline = time.monotonic() + 15
    while not upstream.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not upstream.started:
        raise SystemExit("mock upstream did not start")

    print(
        f"mock backend on http://127.0.0.1:{args.port}  "
        f"(provider mock on :{args.upstream_port}, stats at /stats)",
        flush=True,
    )
    # log_config=None keeps the app's JSON logging instead of uvicorn's.
    uvicorn.run(backend.app, host="127.0.0.1", port=args.port, log_config=None, access_log=False)


if __name__ == "__main__":
    run()
