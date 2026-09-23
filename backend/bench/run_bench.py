"""
Drive /api/chat with the benchmark conversations and record client-side timings.

Each request carries its own X-Request-Id, so bench/report.py can join these
results to the server's "request timings" log lines.

    # mock mode (start bench.serve_mock first)
    python -m bench.run_bench --mode mock --concurrency 10

    # real provider: concurrency 1-3 only, paced for the free tier
    python -m bench.run_bench --mode real --base-url http://127.0.0.1:8000 \
        --limit 8 --pace 20

Rule: concurrency sweeps are mock-only. Groq's free
tier allows 30 requests and 8,000 tokens per minute for the whole
organisation, so a real run above concurrency 3 measures 429 handling and
nothing else.
"""
import argparse
import asyncio
import json
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

BENCH = Path(__file__).resolve().parent
MAX_REAL_CONCURRENCY = 3


def _parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=["mock", "real"], required=True)
    p.add_argument("--base-url", default=None, help="default: :8100 for mock, :8000 for real")
    p.add_argument("--concurrency", type=int, default=1)
    p.add_argument("--model", default="quality", choices=["fast", "quality"])
    p.add_argument("--questions", type=Path, default=BENCH / "questions.jsonl")
    p.add_argument("--only", nargs="*", help="question ids (or prefixes, e.g. guest-) to include")
    p.add_argument("--limit", type=int, help="use only the first N questions")
    p.add_argument("--repeat", type=int, default=1, help="send the question set this many times")
    p.add_argument("--warmup", type=int, default=2, help="unrecorded requests sent first")
    p.add_argument("--pace", type=float, default=0.0, help="seconds each worker waits between requests")
    p.add_argument("--out", type=Path, help="default: bench/results/<mode>-c<N>-<time>.jsonl")
    p.add_argument("--allow-high-concurrency", action="store_true",
                   help=f"permit --mode real above concurrency {MAX_REAL_CONCURRENCY}")
    return p.parse_args()


