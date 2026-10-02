"""
Who may trigger a crawl.

Run from backend/ with:
    python -m pytest tests/test_admin_kb.py

`/api/admin/kb/ingest` drives a write pipeline with the Supabase service role and
sends traffic to a third party's servers under our own User-Agent. Both halves of
that make the gate the point of the endpoint, so the gate is what is tested here
rather than the crawl.

A guest is the interesting case. Guest tokens are minted by this backend on
request, so if the capability check accepted any authenticated principal, anyone
who can load the page could start a crawl.
"""
import contextlib
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import auth
import main
from .conftest import AUTH_HEADERS, GUEST_HEADERS

client = TestClient(main.app)

ENDPOINTS = [("post", "/api/admin/kb/ingest"), ("get", "/api/admin/kb/status")]


@contextlib.contextmanager
def _grants(stub):
    """Replace the grant lookup, and clear the 60s cache on both sides.

    `get_capabilities` memoises per user for a minute, so without this a verdict
    from one test decides the next one -- and the order would matter.
    """
    auth._grant_cache.clear()
    try:
        with patch("auth._fetch_grants", stub):
            yield
    finally:
        auth._grant_cache.clear()


def _call(method: str, path: str, **kw):
    return getattr(client, method)(path, **kw)


@pytest.mark.parametrize("method,path", ENDPOINTS)
def test_no_token_is_refused(method, path):
    assert _call(method, path).status_code == 401


@pytest.mark.parametrize("method,path", ENDPOINTS)
def test_a_guest_is_refused(method, path):
    """A guest is authenticated and can never hold a grant.

    Grants are seeded by hand against an account, and a guest has no account, so
    403 is the only correct answer -- not 401, which would imply the credential
    was the problem.
    """
    res = _call(method, path, headers=GUEST_HEADERS)
    assert res.status_code == 403, res.text


@pytest.mark.parametrize("method,path", ENDPOINTS)
def test_a_signed_in_user_without_the_grant_is_refused(method, path):
    """Being signed in is not the bar; holding `run_ingestion` is."""
    async def _no_grants(*a, **kw):
        return frozenset()

    with _grants(_no_grants):
        res = _call(method, path, headers=AUTH_HEADERS)
    assert res.status_code == 403, res.text
    assert "run_ingestion" in res.json()["detail"]


@pytest.mark.parametrize("method,path", ENDPOINTS)
def test_an_unreadable_grants_table_fails_closed(method, path):
    """503, not 200, and not 403 either.

    Patched at `_fetch_grants` rather than `get_capabilities` on purpose: the
    conversion of a database outage into a 503 lives in `_fetch_grants`, so
    stubbing the layer above it would test the stub instead of the behaviour.
    503 rather than 403 so an outage stays distinguishable from a missing grant.
    """
    async def _boom(*a, **kw):
        raise auth._unavailable("admin_grants does not exist")

    with _grants(_boom):
        res = _call(method, path, headers=AUTH_HEADERS)
    assert res.status_code == 503, res.text


@pytest.mark.parametrize("method,path", ENDPOINTS)
def test_a_granted_user_gets_past_the_gate(method, path):
    """The gate must also open, or the tests above would pass on a broken route.

    The service key is removed so the handler answers 503 from its own
    configuration check -- past the gate, which is what this asserts. Only the
    key, not SUPABASE_URL: that one is also the token issuer, so blanking it
    fails the request at verification and never reaches the gate at all.
    """
    async def _granted(*a, **kw):
        return frozenset({"run_ingestion"})

    with _grants(_granted), patch.dict(os.environ, {"SUPABASE_SERVICE_KEY": ""}):
        res = _call(method, path, headers=AUTH_HEADERS)
    assert res.status_code == 503
    assert "configured" in res.json()["detail"].lower()


def test_the_capability_name_matches_the_seeded_grant():
    """The grant is seeded by hand, so a typo here is a silent 403 forever."""
    from routers import admin_kb

    source = open(admin_kb.__file__, encoding="utf-8").read()
    assert 'require_capability("run_ingestion")' in source


def test_ingest_is_queue_by_default():
    """`wait=true` has to be asked for.

    catalog.sjsu.edu at crawl-delay 120 is a ~10-hour job; an endpoint that runs
    inline by default would make that a hung request rather than a queued one.
    """
    from routers.admin_kb import IngestRequest

    assert IngestRequest().wait is False
    assert IngestRequest().force is False
