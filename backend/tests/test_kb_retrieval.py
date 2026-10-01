"""
Knowledge-base retrieval, and the branch it adds to the chat path.

Run from backend/ with:
    python -m pytest tests/test_kb_retrieval.py

Two properties matter more than the rest, and they are asserted first:

* **`KB_RETRIEVAL_ENABLED=false` is the entire rollback for Phase 4**, so it has
  to be provably inert rather than probably inert. The default flipped to *on* on
  2026-10-01, which makes this stronger rather than weaker: the off switch is now
  the only way back to pre-Phase-4 grounding, so it is the thing most worth a
  test.
* **A guest never sees `visibility='authenticated'` content.** The backend reads
  with the service key, which bypasses RLS, so `p_include_authenticated` is the
  only thing standing between a visitor and an internal page. Nothing else in the
  request path checks it.
"""
import asyncio
import os
from unittest.mock import patch

import pytest

from services import kb_retrieval
from services.kb_retrieval import KbChunk, kb_items, sufficient
from services.web_search import MAX_SOURCES, MAX_TOTAL_CHARS, assemble_context, build_rag_prompt

ON = {"KB_RETRIEVAL_ENABLED": "true"}


def _chunk(**kw) -> KbChunk:
    base = dict(
        document_id="doc-1",
        chunk_index=0,
        heading=None,
        content="Visitor parking is in the North Garage.",
        title="Parking",
        url="https://www.sjsu.edu/parking/",
        collection="visiting",
        rank=0.5,
        fetched_at="2026-09-28T00:00:00+00:00",
        last_verified_at="2026-09-28T00:00:00+00:00",
        valid_until=None,
    )
    base.update(kw)
    return KbChunk(**base)


MESSAGES = [{"role": "user", "content": "where can visitors park at sjsu"}]


# ── The flag is the rollback ───────────────────────────────────────────────────


def test_the_knowledge_base_is_not_consulted_when_the_flag_is_off():
    called = False

    async def _spy(*a, **kw):
        nonlocal called
        called = True
        return [_chunk()]

    async def _no_search(*a, **kw):
        return []

    with patch.dict(os.environ, {"KB_RETRIEVAL_ENABLED": "false"}), \
         patch.object(kb_retrieval, "search_kb", _spy), \
         patch("services.web_search._search_within", _no_search):
        asyncio.run(build_rag_prompt(MESSAGES, "guest"))

    assert not called, "the flag must make the KB path unreachable, not merely unused"


def test_the_default_is_on():
    """Flipped 2026-10-01, after 4D measured that the corpus helps.

    Asserted because the default is the whole difference between "the knowledge
    base works on one laptop" and "the knowledge base works". A silent revert to
    false would look like nothing at all: answers would simply go back to being
    slower and web-sourced.
    """
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("KB_RETRIEVAL_ENABLED", None)
        assert kb_retrieval.enabled() is True


def test_enabled_reads_the_env_per_call():
    """Import-time capture would make the flag untestable and undeployable."""
    with patch.dict(os.environ, {"KB_RETRIEVAL_ENABLED": "true"}):
        assert kb_retrieval.enabled() is True
    with patch.dict(os.environ, {"KB_RETRIEVAL_ENABLED": "false"}):
        assert kb_retrieval.enabled() is False


# ── The guest boundary ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "principal_kind,expected",
    [("guest", False), ("user", True)],
)
def test_include_authenticated_follows_the_principal(principal_kind, expected):
    seen = {}

    async def _capture(question, audience, *, include_authenticated, k=None):
        seen["include_authenticated"] = include_authenticated
        return []

    async def _no_search(*a, **kw):
        return []

    with patch.dict(os.environ, ON), \
         patch.object(kb_retrieval, "search_kb", _capture), \
         patch("services.web_search._search_within", _no_search):
        asyncio.run(build_rag_prompt(MESSAGES, "guest", principal_kind=principal_kind))

    assert seen["include_authenticated"] is expected


