"""Survey the seed list: what we can fetch, what we chunk, what we miss.

Reads nothing from and writes nothing to the database, so it runs before the KB
migration is live. It answers the two questions stage 4B exists to answer before
any row is written:

  1. Do the seeds actually yield usable text, and how much?
  2. What is being skipped, and for what reason -- in particular how much of
     SJSU is PDF-only or JavaScript-rendered.

    python -m kb.survey                 # every seed
    python -m kb.survey --collection registrar
    python -m kb.survey --links         # also count same-site links per page
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import json
import pathlib
import statistics
import time

import httpx

from kb.chunker import chunk_html, chunk_text
from kb.fetcher import conditional_get
from kb.robots import USER_AGENT, RobotsCache

SEEDS = pathlib.Path(__file__).with_name("seeds.json")

# Our own floor. robots.txt may ask for more -- catalog.sjsu.edu asks for 120 --
# and the larger of the two always wins.
DEFAULT_DELAY = 1.0


def load_seeds(collection: str | None = None) -> list[dict]:
    data = json.loads(SEEDS.read_text(encoding="utf-8"))
    rows = data["sources"]
    if collection:
        rows = [r for r in rows if r.get("collection") == collection]
    return rows


async def survey(seeds: list[dict], *, want_links: bool, delay: float) -> dict:
    outcomes: collections.Counter[str] = collections.Counter()
    per_page: list[dict] = []
    host_last: dict[str, float] = {}
    host_locks: dict[str, asyncio.Lock] = collections.defaultdict(asyncio.Lock)

    async with httpx.AsyncClient(
        timeout=30, follow_redirects=False, headers={"User-Agent": USER_AGENT}
    ) as client:
        robots = RobotsCache(client=client)

        for seed in seeds:
            url = seed["url"]
            host = httpx.URL(url).host
            rules = await robots.for_url(url)
            wait = max(delay, rules.crawl_delay or 0.0)

            async with host_locks[host]:
                since = time.monotonic() - host_last.get(host, 0.0)
                if host_last.get(host) and since < wait:
                    await asyncio.sleep(wait - since)
                result = await conditional_get(
                    client, url, rules=rules, want_links=want_links,
                    allowed_hosts=[host],
                )
                host_last[host] = time.monotonic()

            outcomes[result.outcome.value] += 1
            row = {
                "url": url,
                "collection": seed.get("collection"),
                "outcome": result.outcome.value,
                "status": result.status,
                "chars": len(result.text or ""),
                "chunks": 0,
                "links": len(result.links),
                "detail": result.detail,
                "crawl_delay": rules.crawl_delay,
            }
            if result.ok and result.text:
                # HTML goes through the heading-aware path; a PDF has no markup,
                # so its text is chunked as one unheaded section.
                chunks = (
                    chunk_html(result.html)
                    if result.html
                    else chunk_text(result.text)
                )
                row["chunks"] = len(chunks)
                row["chunk_chars"] = [c.char_count for c in chunks]
                row["headings"] = sum(1 for c in chunks if c.heading)
            per_page.append(row)
            print(
                f"  {row['outcome']:<18} {str(row['status'] or ''):>4}  "
                f"chars={row['chars']:>6}  chunks={row['chunks']:>3}  {url}"
                + (f"   ({row['detail']})" if row["detail"] else "")
            )

    return {"outcomes": dict(outcomes), "pages": per_page}


def report(data: dict) -> None:
    pages = data["pages"]
    ok = [p for p in pages if p["outcome"] == "ok"]

    print("\n" + "=" * 72)
    print(f"  {len(pages)} seeds")
    print("\n  outcomes:")
    for name, count in sorted(data["outcomes"].items(), key=lambda kv: -kv[1]):
        print(f"    {name:<20} {count}")

    if ok:
        chars = [p["chars"] for p in ok]
        chunks = [p["chunks"] for p in ok]
        all_chunk_chars = [n for p in ok for n in p.get("chunk_chars", [])]
        headed = sum(p.get("headings", 0) for p in ok)
        total_chunks = sum(chunks)
        print(f"\n  usable pages:        {len(ok)} of {len(pages)}")
        print(f"  extracted chars:     median {int(statistics.median(chars))}, "
              f"min {min(chars)}, max {max(chars)}")
        print(f"  chunks per page:     median {int(statistics.median(chunks))}, "
              f"total {total_chunks}")
        if all_chunk_chars:
            print(f"  chunk size:          median {int(statistics.median(all_chunk_chars))}, "
                  f"min {min(all_chunk_chars)}, max {max(all_chunk_chars)}")
        print(f"  chunks with heading: {headed} of {total_chunks}")

    skipped = [p for p in pages if p["outcome"] != "ok"]
    if skipped:
        print("\n  what we are missing:")
        for p in skipped:
            print(f"    {p['outcome']:<20} {p['url']}  {p.get('detail') or ''}")

    delays = {p["url"].split("/")[2]: p["crawl_delay"] for p in pages if p["crawl_delay"]}
    if delays:
        print("\n  robots crawl delays:")
        for host, d in delays.items():
            print(f"    {host:<28} {d}s  -> {int(3600 / d)} pages/hour")
    print("=" * 72)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--collection")
    ap.add_argument("--links", action="store_true")
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    ap.add_argument("--out", type=pathlib.Path)
    args = ap.parse_args()

    seeds = load_seeds(args.collection)
    print(f"surveying {len(seeds)} seeds (delay >= {args.delay}s, robots honoured)\n")
    data = asyncio.run(survey(seeds, want_links=args.links, delay=args.delay))
    report(data)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
