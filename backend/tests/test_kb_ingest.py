"""
The ingestion orchestrator, with the network and the database stubbed out.

Run from backend/ with:
    python -m pytest tests/test_kb_ingest.py

**Why this file exists.** Every write in `kb/store.py` is unexercised against a
real schema, because `20260930000100_kb_hybrid_retrieval.sql` is not on the
project yet. These tests cannot fix that -- a stub cannot tell you PostgREST
rejects your payload -- but they can check the thing a live run would be a poor
way to debug: **which store call each fetch outcome leads to.** A 304 must not
re-embed. An unchanged hash must not rewrite chunks. One failing page must not
end the run. Those are decisions in `_ingest_url`, not in the database, and
getting them wrong costs a full re-crawl to notice.

`conditional_get` and the robots lookup are replaced wholesale; what is under
test is the orchestration, not the fetcher.
"""
import asyncio
from collections import Counter
from unittest.mock import patch

import pytest

from kb import ingest, store
from kb.chunker import Chunk
from kb.fetcher import FetchResult, Outcome
from kb.robots import HostRules

HTML = (
    "<html><head><title>Parking | San José State University</title></head>"
    "<body><main><h2>Visitor parking</h2><p>" + ("Park in the North Garage. " * 40) +
    "</p></main></body></html>"
)
SOURCE = {
    "id": "src-1",
    "url": "https://www.sjsu.edu/parking/",
    "collection": "visiting",
    "audience_tags": ["guest"],
}


class FakeStore:
    """Records every call, so a test can assert on what ingestion decided to do."""

    def __init__(self, existing=None):
        self.existing = existing
        self.calls = Counter()
        self.upserts = []
        self.chunk_writes = []
        self.touches = []

    async def get_document(self, client, url):
        self.calls["get_document"] += 1
        return self.existing

    async def upsert_document(self, client, row):
        self.calls["upsert_document"] += 1
        self.upserts.append(row)
        return "doc-1"

    async def touch_document(self, client, document_id, **fields):
        self.calls["touch_document"] += 1
        self.touches.append({"id": document_id, **fields})

    async def replace_chunks(self, client, document_id, chunks):
        self.calls["replace_chunks"] += 1
        self.chunk_writes.append(chunks)
        return len(chunks)


def _run(result, *, existing=None, force=False, embeddings=None, source=None):
    """Drive _ingest_url once against a stubbed fetch and store."""
    fake = FakeStore(existing)
    stats = ingest.Stats()

    async def _fetch(client, url, **kw):
        _fetch.kwargs = kw
        return result

    async def _rules(url):
        return HostRules(host="www.sjsu.edu", reachable=True, parser=None, crawl_delay=0.0)

    async def _embed(client, texts):
        if embeddings == "fail":
            return [None for _ in texts]
        return [[0.1] * 768 for _ in texts]

    class _Robots:
        for_url = staticmethod(_rules)

    with patch.object(ingest, "conditional_get", _fetch), \
         patch.object(ingest.store, "get_document", fake.get_document), \
         patch.object(ingest.store, "upsert_document", fake.upsert_document), \
         patch.object(ingest.store, "touch_document", fake.touch_document), \
         patch.object(ingest.store, "replace_chunks", fake.replace_chunks), \
         patch.object(ingest.embed, "embed_documents", _embed), \
         patch.dict("os.environ", {"KB_CRAWL_DELAY": "0"}):
        asyncio.run(
            ingest._ingest_url(
                None,
                (source or SOURCE)["url"],
                source or SOURCE,
                robots=_Robots(),
                pacer=ingest.HostPacer(),
                gate=asyncio.Semaphore(2),
                embed_gate=asyncio.Semaphore(1),
                stats=stats,
                force=force,
                dry_run=False,
            )
        )
    return fake, stats, getattr(_fetch, "kwargs", {})


def _ok(**kw):
    base = dict(
        outcome=Outcome.OK,
        url=SOURCE["url"],
        text="Park in the North Garage. " * 40,
        html=HTML,
        etag='W/"abc"',
        last_modified="Wed, 01 Oct 2026 00:00:00 GMT",
        status=200,
        content_type="text/html",
    )
    base.update(kw)
    return FetchResult(**base)


# ── 304: the cheap path, and most of a weekly run ─────────────────────────────