def test_an_unknown_principal_kind_under_shares():
    """The default must be the least-privileged value, not the most useful one."""
    seen = {}

    async def _capture(question, audience, *, include_authenticated, k=None):
        seen["include_authenticated"] = include_authenticated
        return []

    async def _no_search(*a, **kw):
        return []

    with patch.dict(os.environ, ON), \
         patch.object(kb_retrieval, "search_kb", _capture), \
         patch("services.web_search._search_within", _no_search):
        # No principal_kind passed at all -- the signature default applies.
        asyncio.run(build_rag_prompt(MESSAGES, "guest"))

    assert seen["include_authenticated"] is False


# ── Branch behaviour ──────────────────────────────────────────────────────────


def test_a_good_hit_answers_without_touching_the_web():
    searched = False

    async def _search_spy(*a, **kw):
        nonlocal searched
        searched = True
        return []

    async def _kb(*a, **kw):
        return [_chunk(document_id="d1", rank=0.5), _chunk(document_id="d2", rank=0.4)]

    with patch.dict(os.environ, ON), \
         patch.object(kb_retrieval, "search_kb", _kb), \
         patch("services.web_search._search_within", _search_spy):
        prompt, sources = asyncio.run(build_rag_prompt(MESSAGES, "guest"))

    assert prompt and sources, "a sufficient KB hit must produce a prompt"
    assert not searched, "the whole point is skipping the search and the crawl"
    assert all(set(s) == {"title", "url"} for s in sources)


def test_a_time_sensitive_question_skips_the_knowledge_base():
    """A stored answer to a date question is wrong with a citation attached."""
    consulted = False

    async def _kb(*a, **kw):
        nonlocal consulted
        consulted = True
        return [_chunk()]

    async def _no_search(*a, **kw):
        return []

    with patch.dict(os.environ, ON), \
         patch.object(kb_retrieval, "search_kb", _kb), \
         patch("services.web_search._search_within", _no_search):
        asyncio.run(
            build_rag_prompt(
                [{"role": "user", "content": "when is the last day to drop a class this semester"}],
                "student",
            )
        )

    assert not consulted


@pytest.mark.parametrize(
    "chunks",
    [
        [],                                            # nothing found
        [_chunk(rank=0.0001)],                         # below KB_MIN_RANK
        [_chunk(document_id="only", rank=0.02)],       # one weak document
    ],
    ids=["empty", "below-min-rank", "single-weak-document"],
)
def test_a_weak_result_falls_through_to_live_search(chunks):
    searched = False

    async def _search_spy(*a, **kw):
        nonlocal searched
        searched = True
        return []

    async def _kb(*a, **kw):
        return chunks

    with patch.dict(os.environ, {**ON, "KB_MIN_RANK": "0.01", "KB_STRONG_RANK": "0.03"}), \
         patch.object(kb_retrieval, "search_kb", _kb), \
         patch("services.web_search._search_within", _search_spy):
        asyncio.run(build_rag_prompt(MESSAGES, "guest"))

    assert searched, "a weak KB result must not suppress the live path"


def test_a_broken_knowledge_base_degrades_to_todays_behaviour():
    """search_kb swallows everything; the chat path must not notice."""

    async def _boom(*a, **kw):
        raise RuntimeError("postgrest is down")

    async def _no_search(*a, **kw):
        return []

    with patch.dict(os.environ, ON), \
         patch.object(kb_retrieval, "_search", _boom), \
         patch("services.web_search._search_within", _no_search):
        # No exception escapes, and the live path is reached.
        prompt, sources = asyncio.run(build_rag_prompt(MESSAGES, "guest"))
    assert (prompt, sources) == (None, [])


def test_search_kb_never_raises_and_returns_a_list():
    async def _boom(*a, **kw):
        raise ValueError("bad shape")

    with patch.dict(os.environ, ON), patch.object(kb_retrieval, "_search", _boom):
        got = asyncio.run(
            kb_retrieval.search_kb("x", "guest", include_authenticated=False)
        )
    assert got == []


# ── Sufficiency ───────────────────────────────────────────────────────────────


def test_two_documents_are_enough_but_one_weak_one_is_not():
    with patch.dict(os.environ, {"KB_MIN_RANK": "0.01", "KB_STRONG_RANK": "0.9"}):
        assert sufficient([_chunk(document_id="a", rank=0.2), _chunk(document_id="b", rank=0.2)])
        assert not sufficient([_chunk(document_id="a", rank=0.2)])