def _load(args):
    rows = [json.loads(line) for line in args.questions.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.only:
        rows = [r for r in rows if any(r["id"] == o or r["id"].startswith(o) for o in args.only)]
    if args.limit:
        rows = rows[: args.limit]
    return rows * args.repeat


def _ms(start):
    return round((time.perf_counter() - start) * 1000, 1)


async def _acquire_guest_token(client, base_url):
    """Get a session the way a visitor's browser does.

    The harness used to post with no Authorization header at all and relied on
    AUTH_OPTIONAL to let it through. That flag is gone -- it was a switch that
    admitted unauthenticated callers, which stops being tolerable once a guest
    principal is a legitimate one. Minting a real token here means the bench
    now measures the same code path a real request takes, verification
    included, instead of one that skipped it.

    Also note the limiter: a sweep at concurrency N will trip the per-principal
    budget, because every request shares this one guest id. Set
    RATE_LIMIT_ENABLED=false on the server for sweeps, which is what
    bench/serve_mock.py does.
    """
    res = await client.post(f"{base_url}/api/guest/session")
    if res.status_code != 200:
        raise SystemExit(
            f"could not start a guest session ({res.status_code}). "
            "Is GUEST_JWT_SECRET set on the server? It answers 503 without one."
        )
    return res.json()["token"]


async def _one(client, base_url, model, question, request_id, headers=None):
    record = {
        "request_id": request_id,
        "id": question["id"],
        "audience": question["audience"],
        "kind": question["kind"],
        "conversation": "existing" if len(question["messages"]) > 1 else "new",
        "http_status": None,
        "error": None,
        "keepalives": 0,
        "sources": 0,
        "answer_chars": 0,
    }
    start = time.perf_counter()
    try:
        async with client.stream(
            "POST",
            f"{base_url}/api/chat",
            # questions.jsonl already labels every question with the audience
            # it belongs to, so the sweep exercises all four prompt variants
            # and all four source scopings rather than measuring 'student' 40
            # times. This is what makes a before/after diff of sources per
            # audience possible after a retrieval change.
            json={
                "messages": question["messages"],
                "model": model,
                "audience": question["audience"],
            },
            headers={**(headers or {}), "x-request-id": request_id},
        ) as res:
            record["http_status"] = res.status_code
            record["headers_ms"] = _ms(start)
            if res.status_code != 200:
                await res.aread()
                record["error"] = f"http_{res.status_code}"
                return record
            async for line in res.aiter_lines():
                if line.startswith(":"):
                    record["keepalives"] += 1
                    continue
                if not line.startswith("data: "):
                    continue
                frame = json.loads(line[6:])
                now = _ms(start)
                if "error" in frame:
                    error = frame["error"]
                    record["error"] = error if isinstance(error, str) else error.get("code", "error")
                    if isinstance(error, dict) and error.get("retry_after") is not None:
                        record["retry_after"] = error["retry_after"]
                elif "status" in frame:
                    record.setdefault("first_status_ms", now)
                    record.setdefault(f"{frame['status']}_ms", now)
                elif "token" in frame:
                    record.setdefault("first_token_ms", now)
                elif frame.get("done"):
                    record["done_ms"] = now
                    record["sources"] = len(frame.get("sources") or [])
                    full = frame.get("full_response") or ""
                    record["answer_chars"] = len(full)
                    record["citations"] = len(re.findall(r"\[\d+\]", full))
                    # gpt-oss's own citation form, if it reached the answer un-normalised
                    record["raw_citation_markers"] = full.count("【")
    except httpx.HTTPError as exc:
        record["error"] = type(exc).__name__
    record["end_ms"] = _ms(start)
    return record


async def _run(args):
    base_url = args.base_url or ("http://127.0.0.1:8100" if args.mode == "mock" else "http://127.0.0.1:8000")
    questions = _load(args)
    run_id = uuid.uuid4().hex[:8]
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    meta = {"run_id": run_id, "mode": args.mode, "concurrency": args.concurrency,
            "model": args.model, "started_at": started_at}

    limits = httpx.Limits(max_connections=args.concurrency + 2, max_keepalive_connections=args.concurrency)
    async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=10), limits=limits) as client:
        token = await _acquire_guest_token(client, base_url)
        headers = {"Authorization": f"Bearer {token}"}
        meta["principal"] = "guest"

        for i in range(min(args.warmup, len(questions))):
            await _one(client, base_url, args.model, questions[i], f"warm-{run_id}-{i}", headers)

        queue: asyncio.Queue = asyncio.Queue()
        for i, q in enumerate(questions):
            queue.put_nowait((i, q))
        results = []

        async def worker():
            while True:
                try:
                    i, q = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                results.append({**meta, **await _one(client, base_url, args.model, q, f"bench-{run_id}-{i:04d}", headers)})
                done = len(results)
                print(f"\r  {done}/{len(questions)}", end="", flush=True)
                if args.pace:
                    await asyncio.sleep(args.pace)

        wall = time.perf_counter()
        await asyncio.gather(*(worker() for _ in range(args.concurrency)))
        wall_s = time.perf_counter() - wall
    print()
    return results, wall_s, run_id


def main():
    args = _parse_args()
    if args.mode == "real" and args.concurrency > MAX_REAL_CONCURRENCY and not args.allow_high_concurrency:
        sys.exit(
            f"refusing --mode real at concurrency {args.concurrency}: against Groq's free tier this "
            f"measures 429s, not latency. Sweep concurrency in mock mode instead "
            f"(or pass --allow-high-concurrency)."
        )

    results, wall_s, run_id = asyncio.run(_run(args))
    out = args.out or BENCH / "results" / f"{args.mode}-c{args.concurrency}-{run_id}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r) + "\n" for r in results), encoding="utf-8")

    errors = [r["error"] for r in results if r["error"]]
    first_tokens = sorted(r["first_token_ms"] for r in results if "first_token_ms" in r)
    median = first_tokens[len(first_tokens) // 2] if first_tokens else None
    print(f"{len(results)} requests in {wall_s:.1f}s, {len(errors)} errors"
          + (f" ({', '.join(sorted(set(errors)))})" if errors else "")
          + (f", median first token {median:.0f} ms" if median is not None else ""))
    print(f"results: {out}")


if __name__ == "__main__":
    main()
