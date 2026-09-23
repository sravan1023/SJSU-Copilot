"""
Tests for retrieval gating: skipping retrieval for meta turns and skipping the
query rewrite when a question is already search-ready.

Run from backend/ with:
    python -m tests.test_retrieval_gating

Both gates skip work, so both are tested in *both* directions: the false
negatives (paying for retrieval that cannot help) matter for latency, but the
false positives (skipping retrieval a real question needed) are the ones that
damage answers. The "must not skip" cases below are the important half.
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import web_search  # noqa: E402
from services.web_search import (  # noqa: E402
    _is_meta_request,
    needs_query_rewrite,
    prepare_rag_query,
)

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


# ── 1. Meta requests skip retrieval ───────────────────────────────────────────


def test_meta_requests_detected():
    print("\n[1.1] requests that operate on the previous answer")
    for msg in [
        "rewrite that more simply",
        "can you shorten that",
        "summarize that",
        "summarise that please",
        "make it shorter",
        "make that more concise",
        "translate that to Spanish",
        "repeat that",
        "say that again",
        "put that in bullet points",
        "reformat it as a table",
        "simplify it",
        "tl;dr",
        "shorter",
        "simpler",
        "bullet points",
        "in a table",
        "elaborate on that",
        "clarify that",
        "expand on that",
    ]:
        _check(f"meta: {msg!r}", _is_meta_request(msg))


def test_real_questions_are_not_treated_as_meta():
    print("\n[1.2] real questions must NOT be skipped (false positives hurt answers)")
    for msg in [
        "summarize the CS degree requirements at SJSU",
        "can you explain the SJSU add drop deadline",
        "what is the SJSU tuition for graduate students",
        "rewrite my resume bullet points for a software internship",
        "how do I simplify my course schedule next semester",
        "translate my transcript request into a formal email to the registrar",
        "what are the bullet points on the SJSU CS curriculum sheet",
        "expand my knowledge of SJSU research opportunities in computer science",
        "where is the SJSU library",
        "who is the CS department chair",
        "when does spring registration open",
        "tell me about SJSU parking permits",
        "what does the SJSU catalog say about transfer credits",
        "what does the policy say about late registration",
    ]:
        _check(f"not meta: {msg!r}", not _is_meta_request(msg))


def test_prepare_rag_query_skips_meta():
    print("\n[1.3] prepare_rag_query returns None for meta turns")
    convo = [
        {"role": "user", "content": "what are the SJSU graduation requirements"},
        {"role": "assistant", "content": "A long answer about units and GE."},
        {"role": "user", "content": "rewrite that more simply"},
    ]
    _check("meta follow-up skips retrieval", prepare_rag_query(convo) is None)

    convo_real = [
        {"role": "user", "content": "what are the SJSU graduation requirements"},
        {"role": "assistant", "content": "A long answer."},
        {"role": "user", "content": "what about the GE requirements specifically"},
    ]
    _check(
        "genuine follow-up still retrieves",
        prepare_rag_query(convo_real) is not None,
        str(prepare_rag_query(convo_real)),
    )

    _check(
        "plain greeting still skips",
        prepare_rag_query([{"role": "user", "content": "thanks!"}]) is None,
    )


# ── 2. Selective query rewriting ──────────────────────────────────────────────


def test_rewrite_skipped_when_already_searchable():
    print("\n[2.1] already-anchored, specific questions skip the rewrite call")
    for msg in [
        "what is the SJSU add drop deadline",
        "SJSU computer science degree requirements",
        "where is the SJSU library located",
        "how much is SJSU parking per semester",
        "san jose state spring registration dates",
    ]:
        _check(f"skip rewrite: {msg!r}", not needs_query_rewrite(msg))


def test_rewrite_kept_when_it_helps():
    print("\n[2.2] the rewrite is kept where it earns its round trip")
    cases = [
        ("no SJSU anchor", "when does registration open for spring"),
        ("too terse", "parking"),
        ("terse acronym", "CPT rules"),
        ("acronym needs expansion", "what are the SJSU CPT requirements"),
        ("I-20 acronym", "how do I get my SJSU I-20 reissued"),
        ("FAFSA acronym", "SJSU FAFSA priority deadline this year"),
        (
            "long and rambling",
            "hey so I was wondering about the SJSU thing where you have to "
            "register for classes and I heard there is some kind of deadline "
            "that I might have already missed",
        ),
    ]
    for label, msg in cases:
        _check(f"rewrite kept ({label})", needs_query_rewrite(msg), msg)


def test_rewrite_call_is_actually_skipped():
    print("\n[2.3] the provider call is genuinely not made")
    calls = []

    async def counting_rewrite(question, audience=None):
        calls.append(question)
        return question

    def fake_search(query):
        return [{"title": "SJSU", "url": "https://sjsu.edu/a", "snippet": "snippet text"}]

    async def no_crawl(client, url):
        return url, "<html><body><main>page body</main></body></html>"

    async def drive(question):
        calls.clear()
        with patch.object(web_search, "rewrite_query_for_sjsu", counting_rewrite), \
             patch.object(web_search, "search_web", fake_search), \
             patch.object(web_search, "_safe_stream_get", no_crawl):
            await web_search.build_rag_prompt([{"role": "user", "content": question}])
        return list(calls)

    skipped = asyncio.run(drive("what is the SJSU add drop deadline"))
    _check("no rewrite call for a search-ready question", skipped == [], str(skipped))

    kept = asyncio.run(drive("when does registration open for spring"))
    _check("rewrite call made when needed", len(kept) == 1, str(kept))


def test_meta_request_makes_no_network_calls_at_all():
    print("\n[2.4] a meta turn costs no rewrite, no search, no crawl")
    calls = {"rewrite": 0, "search": 0, "crawl": 0}

    async def counting_rewrite(question):
        calls["rewrite"] += 1
        return question

    def counting_search(query):
        calls["search"] += 1
        return []

    async def counting_crawl(client, url):
        calls["crawl"] += 1
        return url, ""

    async def drive():
        with patch.object(web_search, "rewrite_query_for_sjsu", counting_rewrite), \
             patch.object(web_search, "search_web", counting_search), \
             patch.object(web_search, "_safe_stream_get", counting_crawl):
            return await web_search.build_rag_prompt([
                {"role": "user", "content": "what are the SJSU graduation requirements"},
                {"role": "assistant", "content": "A long answer."},
                {"role": "user", "content": "make that shorter"},
            ])

    prompt, sources = asyncio.run(drive())
    _check("no rewrite call", calls["rewrite"] == 0, str(calls))
    _check("no search call", calls["search"] == 0, str(calls))
    _check("no crawl call", calls["crawl"] == 0, str(calls))
    _check("no rag prompt produced", prompt is None)
    _check("no sources produced", sources == [])


def run():
    test_meta_requests_detected()
    test_real_questions_are_not_treated_as_meta()
    test_prepare_rag_query_skips_meta()
    test_rewrite_skipped_when_already_searchable()
    test_rewrite_kept_when_it_helps()
    test_rewrite_call_is_actually_skipped()
    test_meta_request_makes_no_network_calls_at_all()

    print("\n" + "=" * 60)
    print(f"  Passed: {PASS}")
    print(f"  Failed: {FAIL}")
    print("=" * 60)
    for label, detail in FAILURES:
        print(f"  - {label}: {detail}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(run())
