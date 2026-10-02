"""Calibrating knowledge-base retrieval, without spending a token.

    python -m kb.eval_retrieval --coverage
    python -m kb.eval_retrieval --questions bench/questions.jsonl
    python -m kb.eval_retrieval --question "where can visitors park"

This calls `search_kb` directly and prints what came back. No LLM, no generation,
no token metering -- seconds per run instead of the SSE benchmark's ~14 minutes.
That makes it the iteration tool for `KB_MIN_RANK`, `KB_STRONG_RANK`, chunk size
and overlap; `bench.run_bench` is the confirmation afterwards, not the loop.

**Run `--coverage` before anything else.** If the page holding an answer was never
ingested, every rank below is measuring a gap in `kb/seeds.json` and reporting it
as a retrieval failure -- and the fix is a seed, not a threshold.

**The denominator is 10, not 16.** `bench/questions.jsonl` labels sixteen
questions `faq` or `long`, and the plan treated all sixteen as the coverage
target. Measured on 2026-10-01: four are conversational ("thanks!", "Can you make
that shorter?") and never reach retrieval, because `prepare_rag_query`'s gates
catch them first; two more are time-sensitive and are refused by
`services/freshness.py` by design. So this script applies **both** real gates
before counting, rather than filtering on `kind` alone -- otherwise it reports a
miss on a question the system is working correctly by declining to answer, and
the only honest score becomes unreachable.

Of those ten, one is known uncovered: `student-faq-2` ("What GPA do I need to stay
in good academic standing?"). Ten candidate URLs 404 and SJSU's own navigation
links only to grade-change pages; the policy lives in the catalog or a Senate
S-policy, which needs `crawl_depth > 0` or the slow catalog host. **Expect 9 of
10.**

`KB_RETRIEVAL_ENABLED` is deliberately ignored here: this tool exists to decide
whether that flag should be turned on, so it would be circular to require it.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import pathlib
import statistics
import time

import httpx
from dotenv import load_dotenv

from kb import store
from services import kb_retrieval
from services.freshness import explain, is_time_sensitive

logger = logging.getLogger(__name__)

REPO_BACKEND = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_QUESTIONS = REPO_BACKEND / "bench" / "questions.jsonl"

# The kinds the corpus is meant to answer, and the one it must not.
KB_KINDS = ("faq", "long")
CONTROL_KIND = "fresh"


def load_questions(path: pathlib.Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def question_text(row: dict) -> str:
    return row["messages"][-1]["content"]


def reaches_retrieval(row: dict) -> bool:
    """Would this question actually get as far as a retrieval call?

    Imported from web_search rather than reimplemented, so this tracks the real
    gates instead of a copy of them that can drift. A question the system
    deliberately declines to retrieve on is not a coverage miss.
    """
    from services.web_search import prepare_rag_query

    return bool(prepare_rag_query(row["messages"]))


async def coverage(client: httpx.AsyncClient) -> int:
    """What is actually in the corpus, by collection."""
    documents = await store._request(
        client,
        "GET",
        "documents",
        params={"select": "url,title,collection,last_verified_at", "order": "collection,url"},
    ) or []
    chunks = await store._request(
        client,
        "GET",
        "document_chunks",
        params={"select": "id,embedding", "limit": "20000"},
    ) or []

    print(f"documents: {len(documents)}   chunks: {len(chunks)}")
    if not documents:
        print(
            "\nThe corpus is empty. Run:\n"
            "    python -m kb.ingest --sync-seeds\n"
            "    python -m kb.ingest\n"
        )
        return 1

    with_vectors = sum(1 for c in chunks if c.get("embedding"))
    print(f"chunks with an embedding: {with_vectors}/{len(chunks)}")
    if with_vectors == 0:
        # Not fatal, but it halves the retrieval design, so it should never be a
        # surprise discovered later via disappointing ranks.
        print("  !! no embeddings at all -- the vector arm of the fusion is inert.")
        print("     Check GOOGLE_API_KEY, then re-run ingestion with --force.")

    by_collection: dict[str, int] = {}
    for doc in documents:
        by_collection[doc.get("collection") or "(none)"] = (
            by_collection.get(doc.get("collection") or "(none)", 0) + 1
        )
    print("\nby collection:")
    for collection, count in sorted(by_collection.items()):
        print(f"  {collection:<20} {count}")
    return 0


async def evaluate(
    questions: list[dict], *, k: int, verbose: bool
) -> dict:
    """Search each question and report rank, sufficiency and latency."""
    results = []
    for row in questions:
        text = question_text(row)
        started = time.perf_counter()
        chunks = await kb_retrieval.search_kb(
            text, row.get("audience"), include_authenticated=False, k=k
        )
        elapsed_ms = (time.perf_counter() - started) * 1000

        fresh = is_time_sensitive(text)
        reaches = reaches_retrieval(row)
        verdict = kb_retrieval.sufficient(chunks)
        documents = {c.document_id for c in chunks}
        results.append(
            {
                "id": row.get("id"),
                "kind": row.get("kind"),
                "question": text,
                "time_sensitive": fresh,
                "reaches": reaches,
                "hits": len(chunks),
                "documents": len(documents),
                "top_rank": max((c.rank for c in chunks), default=0.0),
                "sufficient": verdict,
                "ms": elapsed_ms,
            }
        )

        # "n/a" matters: without it an excluded question still printed "OK", which
        # reads as "the corpus answers this" for a turn that never reaches
        # retrieval at all -- and makes the summary's denominator look wrong.
        if not reaches:
            flag = " n/a "
        elif fresh:
            flag = "FRESH"
        else:
            flag = "  OK " if verdict else " weak"
        print(
            f"[{flag}] {row.get('kind',''):<10} {row.get('id','')}\n"
            f"        {text[:96]}\n"
            f"        {len(chunks)} chunks / {len(documents)} docs, "
            f"top {max((c.rank for c in chunks), default=0.0):.4f}, {elapsed_ms:.0f}ms"
        )
        if not reaches:
            print("        gated before retrieval (conversational or meta) -- excluded")
        elif fresh:
            print(f"        time-sensitive via {explain(text)} -- the KB is skipped on this one")
        if verbose:
            for chunk in chunks[:5]:
                print(
                    f"          {chunk.rank:.4f}  {chunk.heading or '-'}\n"
                    f"                  {chunk.url}"
                )
        print()

    return summarise(results)


def summarise(results: list[dict]) -> dict:
    kb_rows = [
        r
        for r in results
        if r["kind"] in KB_KINDS and not r["time_sensitive"] and r["reaches"]
    ]
    control = [r for r in results if r["kind"] == CONTROL_KIND]
    answered = [r for r in kb_rows if r["sufficient"]]

    excluded = [
        r for r in results
        if r["kind"] in KB_KINDS and (r["time_sensitive"] or not r["reaches"])
    ]

    print("=" * 72)
    print(
        f"answerable from the corpus: {len(answered)}/{len(kb_rows)} of the "
        f"faq/long questions that reach retrieval"
    )
    if excluded:
        # Named, not silently dropped: an unexplained denominator of 10 against a
        # file of 16 looks like a bug in the harness.
        print(f"  ({len(excluded)} excluded -- conversational or time-sensitive by design)")

    if control:
        leaked = [r for r in control if r["sufficient"] and not r["time_sensitive"]]
        print(f"control (kind=fresh):       {len(leaked)}/{len(control)} would answer from the corpus")
        if leaked:
            # The single most important line in this report. A corpus that also
            # answers the date questions has not improved grounding; it has found
            # a faster way to serve a stale deadline with a citation on it.
            print("  !! these must be 0. Each one is a stale date answered confidently:")
            for row in leaked:
                print(f"     {row['id']}: {row['question'][:70]}")

    latencies = [r["ms"] for r in results]
    if latencies:
        print(
            f"latency: p50 {statistics.median(latencies):.0f}ms  "
            f"max {max(latencies):.0f}ms"
        )

    ranks = sorted((r["top_rank"] for r in kb_rows if r["hits"]), reverse=True)
    if ranks:
        print(f"top ranks: best {ranks[0]:.4f}  median {statistics.median(ranks):.4f}  worst {ranks[-1]:.4f}")
        print(
            f"\nKB_MIN_RANK is {kb_retrieval.min_rank()} and KB_STRONG_RANK is "
            f"{kb_retrieval.strong_rank()}."
        )
        # Suggesting rather than setting: the thresholds are a judgement about how
        # much a wrong answer costs relative to a slow one, and that is not a
        # number this script can derive.
        misses = [r["top_rank"] for r in kb_rows if r["hits"] and not r["sufficient"]]
        if misses:
            print(
                f"The {len(misses)} unanswered questions that did return chunks peak at "
                f"{max(misses):.4f}; lowering KB_MIN_RANK below that admits them, "
                "along with whatever else sits at that score."
            )

    return {
        "answerable": len(answered),
        "kb_questions": len(kb_rows),
        "control_leaks": len([r for r in control if r["sufficient"]]),
    }


def _explain_store_error(exc: "store.StoreError") -> str:
    """Turn a PostgREST error into the action that fixes it.

    The overwhelmingly likely cause is the migration not being on the project
    yet: `documents.collection`, `kb_sources` and `match_kb_hybrid` all arrive in
    20260930000100, and PostgREST reports each absence differently (42703, 404,
    PGRST202). A traceback tells you none of that.
    """
    text = str(exc)
    migration_missing = any(
        marker in text
        for marker in ("42703", "PGRST202", "PGRST205", "404", "does not exist")
    )
    if migration_missing:
        return (
            f"{text}\n\n"
            "This almost certainly means supabase/migrations/"
            "20260930000100_kb_hybrid_retrieval.sql is not on the project yet.\n"
            "Apply it with:\n"
            "    npx supabase --agent no db push\n"
        )
    return text


async def _main() -> int:
    parser = argparse.ArgumentParser(description="Calibrate knowledge-base retrieval.")
    parser.add_argument("--questions", type=pathlib.Path, default=None)
    parser.add_argument("--question", action="append", dest="adhoc")
    parser.add_argument("--coverage", action="store_true", help="what is in the corpus, then exit")
    parser.add_argument("--kind", action="append", help="limit to these kinds")
    parser.add_argument("-k", type=int, default=12)
    parser.add_argument("--verbose", action="store_true", help="print the top chunks")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    load_dotenv()

    if not store.configured():
        print("SUPABASE_URL or SUPABASE_SERVICE_KEY is not set.")
        return 2

    if args.coverage:
        async with httpx.AsyncClient() as client:
            try:
                return await coverage(client)
            except store.StoreError as exc:
                print(_explain_store_error(exc))
                return 2

    if args.adhoc:
        rows = [
            {"id": f"adhoc{i}", "kind": "faq", "messages": [{"role": "user", "content": q}]}
            for i, q in enumerate(args.adhoc, start=1)
        ]
    else:
        path = args.questions or DEFAULT_QUESTIONS
        if not path.exists():
            print(f"no such question file: {path}")
            return 2
        rows = load_questions(path)
        if args.kind:
            rows = [r for r in rows if r.get("kind") in set(args.kind)]

    if not rows:
        print("no questions selected")
        return 2

    # The corpus is checked first either way: ranks are uninterpretable against an
    # empty or unembedded corpus, and the failure would look like bad retrieval.
    async with httpx.AsyncClient() as client:
        try:
            if await coverage(client) != 0:
                return 1
        except store.StoreError as exc:
            print(_explain_store_error(exc))
            return 2
    print()

    stats = await evaluate(rows, k=args.k, verbose=args.verbose)
    # Non-zero when the control leaks, so this is usable as a gate and not only
    # as a report.
    return 1 if stats["control_leaks"] else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
