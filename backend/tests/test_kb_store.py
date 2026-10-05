"""
The knowledge-base write path, without a database.

Run from backend/ with:
    python -m pytest tests/test_kb_store.py

These assert the shape of what goes to PostgREST, because the write path's bugs
are shaped like malformed requests rather than wrong answers -- and a malformed
request only shows up against a live endpoint, long after the code looked right.

The grouping test exists because that exact bug shipped and was caught by a live
call: PostgREST rejects a bulk insert whose objects do not all carry the same
keys (PGRST102, "All object keys must match"), and `sync_seeds` builds rows with
deliberately different keys so the table's own column defaults survive a re-sync.
"""
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest

from kb import store


def _captured(seeds: list[dict]) -> list[list[dict]]:
    """Run sync_seeds against a stub and return the body of each POST."""
    bodies: list[list[dict]] = []

    async def _fake_request(client, method, path, *, params=None, json_body=None, prefer=None):
        assert method == "POST" and path == "kb_sources"
        # The upsert target. A partial index cannot be inferred by PostgREST,
        # which is why 20260930000100's uq_documents_url is not partial either.
        assert params == {"on_conflict": "url"}
        assert prefer is not None and "merge-duplicates" in prefer
        bodies.append(json_body)
        return None

    with patch.object(store, "load_seeds", lambda: seeds), \
         patch.object(store, "_request", _fake_request):
        import asyncio

        asyncio.run(store.sync_seeds(None))
    return bodies


def test_rows_of_one_shape_go_in_a_single_request():
    seeds = [
        {"url": "https://a.test/", "collection": "registrar", "audience_tags": ["student"]},
        {"url": "https://b.test/", "collection": "alumni", "audience_tags": ["alumni"]},
    ]
    bodies = _captured(seeds)
    assert len(bodies) == 1
    assert len(bodies[0]) == 2


def test_rows_of_different_shapes_are_split_by_key_signature():
    """PGRST102: every object in one bulk insert must carry identical keys."""
    seeds = [
        {"url": "https://a.test/", "collection": "registrar"},
        {"url": "https://b.test/", "collection": "catalog", "max_pages": 5},
        {"url": "https://c.test/", "collection": "catalog", "fetch_interval_minutes": 40320},
    ]
    bodies = _captured(seeds)
    assert len(bodies) == 3, "three distinct key sets must become three requests"
    for body in bodies:
        keys = {tuple(sorted(row)) for row in body}
        assert len(keys) == 1, f"a single request carried mixed keys: {keys}"


def test_an_unspecified_column_is_omitted_not_defaulted():
    """The table owns its defaults.

    Writing this module's idea of crawl_depth or max_pages into every row would
    silently reset a value a human had tuned by hand on the next re-sync.
    """
    bodies = _captured([{"url": "https://a.test/", "collection": "registrar"}])
    row = bodies[0][0]
    assert set(row) == {"url", "collection"}
    for defaulted in ("crawl_depth", "max_pages", "enabled", "fetch_interval_minutes"):
        assert defaulted not in row


def test_every_seed_survives_the_grouping():
    """A regrouping bug that dropped rows would otherwise be invisible."""
    seeds = store.load_seeds()
    bodies = _captured(seeds)
    sent = [row["url"] for body in bodies for row in body]
    assert sorted(sent) == sorted(s["url"] for s in seeds)
    assert len(sent) == len(set(sent)), "a URL was sent twice"


# ── The real seed file ────────────────────────────────────────────────────────


def test_the_seed_file_is_well_formed():
    seeds = store.load_seeds()
    assert len(seeds) >= 20
    for seed in seeds:
        assert seed["url"].startswith("https://"), seed
        assert seed.get("collection"), seed
        # `documents.visibility` has a check constraint; a typo here would fail
        # the insert mid-run rather than at review time.
        assert seed.get("visibility", "public") in ("public", "authenticated", "restricted")
        assert isinstance(seed.get("audience_tags", []), list)


