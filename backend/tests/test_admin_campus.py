"""
Operator routes for the campus refresh (R4).

Run from backend/ with:
    python -m pytest tests/test_admin_campus.py

The spawn is always monkeypatched: no test starts a real process or touches a
database.
"""
import contextlib
import os
import sys
from unittest.mock import patch

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

import auth
import main
from routers import admin_campus
from services import registration
from .conftest import AUTH_HEADERS, GUEST_HEADERS, SUPABASE_URL

client = TestClient(main.app)

ENDPOINTS = [("post", "/api/admin/campus/refresh"), ("get", "/api/admin/campus/status")]
REST = f"{SUPABASE_URL}/rest/v1"


@contextlib.contextmanager
def _grants(stub):
    auth._grant_cache.clear()
    try:
        with patch("auth._fetch_grants", stub):
            yield
    finally:
        auth._grant_cache.clear()


async def _granted(*a, **kw):
    return frozenset({"run_ingestion"})


class FakeChild:
    def __init__(self, pid=4242):
        self.pid = pid
        self.code = None

    def poll(self):
        return self.code


@pytest.fixture(autouse=True)
def _state():
    admin_campus._child = None
    admin_campus._spawning = False
    with patch.dict(os.environ, {"SUPABASE_SERVICE_KEY": "service-key-for-tests"}):
        yield
    admin_campus._child = None
    admin_campus._spawning = False


@pytest.fixture
def spawned():
    calls = []

    def fake(argv, cwd):
        calls.append((argv, cwd))
        return FakeChild()

    with patch.object(admin_campus, "_spawn", fake):
        yield calls


def _call(method, path, **kw):
    return getattr(client, method)(path, **kw)


# -- the gate ----------------------------------------------------------------------


@pytest.mark.parametrize("method,path", ENDPOINTS)
def test_no_token_is_401(method, path):
    assert _call(method, path).status_code == 401


@pytest.mark.parametrize("method,path", ENDPOINTS)
def test_a_guest_is_403_and_nothing_is_spawned(method, path, spawned):
    assert _call(method, path, headers=GUEST_HEADERS).status_code == 403
    assert spawned == []


@pytest.mark.parametrize("method,path", ENDPOINTS)
def test_a_user_without_the_grant_is_403(method, path, spawned):
    async def none(*a, **kw):
        return frozenset()

    with _grants(none):
        res = _call(method, path, headers=AUTH_HEADERS)
    assert res.status_code == 403
    assert "run_ingestion" in res.json()["detail"]
    assert spawned == []


@pytest.mark.parametrize("method,path", ENDPOINTS)
def test_unreadable_grants_fail_closed_with_503(method, path, spawned):
    async def boom(*a, **kw):
        raise auth._unavailable("admin_grants does not exist")

    with _grants(boom):
        assert _call(method, path, headers=AUTH_HEADERS).status_code == 503
    assert spawned == []


def test_the_capability_name_matches_the_seeded_grant():
    source = open(admin_campus.__file__, encoding="utf-8").read()
    assert 'require_capability("run_ingestion")' in source


# -- refresh -----------------------------------------------------------------------


def test_refresh_spawns_the_fixed_argv_and_returns_202(spawned):
    with _grants(_granted):
        res = client.post("/api/admin/campus/refresh", headers=AUTH_HEADERS)
    assert res.status_code == 202
    assert res.json()["pid"] == 4242 and res.json()["message"]
    (argv, cwd), = spawned
    assert argv == [sys.executable, "-m", "campus.registration.refresh"]
    assert os.path.basename(cwd) == "backend"
    assert os.path.isfile(os.path.join(cwd, "campus", "registration", "refresh.py"))


def test_refresh_passes_validated_source_and_term(spawned):
    with _grants(_granted):
        res = client.post("/api/admin/campus/refresh", headers=AUTH_HEADERS,
                          json={"source": "registrar", "term": "fall-2026"})
    assert res.status_code == 202
    assert spawned[0][0] == [sys.executable, "-m", "campus.registration.refresh",
                             "--source", "registrar", "--term", "fall-2026"]