def test_a_304_writes_nothing_but_the_timestamp():
    """No parse, no embedding, no chunk write. This is the whole point of etags."""
    existing = {"id": "doc-1", "etag": 'W/"abc"', "content_hash": "x", "version": 3}
    fake, stats, _ = _run(FetchResult(Outcome.NOT_MODIFIED, SOURCE["url"]), existing=existing)

    assert stats.pages_unchanged == 1
    assert stats.pages_fetched == 0
    assert fake.calls["touch_document"] == 1
    assert fake.calls["upsert_document"] == 0
    assert fake.calls["replace_chunks"] == 0


def test_a_304_on_a_document_we_have_never_seen_does_not_crash():
    """Defensive: a 304 implies we sent a validator, but the row could be gone."""
    fake, stats, _ = _run(FetchResult(Outcome.NOT_MODIFIED, SOURCE["url"]), existing=None)
    assert stats.pages_unchanged == 1
    assert fake.calls["touch_document"] == 0


def test_the_stored_validators_are_sent_back():
    existing = {"id": "doc-1", "etag": 'W/"zzz"', "last_modified": "LM", "content_hash": "x"}
    _, _, kwargs = _run(_ok(), existing=existing)
    assert kwargs["etag"] == 'W/"zzz"'
    assert kwargs["last_modified"] == "LM"


def test_force_withholds_the_validators():
    """Otherwise a bad extraction can never be corrected: the server keeps
    answering 304 and the stored text is never revisited."""
    existing = {"id": "doc-1", "etag": 'W/"zzz"', "last_modified": "LM", "content_hash": "x"}
    _, _, kwargs = _run(_ok(), existing=existing, force=True)
    assert kwargs["etag"] is None
    assert kwargs["last_modified"] is None


# ── 200 with unchanged text ───────────────────────────────────────────────────


def test_an_unchanged_hash_refreshes_validators_without_re_embedding():
    # .strip() matters: _ingest_url hashes result.text.strip(), so a fixture that
    # hashes the unstripped text never reaches the unchanged branch at all.
    text = ("Park in the North Garage. " * 40).strip()
    existing = {
        "id": "doc-1",
        "content_hash": store.content_hash(text),
        "etag": "old",
        "version": 2,
    }
    fake, stats, _ = _run(_ok(), existing=existing)

    assert fake.calls["replace_chunks"] == 0, "re-embedding identical text is pure waste"
    assert fake.calls["upsert_document"] == 0
    assert fake.calls["touch_document"] == 1
    assert stats.pages_unchanged == 1
    # It was counted as fetched before the hash was compared, then taken back.
    assert stats.pages_fetched == 0
    assert fake.touches[0]["etag"] == 'W/"abc"'


def test_force_rewrites_even_when_the_hash_matches():
    text = ("Park in the North Garage. " * 40).strip()
    existing = {"id": "doc-1", "content_hash": store.content_hash(text), "version": 2}
    fake, stats, _ = _run(_ok(), existing=existing, force=True)
    assert fake.calls["replace_chunks"] == 1
    assert stats.documents_written == 1


# ── 200 with changed text ─────────────────────────────────────────────────────


def test_changed_text_upserts_then_replaces_chunks():
    existing = {"id": "doc-1", "content_hash": "stale", "version": 4}
    fake, stats, _ = _run(_ok(), existing=existing)

    assert fake.calls["upsert_document"] == 1
    assert fake.calls["replace_chunks"] == 1
    assert stats.documents_written == 1
    assert stats.chunks_written > 0


def test_the_version_increments_from_the_stored_row():
    existing = {"id": "doc-1", "content_hash": "stale", "version": 4}
    fake, _, _ = _run(_ok(), existing=existing)
    assert fake.upserts[0]["version"] == 5


def test_a_brand_new_url_starts_at_version_1():
    fake, stats, _ = _run(_ok(), existing=None)
    assert fake.upserts[0]["version"] == 1
    assert stats.documents_written == 1


def test_the_document_row_carries_the_source_metadata():
    """collection and audience_tags come from kb_sources, not from the page."""
    fake, _, _ = _run(_ok())
    row = fake.upserts[0]
    assert row["collection"] == "visiting"
    assert row["audience_tags"] == ["guest"]
    assert row["visibility"] == "public"
    assert row["url"] == SOURCE["url"]
    assert row["content_hash"]


def test_visibility_defaults_to_public_when_the_source_omits_it():
    fake, _, _ = _run(_ok(), source={**SOURCE, "visibility": None})
    assert fake.upserts[0]["visibility"] == "public"


def test_a_restricted_source_is_not_silently_published():
    fake, _, _ = _run(_ok(), source={**SOURCE, "visibility": "authenticated"})
    assert fake.upserts[0]["visibility"] == "authenticated"