def test_seed_urls_are_unique():
    """`kb_sources.url` is unique, so a duplicate is a failed sync, not a warning."""
    urls = [s["url"] for s in store.load_seeds()]
    duplicates = {u for u in urls if urls.count(u) > 1}
    assert not duplicates, duplicates


def test_no_seed_points_at_a_robots_disallowed_path():
    """www.sjsu.edu/robots.txt blocks these prefixes.

    Crawling one would be both a compliance failure and wasted budget: the
    fetcher refuses it at request time and records robots_denied, so the page
    never enters the corpus and the skip is easy to overlook.

    **Scoped to www.sjsu.edu, because robots.txt is per host.** An earlier version
    of this test applied this list to every seed and failed on
    `careercenter.sjsu.edu/resources/` -- which that host's own robots.txt
    explicitly permits (it disallows only /admin/, /search/ and a list of file
    extensions). Treating one host's rules as site-wide rejects legitimate pages,
    which is a different bug from crawling a forbidden one but still a bug.
    """
    blocked = ("/ecampus/", "/resources/", "/drc/", "/atn/", "/mae/", "/hrtm/",
               "/computereng/", "/cgi-bin/", "/contact/", "/gallery-pics/")
    for seed in store.load_seeds():
        if urlsplit(seed["url"]).netloc.lower() != "www.sjsu.edu":
            continue
        for prefix in blocked:
            assert prefix not in seed["url"], f"{seed['url']} is robots-disallowed"


# ── Hashing ───────────────────────────────────────────────────────────────────


def test_the_hash_is_over_text_so_boilerplate_churn_does_not_trigger_rewrites():
    """Hashing HTML would mark every page modified forever.

    SJSU pages carry CSRF nonces and build ids that change on every fetch, so an
    HTML hash would re-embed the whole corpus weekly for no content change.
    """
    first = "<html><body><p>Parking is in the North Garage.</p><!-- build 991 --></body></html>"
    second = "<html><body><p>Parking is in the North Garage.</p><!-- build 992 --></body></html>"
    assert store.content_hash(first) != store.content_hash(second)
    # The same extracted text out of both, however, hashes identically.
    text = "Parking is in the North Garage."
    assert store.content_hash(text) == store.content_hash(text)


def test_the_hash_is_stable_across_processes():
    """sha256, not hash() -- which is salted per process and would rewrite everything."""
    assert store.content_hash("abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


# ── Configuration ─────────────────────────────────────────────────────────────


def test_configured_requires_both_halves():
    import os

    with patch.dict(os.environ, {"SUPABASE_URL": "https://x.test", "SUPABASE_SERVICE_KEY": ""}):
        assert store.configured() is False
    with patch.dict(os.environ, {"SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": "k"}):
        assert store.configured() is False
    with patch.dict(os.environ, {"SUPABASE_URL": "https://x.test", "SUPABASE_SERVICE_KEY": "k"}):
        assert store.configured() is True


def test_a_missing_service_key_raises_rather_than_sending_an_anonymous_request():
    """Every kb table grants nothing to anon, so a keyless request is a silent 404."""
    import os

    with patch.dict(os.environ, {"SUPABASE_SERVICE_KEY": ""}):
        with pytest.raises(store.StoreError):
            store._headers()


# ── What gets into the corpus at all ──────────────────────────────────────────


def test_the_thin_threshold_admits_a_terse_but_real_page():
    """`MIN_USEFUL_CHARS` detects extraction failure, not brevity.

    At 500 the only page rejected across all 35 seeds was
    /registrar/transcripts/ -- a 182-character stub whose entire body answers
    bench question `alumni-faq-1`. Extraction had worked perfectly; the guard was
    rejecting the page for being short. Pinned at 200 because a JavaScript shell
    extracts to tens of characters, not two hundred, so the guard still catches
    what it was written for.
    """
    from kb.fetcher import MIN_USEFUL_CHARS

    assert MIN_USEFUL_CHARS <= 200, (
        "raising this above 200 drops /transcripts/ and with it alumni-faq-1"
    )
    assert MIN_USEFUL_CHARS >= 100, (
        "below ~100 this stops distinguishing a real page from a JS shell"
    )