@pytest.mark.parametrize("body", [
    {"source": "schedule"},               # not an enum member
    {"source": "registrar; rm -rf /"},
    {"term": "fall-2026 --dry-run"},
    {"term": "../../etc"},
    {"term": "autumn-2026"},
    {"term": "x" * 100},
])
def test_refresh_rejects_unvalidated_input_before_spawning(body, spawned):
    with _grants(_granted):
        res = client.post("/api/admin/campus/refresh", headers=AUTH_HEADERS, json=body)
    assert res.status_code == 422
    assert spawned == []


def test_a_second_refresh_while_running_is_409(spawned):
    with _grants(_granted):
        assert client.post("/api/admin/campus/refresh", headers=AUTH_HEADERS).status_code == 202
        assert client.post("/api/admin/campus/refresh", headers=AUTH_HEADERS).status_code == 409
        assert len(spawned) == 1
        # Once the child has exited, a new one may start.
        admin_campus._child.code = 0
        assert client.post("/api/admin/campus/refresh", headers=AUTH_HEADERS).status_code == 202
    assert len(spawned) == 2


def test_a_spawn_failure_is_503_and_does_not_wedge_the_guard():
    def broken(argv, cwd):
        raise OSError("no such file")

    with _grants(_granted), patch.object(admin_campus, "_spawn", broken):
        assert client.post("/api/admin/campus/refresh", headers=AUTH_HEADERS).status_code == 503
    assert admin_campus._spawning is False and admin_campus._child is None


# -- status ------------------------------------------------------------------------


def test_status_shape():
    runs = [{"id": "r1", "started_at": "2026-10-01T09:00:00+00:00", "finished_at": None,
             "outcome": "running", "stats": {}, "error": None}]
    pointers = [
        {"source_key": "registrar", "scope_key": "fall-2026", "snapshot_id": "s1",
         "verified_at": "2026-10-01T10:00:00+00:00"},
        {"source_key": "academic", "scope_key": "ay-2026-2027", "snapshot_id": "s2",
         "verified_at": "2026-10-01T10:00:00+00:00"},
    ]
    snaps = [
        {"id": "s1", "page_last_updated": "2026-09-30", "row_count": 33, "fetched_at": "2026-10-01T09:00:00+00:00"},
        {"id": "s2", "page_last_updated": "2026-08-01", "row_count": 20, "fetched_at": "2026-10-01T09:00:00+00:00"},
    ]
    seen = []

    def handler(request: httpx.Request):
        table = request.url.path.rsplit("/", 1)[-1]
        seen.append((table, dict(request.url.params)))
        return httpx.Response(200, json={"campus_refresh_runs": runs, "campus_current": pointers,
                                         "campus_snapshots": snaps}[table])

    with _grants(_granted), respx.mock(assert_all_called=False) as mock:
        mock.route(url__startswith=REST).mock(side_effect=handler)
        res = client.get("/api/admin/campus/status", headers=AUTH_HEADERS)
    body = res.json()
    assert res.status_code == 200
    assert body["runs"] == runs
    assert body["refresh_running"] is False
    assert [(c["source_key"], c["scope_key"], c["row_count"], c["page_last_updated"])
            for c in body["current"]] == [
        ("academic", "ay-2026-2027", 20, "2026-08-01"),
        ("registrar", "fall-2026", 33, "2026-09-30"),
    ]
    assert all("verified_at" in c for c in body["current"])
    runs_query = next(q for t, q in seen if t == "campus_refresh_runs")
    assert runs_query["limit"] == "10" and runs_query["order"] == "started_at.desc"


def test_status_reports_a_running_child_and_503_on_database_failure(spawned):
    with _grants(_granted):
        client.post("/api/admin/campus/refresh", headers=AUTH_HEADERS)
        with respx.mock(assert_all_called=False) as mock:
            mock.route(url__startswith=REST).mock(return_value=httpx.Response(500, text="boom"))
            res = client.get("/api/admin/campus/status", headers=AUTH_HEADERS)
    assert res.status_code == 503 and "boom" not in res.text
    assert admin_campus._running() is True
