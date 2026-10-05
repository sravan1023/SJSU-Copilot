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
    admin_campus._last_exit = None
    with patch.dict(os.environ, {"SUPABASE_SERVICE_KEY": "service-key-for-tests"}):
        yield
    admin_campus._child = None
    admin_campus._spawning = False
    admin_campus._last_exit = None


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


# -- the child's output and exit code -------------------------------------------------


class _Popen:
    """Records what _spawn handed to subprocess.Popen."""

    def __init__(self, argv, **kw):
        self.argv, self.kw, self.pid = argv, kw, 7
        self.log_closed_during = kw["stdout"].closed


@pytest.fixture
def popen(tmp_path):
    made = []

    def factory(argv, **kw):
        p = _Popen(argv, **kw)
        made.append(p)
        return p

    with patch.object(admin_campus, "LOG_PATH", tmp_path / "logs" / "campus-refresh.log"),             patch.object(admin_campus.subprocess, "Popen", factory):
        yield made


def test_spawn_sends_stdout_and_stderr_to_an_appended_log_and_closes_the_handle(popen):
    log_path = admin_campus.LOG_PATH
    argv = [sys.executable, "-m", "campus.registration.refresh", "--source", "registrar"]
    admin_campus._spawn(argv, "cwd")
    admin_campus._spawn(argv, "cwd")
    first, second = popen
    handle = first.kw["stdout"]
    assert handle.name == str(log_path) and "a" in handle.mode  # append
    assert first.kw["stderr"] is admin_campus.subprocess.STDOUT  # same stream, same file
    assert first.log_closed_during is False and handle.closed is True  # parent let go
    text = log_path.read_text(encoding="utf-8")
    assert text.count("=== ") == 2  # appended, not truncated
    assert "--source registrar" in text and "+00:00" in text  # timestamp and argv header


def test_create_no_window_only_on_windows(popen):
    with patch.object(admin_campus.sys, "platform", "win32"):
        admin_campus._spawn(["x"], "cwd")
    with patch.object(admin_campus.sys, "platform", "linux"):
        admin_campus._spawn(["x"], "cwd")
    win, other = popen
    assert win.kw["creationflags"] == admin_campus.subprocess.CREATE_NO_WINDOW
    assert "creationflags" not in other.kw


@pytest.mark.parametrize("code,meaning", [
    (0, "ok"), (1, "failed or partial"), (2, "bad arguments or not configured"),
    (3, "busy (another refresh holds the lock)"), (9, "unexpected exit code"),
])
def test_exit_code_meaning_is_logged_and_in_status(code, meaning, spawned, caplog):
    with _grants(_granted):
        client.post("/api/admin/campus/refresh", headers=AUTH_HEADERS)
        admin_campus._child.code = code
        with caplog.at_level("INFO", logger=admin_campus.logger.name),                 respx.mock(assert_all_called=False) as mock:
            mock.route(url__startswith=REST).mock(return_value=httpx.Response(200, json=[]))
            body = client.get("/api/admin/campus/status", headers=AUTH_HEADERS).json()
    assert body["refresh_running"] is False
    assert (body["last_exit_code"], body["last_exit_meaning"]) == (code, meaning)
    rec = next(r for r in caplog.records if r.getMessage() == "campus refresh exited")
    assert (rec.exit_code, rec.meaning) == (code, meaning)


def test_status_has_no_exit_code_before_any_child_has_finished(spawned):
    with _grants(_granted), respx.mock(assert_all_called=False) as mock:
        mock.route(url__startswith=REST).mock(return_value=httpx.Response(200, json=[]))
        body = client.get("/api/admin/campus/status", headers=AUTH_HEADERS).json()
    assert body["last_exit_code"] is None and body["last_exit_meaning"] is None