def test_one_very_strong_document_is_enough():
    with patch.dict(os.environ, {"KB_MIN_RANK": "0.01", "KB_STRONG_RANK": "0.1"}):
        assert sufficient([_chunk(document_id="a", rank=0.5)])


def test_an_expired_document_is_never_sufficient():
    """`valid_until` in the past means the page itself has expired.

    Independent of services/freshness.py: that asks whether the question expires,
    this asks whether the document already has.
    """
    expired = _chunk(rank=0.9, valid_until="2020-01-01")
    assert not sufficient([expired])
    assert kb_items([expired]) == []


# ── Items and citations ───────────────────────────────────────────────────────


def test_one_item_per_document_not_per_chunk():
    """`[N]` has to resolve to something a person can open."""
    chunks = [
        _chunk(document_id="d1", chunk_index=0, content="first"),
        _chunk(document_id="d1", chunk_index=1, content="second"),
        _chunk(document_id="d2", chunk_index=0, content="other", url="https://x.test/"),
    ]
    items = kb_items(chunks)
    assert len(items) == 2
    assert len({i["url"] for i in items}) == 2


def test_chunks_within_a_document_read_forwards():
    """Rank order would scramble prose; chunk_index is the reading order."""
    chunks = [
        _chunk(document_id="d", chunk_index=2, content="third", rank=0.9),
        _chunk(document_id="d", chunk_index=0, content="first", rank=0.1),
        _chunk(document_id="d", chunk_index=1, content="second", rank=0.5),
    ]
    content = kb_items(chunks)[0]["content"]
    assert content.index("first") < content.index("second") < content.index("third")


def test_documents_are_ordered_by_their_best_chunk():
    chunks = [
        _chunk(document_id="weak", rank=0.1, url="https://weak.test/"),
        _chunk(document_id="strong", rank=0.8, url="https://strong.test/"),
    ]
    assert kb_items(chunks)[0]["url"] == "https://strong.test/"


def test_the_citation_header_carries_how_old_the_source_is():
    """An answer should not imply a five-week-old page is current."""
    items = kb_items([_chunk(last_verified_at="2026-09-28T12:00:00+00:00")])
    assert items[0]["verified_on"] == "2026-09-28"
    blocks, _, _ = assemble_context(items)
    assert "verified 2026-09-28" in blocks[0]


def test_a_heading_is_emitted_once_per_run_not_per_chunk():
    chunks = [
        _chunk(document_id="d", chunk_index=0, heading="Parking", content="a"),
        _chunk(document_id="d", chunk_index=1, heading="Parking", content="b"),
        _chunk(document_id="d", chunk_index=2, heading="Shuttles", content="c"),
    ]
    content = kb_items(chunks)[0]["content"]
    assert content.count("Parking") == 1
    assert content.count("Shuttles") == 1


# ── The shared budget ─────────────────────────────────────────────────────────


def test_assemble_context_caps_the_number_of_sources():
    """MAX_SOURCES used to be enforced inside rank_sources, which the KB bypasses."""
    items = [
        {"title": f"t{i}", "url": f"https://x.test/{i}", "content": "word " * 20}
        for i in range(MAX_SOURCES + 4)
    ]
    blocks, sources, _ = assemble_context(items)
    assert len(blocks) == len(sources) == MAX_SOURCES


def test_assemble_context_caps_total_characters():
    items = [
        {"title": f"t{i}", "url": f"https://x.test/{i}", "content": "x" * 9000}
        for i in range(MAX_SOURCES)
    ]
    _, _, chars = assemble_context(items)
    assert chars <= MAX_TOTAL_CHARS


def test_citation_numbers_match_source_positions():
    """The model is told to cite by number; the number is the 1-based index."""
    items = [
        {"title": "A", "url": "https://a.test/", "content": "alpha"},
        {"title": "B", "url": "https://b.test/", "content": "beta"},
    ]
    blocks, sources, _ = assemble_context(items)
    for i, block in enumerate(blocks, start=1):
        assert block.startswith(f"[{i}] ")
        assert sources[i - 1]["url"] in block