# ── Chunks and embeddings ─────────────────────────────────────────────────────


def test_chunk_rows_carry_index_content_and_token_count():
    fake, _, _ = _run(_ok())
    rows = fake.chunk_writes[0]
    assert [r["chunk_index"] for r in rows] == list(range(len(rows)))
    for row in rows:
        assert row["content"]
        assert row["token_count"] > 0
        assert row["embedding"] is not None


def test_a_failed_embedding_still_stores_the_chunk():
    """The keyword arm works without a vector; losing the row would be worse.

    A NULL embedding simply drops out of the vector half of the fusion, so the
    page stays findable by `tsv`. Dropping the chunk because an embedding call
    failed would make an outage permanent until the next --force.
    """
    fake, stats, _ = _run(_ok(), embeddings="fail")
    rows = fake.chunk_writes[0]
    assert rows, "chunks must still be written"
    assert all(r["embedding"] is None for r in rows)
    assert stats.chunks_written == len(rows)
    assert stats.embeddings_missing == len(rows)


def test_the_title_comes_from_the_markup_with_the_university_suffix_stripped():
    fake, _, _ = _run(_ok())
    assert fake.upserts[0]["title"] == "Parking"


def test_a_page_with_no_title_falls_back_to_its_url():
    """`documents.title` is not null, and it is what the citation renders."""
    fake, _, _ = _run(_ok(html="<html><body><main><p>" + "x " * 400 + "</p></main></body></html>"))
    assert fake.upserts[0]["title"] == SOURCE["url"]


# ── Failures ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "outcome",
    [Outcome.ROBOTS_DENIED, Outcome.HTTP_4XX, Outcome.HTTP_429, Outcome.HTTP_5XX,
     Outcome.TIMEOUT, Outcome.NON_HTML, Outcome.THIN, Outcome.BLOCKED_HOST],
)
def test_every_failure_is_counted_by_name_and_writes_nothing(outcome):
    """`skip_reasons` is the coverage report; an uncounted skip is invisible."""
    fake, stats, _ = _run(FetchResult(outcome, SOURCE["url"]))
    assert stats.pages_skipped == 1
    assert stats.skip_reasons[outcome.value] == 1
    assert fake.calls["upsert_document"] == 0
    assert fake.calls["replace_chunks"] == 0


def test_a_fetch_that_extracts_nothing_chunkable_is_counted_as_thin():
    """200 with no usable text must not pass silently as a success."""
    fake, stats, _ = _run(_ok(text="", html=""))
    assert stats.pages_skipped == 1
    assert stats.skip_reasons["thin"] == 1
    assert fake.calls["upsert_document"] == 0


# ── Run-level accounting ──────────────────────────────────────────────────────


def test_one_failing_page_does_not_end_the_run():
    """A partially built corpus is worth more than no corpus."""
    async def _boom(client, url, source, **kw):
        if url.endswith("/bad/"):
            raise RuntimeError("kaboom")
        kw["stats"].pages_fetched += 1

    sources = [
        {"id": "a", "url": "https://www.sjsu.edu/good/", "collection": "x"},
        {"id": "b", "url": "https://www.sjsu.edu/bad/", "collection": "x"},
        {"id": "c", "url": "https://www.sjsu.edu/good2/", "collection": "x"},
    ]

    async def _list_sources(client, urls=None):
        return sources

    async def _noop(*a, **kw):
        return None

    with patch.object(ingest, "_ingest_url", _boom), \
         patch.object(ingest.store, "configured", lambda: True), \
         patch.object(ingest.store, "list_sources", _list_sources), \
         patch.object(ingest.store, "start_run", _noop), \
         patch.object(ingest.store, "finish_run", _noop), \
         patch.object(ingest.store, "mark_source_fetched", _noop):
        record = asyncio.run(ingest.run_ingestion())

    assert record["pages_fetched"] == 2
    assert record["skip_reasons"].get("exception") == 1
    assert record["status"] == "partial"
    assert "kaboom" in (record["error_message"] or "")


def test_the_run_record_status_reflects_what_happened():
    assert ingest.Stats(pages_fetched=5).as_record()["status"] == "success"
    assert ingest.Stats(pages_fetched=5, pages_skipped=1).as_record()["status"] == "partial"
    assert ingest.Stats(errors=["boom"]).as_record()["status"] == "failed"
    # Something got through, so it is partial rather than a total loss.
    assert ingest.Stats(pages_fetched=1, errors=["boom"]).as_record()["status"] == "partial"