def test_a_truncated_source_is_cut_at_a_word_boundary():
    items = [{"title": "T", "url": "https://t.test/", "content": "alpha " * 5000}]
    blocks, _, _ = assemble_context(items)
    assert blocks[0].endswith("...")
    assert "alph..." not in blocks[0]


# ── The full-text query, and why it is OR-joined ──────────────────────────────


def test_the_fts_query_is_or_joined():
    """`websearch_to_tsquery` ANDs terms, which matched nothing for any question.

    Measured against the live corpus on 2026-10-01: "How do I order an official
    transcript from SJSU as an alumnus?" returned **zero** keyword hits, because no
    chunk contains all of those words. Every bench question behaved the same way,
    so the keyword arm was dead and "hybrid" retrieval was vector-only.
    """
    out = kb_retrieval.fts_query("How do I order an official transcript?")
    assert " or " in out
    assert "transcript" in out
    assert out.islower()


def test_the_fts_query_cannot_be_turned_into_an_operator():
    """websearch_to_tsquery has operators, and a raw question can trip them.

    A leading `-` negates and a double quote starts a phrase, so "add-drop
    deadline" passed through raw becomes `add & !drop & deadline` -- the opposite
    of what was asked. Only word characters survive tokenisation.
    """
    assert kb_retrieval.fts_query("add-drop deadline") == "add or drop or deadline"
    assert '"' not in kb_retrieval.fts_query('parking "north garage"')
    assert "-" not in kb_retrieval.fts_query("add-drop")


def test_the_words_or_and_not_are_dropped_as_terms():
    """Otherwise a question containing "or" restructures the query."""
    out = kb_retrieval.fts_query("Is it A or B and not C?")
    assert "and" not in out.split(" or ")
    assert "not" not in out.split(" or ")


def test_an_empty_or_punctuation_only_question_yields_an_empty_query():
    """The SQL guards this with numnode(q) = 0; it must not become ' or '."""
    assert kb_retrieval.fts_query("") == ""
    assert kb_retrieval.fts_query("?!  ...") == ""


def test_duplicate_words_are_not_repeated():
    assert kb_retrieval.fts_query("parking parking parking") == "parking"


# ── Sufficiency, after calibration ────────────────────────────────────────────


def test_documents_are_counted_only_above_the_threshold():
    """The bug that made this gate inert.

    Vector search always returns its k nearest neighbours, so counting documents
    across every returned chunk meant `len(documents) >= 2` was satisfied by any
    query at all. Measured before the fix: "quantum chromodynamics lattice gauge
    theory" came back as 12 chunks across 11 documents and was judged sufficient.
    """
    with patch.dict(os.environ, {"KB_MIN_RANK": "0.02", "KB_STRONG_RANK": "0.03"}):
        # Eleven documents, none of which clears the threshold.
        noise = [
            _chunk(document_id=f"d{i}", rank=0.0164 - i * 0.0001) for i in range(11)
        ]
        assert not sufficient(noise), "near neighbours must not look like an answer"

        # Two documents that do clear it.
        real = [_chunk(document_id="a", rank=0.0325), _chunk(document_id="b", rank=0.0210)]
        assert sufficient(real)


def test_a_single_document_needs_both_arms_to_agree():
    """0.0164 is one arm; ~0.032 is both. Only the latter answers alone."""
    with patch.dict(os.environ, {"KB_MIN_RANK": "0.02", "KB_STRONG_RANK": "0.03"}):
        assert not sufficient([_chunk(document_id="a", rank=0.0164)])
        assert sufficient([_chunk(document_id="a", rank=0.0328)])


def test_the_calibrated_defaults_sit_between_the_two_modes():
    """A regression here silently changes what the chat path will answer.

    0.0164 = 1/61, one arm. ~0.032 = 2/61, both arms. KB_MIN_RANK has to fall
    between them: below 0.0164 it admits every near neighbour, above ~0.033 it
    rejects everything.
    """
    with patch.dict(os.environ, {}, clear=False):
        for name in ("KB_MIN_RANK", "KB_STRONG_RANK"):
            os.environ.pop(name, None)
        assert 0.0164 < kb_retrieval.min_rank() < 0.0328
        assert kb_retrieval.strong_rank() >= kb_retrieval.min_rank()