def test_a_run_with_no_credentials_fails_without_touching_the_network():
    with patch.object(ingest.store, "configured", lambda: False):
        record = asyncio.run(ingest.run_ingestion())
    assert record["status"] == "failed"
    assert "SUPABASE" in record["error_message"]


def test_the_page_budget_is_enforced():
    seen = []

    async def _count(client, url, source, **kw):
        seen.append(url)

    sources = [
        {"id": str(i), "url": f"https://www.sjsu.edu/p{i}/", "collection": "x"}
        for i in range(10)
    ]

    async def _list_sources(client, urls=None):
        return sources

    async def _noop(*a, **kw):
        return None

    with patch.object(ingest, "_ingest_url", _count), \
         patch.object(ingest.store, "configured", lambda: True), \
         patch.object(ingest.store, "list_sources", _list_sources), \
         patch.object(ingest.store, "start_run", _noop), \
         patch.object(ingest.store, "finish_run", _noop), \
         patch.object(ingest.store, "mark_source_fetched", _noop):
        asyncio.run(ingest.run_ingestion(max_pages=4))

    assert len(seen) == 4


# ── Politeness ────────────────────────────────────────────────────────────────


def test_the_pacer_waits_between_requests_to_one_host():
    pacer = ingest.HostPacer()

    async def drive():
        import time as _t

        started = _t.monotonic()
        await pacer.wait("h", 0.0)      # first request: no wait
        await pacer.wait("h", 0.25)     # second: must wait
        return _t.monotonic() - started

    assert asyncio.run(drive()) >= 0.2


def test_two_hosts_do_not_wait_on_each_other():
    """One slow host must not pace the rest -- catalog.sjsu.edu is 120s."""
    pacer = ingest.HostPacer()

    async def drive():
        import time as _t

        await pacer.wait("slow", 0.0)
        started = _t.monotonic()
        await pacer.wait("fast", 0.0)
        return _t.monotonic() - started

    assert asyncio.run(drive()) < 0.1


def test_robots_crawl_delay_wins_when_it_is_longer_than_the_default():
    """catalog.sjsu.edu asks for 120s; the configured default is 1s."""
    with patch.dict("os.environ", {"KB_CRAWL_DELAY": "1.0"}):
        assert max(120.0, ingest.crawl_delay()) == 120.0
        assert max(0.0, ingest.crawl_delay()) == 1.0


def test_a_bad_crawl_delay_setting_falls_back_rather_than_crashing():
    with patch.dict("os.environ", {"KB_CRAWL_DELAY": "not-a-number"}):
        assert ingest.crawl_delay() == ingest.DEFAULT_CRAWL_DELAY


# ── kb_sources.last_status ────────────────────────────────────────────────────


def test_last_status_records_the_real_outcome_not_ok():
    """`kb_sources.last_status` is a diagnostic, so it has to be honest.

    It used to be written as an unconditional "ok" for every source that did not
    raise, so a seed whose page was robots-denied or 404 read as healthy. That is
    the table someone checks to find out why a question has no answer, and a
    cheerful "ok" sends them looking at retrieval instead of at the crawl.
    """
    recorded = {}

    async def _mark(client, source_id, status):
        recorded[source_id] = status

    outcomes = {
        "https://www.sjsu.edu/a/": Outcome.OK,
        "https://www.sjsu.edu/b/": Outcome.ROBOTS_DENIED,
        "https://www.sjsu.edu/c/": Outcome.HTTP_4XX,
    }
    sources = [
        {"id": url[-2], "url": url, "collection": "x"} for url in outcomes
    ]

    async def _ingest(client, url, source, **kw):
        outcome = outcomes[url]
        if outcome is not Outcome.OK:
            kw["stats"].pages_skipped += 1
            kw["stats"].skip_reasons[outcome.value] += 1
        return outcome.value

    async def _list_sources(client, urls=None):
        return sources

    async def _noop(*a, **kw):
        return None

    with patch.object(ingest, "_ingest_url", _ingest), \
         patch.object(ingest.store, "configured", lambda: True), \
         patch.object(ingest.store, "list_sources", _list_sources), \
         patch.object(ingest.store, "start_run", _noop), \
         patch.object(ingest.store, "finish_run", _noop), \
         patch.object(ingest.store, "mark_source_fetched", _mark):
        asyncio.run(ingest.run_ingestion())

    assert recorded == {"a": "ok", "b": "robots_denied", "c": "http_4xx"}
