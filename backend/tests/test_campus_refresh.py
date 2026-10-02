"""
The deadline refresh: store, drift checks and the fail-closed flow (SERVICES_BUILD_PLAN R2).

Run from backend/ with:
    python -m pytest tests/test_campus_refresh.py

No network and no database. PostgREST is a small in-memory fake behind respx that
enforces the constraints the migration does (RESTRICT on the pointer, the unique
pointer key, the date-order check), so the tests exercise the real request shapes.
SJSU pages come from tests/fixtures/campus, either as files (`--fixture-dir`) or
served by respx.

Every database path here is offline-tested only: migration 20261001000100 is on
live, but this code has not yet run against it.
"""
import asyncio
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx

from campus import store, terms
from campus.registration import refresh
from campus.registration.refresh import Unit, run_refresh
from kb import fetcher

FIX = Path(__file__).parent / "fixtures" / "campus"
SB = "https://refresh-test.supabase.co"
ENV = {
    "SUPABASE_URL": SB,
    "SUPABASE_SERVICE_KEY": "service-key-for-tests",
    "KB_CRAWL_DELAY": "0",
}
REG = Unit("registrar", "fall-2026")
ACA = Unit("academic", "ay-2026-2027")
REG_URL = REG.url
HTML = {"content-type": "text/html; charset=utf-8"}


def _ago(**kw) -> str:
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat()


def _val(params, key):
    raw = params.get(key)
    return raw.split(".", 1)[1] if raw and "." in raw else None


class FakeDB:
    """Just enough of PostgREST for campus/store.py."""

    def __init__(self):
        self.snapshots: dict[str, dict] = {}
        self.current: dict[tuple, dict] = {}
        self.events: list[dict] = []
        self.runs: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []
        self.fail_events_batch: int | None = None  # fail the nth POST to reg_term_events
        self.event_posts = 0
        self.on_flip_patch = None
        self.delete_error_23503 = False

    # seeding
    def add_snapshot(self, source, scope, *, status="superseded", age=None, rows=40, digest="old", current=False):
        sid = str(uuid.uuid4())
        self.snapshots[sid] = {
            "id": sid, "source_key": source, "scope_key": scope, "status": status,
            "created_at": _ago(**(age or {"minutes": 1})), "content_hash": digest,
            "row_count": rows, "fetched_at": _ago(minutes=1), "page_last_updated": None,
        }
        if current:
            self.current[(source, scope)] = {
                "source_key": source, "scope_key": scope, "snapshot_id": sid, "verified_at": "2026-01-01T00:00:00+00:00",
            }
        return sid

    def count(self, method, table):
        return sum(1 for m, t in self.calls if m == method and t == table)

    # routing
    def handle(self, request: httpx.Request) -> httpx.Response:
        table = request.url.path.rsplit("/", 1)[-1]
        method = request.method
        self.calls.append((method, table))
        params = dict(request.url.params)
        prefer = request.headers.get("prefer", "")
        body = None
        if request.content:
            import json

            body = json.loads(request.content)
        fn = getattr(self, f"_{method.lower()}_{table}", None)
        assert fn, f"unexpected {method} {table}"
        status, payload, headers = fn(params, body, prefer)
        if payload is None or status == 204:
            return httpx.Response(status, headers=headers)
        return httpx.Response(status, json=payload, headers=headers)

    @staticmethod
    def _out(rows, prefer, status=200):
        return (200 if "representation" in prefer else 204, rows if "representation" in prefer else None, {})

    # campus_refresh_runs
    def _get_campus_refresh_runs(self, params, body, prefer):
        since = params["started_at"].split(".", 1)[1].replace(" ", "+")
        rows = [
            {"id": r["id"]} for r in self.runs.values()
            if r["outcome"] == "running" and datetime.fromisoformat(r["started_at"]) >= datetime.fromisoformat(since)
        ]
        return 200, rows, {}

    def _post_campus_refresh_runs(self, params, body, prefer):
        rid = str(uuid.uuid4())
        self.runs[rid] = {"id": rid, "outcome": "running", "started_at": _ago(seconds=1), "stats": {}, "error": None}
        return 201, [self.runs[rid]], {}

    def _patch_campus_refresh_runs(self, params, body, prefer):
        self.runs[_val(params, "id")].update(body)
        return 204, None, {}

    # campus_current
    def _get_campus_current(self, params, body, prefer):
        rows = [
            r for k, r in self.current.items()
            if ("source_key" not in params or k[0] == _val(params, "source_key"))
            and ("scope_key" not in params or k[1] == _val(params, "scope_key"))
        ]
        return 200, [dict(r) for r in rows], {}

    def _post_campus_current(self, params, body, prefer):
        row = body[0]
        key = (row["source_key"], row["scope_key"])
        if key in self.current:
            return 409, {"code": "23505", "message": "duplicate key"}, {}
        self.current[key] = row
        return 201, None, {}

    def _patch_campus_current(self, params, body, prefer):
        if self.on_flip_patch:
            self.on_flip_patch(self)
        hit = [
            r for r in self.current.values()
            if r["source_key"] == _val(params, "source_key")
            and r["scope_key"] == _val(params, "scope_key")
            and r["snapshot_id"] == _val(params, "snapshot_id")
        ]
        for r in hit:
            r.update(body)
        return self._out([dict(r) for r in hit], prefer)

    # campus_snapshots
    def _get_campus_snapshots(self, params, body, prefer):
        rows = list(self.snapshots.values())
        if "id" in params:
            rows = [r for r in rows if r["id"] == _val(params, "id")]
        if "source_key" in params:
            rows = [r for r in rows if r["source_key"] == _val(params, "source_key")]
        if "scope_key" in params:
            rows = [r for r in rows if r["scope_key"] == _val(params, "scope_key")]
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        return 200, [dict(r) for r in rows], {}

    def _post_campus_snapshots(self, params, body, prefer):
        row = dict(body[0])
        row["id"] = str(uuid.uuid4())
        row["created_at"] = _ago(seconds=1)
        self.snapshots[row["id"]] = row
        return 201, [row], {}

    def _patch_campus_snapshots(self, params, body, prefer):
        self.snapshots[_val(params, "id")].update(body)
        return 204, None, {}

    def _delete_campus_snapshots(self, params, body, prefer):
        ids = params["id"][len("in.("):-1].split(",")
        named = {r["snapshot_id"] for r in self.current.values()}
        if self.delete_error_23503 or named & set(ids):
            return 409, {"code": "23503", "message": "violates foreign key campus_current_snapshot_fkey"}, {}
        for i in ids:
            self.snapshots.pop(i, None)
        self.events = [e for e in self.events if e["snapshot_id"] not in ids]
        return 204, None, {}

    # reg_term_events
    def _post_reg_term_events(self, params, body, prefer):
        self.event_posts += 1
        if self.fail_events_batch == self.event_posts:
            return 500, {"message": "boom"}, {}
        for e in body:
            if e["end_date"] is not None and (e["start_date"] is None or e["end_date"] < e["start_date"]):
                return 400, {"code": "23514", "message": "reg_term_events_date_order_check"}, {}
        self.events.extend(body)
        return 201, None, {}

    def _get_reg_term_events(self, params, body, prefer):
        n = sum(1 for e in self.events if e["snapshot_id"] == _val(params, "snapshot_id"))
        return 206, [], {"content-range": f"0-0/{n}"}


def _run(coro_fn, db: FakeDB | None = None, *, pages=None, robots="User-agent: *\nAllow: /\n", env=None):
    """Run `coro_fn(client)` with the DB fake and, if `pages` is given, SJSU served by respx."""

    async def go():
        async def public(_host):
            return True

        with patch.dict("os.environ", {**ENV, **(env or {})}), \
                patch.object(fetcher, "_host_is_public", public), \
                respx.mock(assert_all_called=False) as router:
            if db is not None:
                router.route(host="refresh-test.supabase.co").mock(side_effect=db.handle)
            router.get("https://www.sjsu.edu/robots.txt").respond(200, text=robots)
            for url, response in (pages or {}).items():
                router.get(url).mock(return_value=response)
            async with httpx.AsyncClient(follow_redirects=False) as client:
                result = await coro_fn(client)
            return result, [c.request for c in router.calls]

    return asyncio.run(go())


def _refresh(db, units=(REG,), **kw):
    fixture = kw.pop("fixture_dir", FIX)

    async def fn(client):
        return await run_refresh(client, list(units), fixture_dir=fixture, **kw)

    report, _ = _run(fn, db)
    return report


# -- The flow ------------------------------------------------------------------


def test_happy_path_stages_flips_and_records_the_run():
    db = FakeDB()
    report = _refresh(db, (REG, ACA))
    assert report.outcome == "success"
    assert [r.status for r in report.results] == ["swapped", "swapped"]
    assert set(db.current) == {("registrar", "fall-2026"), ("academic", "ay-2026-2027")}
    snap = db.snapshots[db.current[("registrar", "fall-2026")]["snapshot_id"]]
    assert snap["status"] == "current" and snap["row_count"] == 40
    assert snap["page_last_updated"] == "2026-09-28"
    assert snap["scope_key"] == "fall-2026" and snap["fetched_from"].endswith("registrar-calendar-fall-2026.html")
    assert terms.SCOPE_KEY_RE.match(snap["scope_key"])
    assert len([e for e in db.events if e["snapshot_id"] == snap["id"]]) == 40
    run = db.runs[report.run_id]
    assert run["outcome"] == "success" and run["finished_at"]
    unit = run["stats"]["units"][0]
    assert unit["rows"] == 40 and unit["swapped"] is True and unit["failed_checks"] == []
    assert {"bytes", "truncated", "fetch_ms", "parse_ms", "rejected"} <= set(unit)


def test_a_changed_page_supersedes_the_old_snapshot_and_keeps_it():
    db = FakeDB()
    old = db.add_snapshot("registrar", "fall-2026", status="current", rows=40, digest="old-hash", current=True)
    report = _refresh(db)
    assert report.results[0].status == "swapped"
    new = db.current[("registrar", "fall-2026")]["snapshot_id"]
    assert new != old
    assert db.snapshots[new]["status"] == "current"
    assert db.snapshots[old]["status"] == "superseded"  # kept: the rollback target
    assert ("DELETE", "campus_snapshots") not in db.calls


def test_unchanged_hash_reverifies_without_staging():
    db = FakeDB()
    _refresh(db)
    posts = db.count("POST", "campus_snapshots"), db.count("POST", "reg_term_events")
    pointer = dict(db.current[("registrar", "fall-2026")])
    report = _refresh(db)
    assert report.results[0].status == "unchanged"
    assert (db.count("POST", "campus_snapshots"), db.count("POST", "reg_term_events")) == posts
    after = db.current[("registrar", "fall-2026")]
    assert after["snapshot_id"] == pointer["snapshot_id"]
    assert after["verified_at"] != pointer["verified_at"]
    assert report.outcome == "success"


def test_drift_failure_means_no_flip_and_a_failed_run(tmp_path):
    html = (FIX / "registrar-calendar-fall-2026.html").read_text(encoding="utf-8")
    broken = html.replace("Grades Due from Faculty", "Something else entirely")
    (tmp_path / "registrar-calendar-fall-2026.html").write_text(broken, encoding="utf-8")
    db = FakeDB()
    old = db.add_snapshot("registrar", "fall-2026", status="current", rows=40, digest="old-hash", current=True)
    report = _refresh(db, fixture_dir=tmp_path)
    assert report.outcome == "failed"
    res = report.results[0]
    assert res.status == "failed" and "core_keys" in res.failed_checks
    assert db.current[("registrar", "fall-2026")]["snapshot_id"] == old  # readers stay put
    assert db.count("PATCH", "campus_current") == 0 and db.count("POST", "reg_term_events") == 0
    rejected = [s for s in db.snapshots.values() if s["status"] == "rejected"]
    assert len(rejected) == 1 and rejected[0]["checks"]["core_keys"]["ok"] is False
    assert db.runs[report.run_id]["outcome"] == "failed"
    assert "core_keys" in db.runs[report.run_id]["error"]


def test_fewer_than_70_percent_of_the_previous_rows_fails():
    db = FakeDB()
    db.add_snapshot("registrar", "fall-2026", status="current", rows=60, digest="old-hash", current=True)  # 40 < 42
    report = _refresh(db)
    assert report.results[0].failed_checks == ["rows_vs_previous"]
    assert db.count("PATCH", "campus_current") == 0
    db2 = FakeDB()
    db2.add_snapshot("registrar", "fall-2026", status="current", rows=57, digest="old-hash", current=True)  # 40 >= 40
    assert _refresh(db2).results[0].status == "swapped"


def test_an_error_midway_through_a_batch_means_no_flip():
    db = FakeDB()
    old = db.add_snapshot("registrar", "fall-2026", status="current", rows=40, digest="old-hash", current=True)
    db.fail_events_batch = 2
    with patch.object(store, "BATCH_SIZE", 15):
        report = _refresh(db)
    assert report.outcome == "failed" and report.results[0].status == "failed"
    assert db.event_posts == 2  # first batch landed, second failed
    assert db.current[("registrar", "fall-2026")]["snapshot_id"] == old
    assert db.count("PATCH", "campus_current") == 0
    staged = [s for s in db.snapshots.values() if s["id"] != old]
    assert len(staged) == 1 and staged[0]["status"] == "rejected"
    assert db.runs[report.run_id]["outcome"] == "failed"


def test_a_date_order_violation_is_caught_before_any_request():
    db = FakeDB()

    async def fn(client):
        row = {"category": "registrar", "label_raw": "x", "date_raw": "y",
               "start_date": date(2026, 9, 2), "end_date": date(2026, 9, 1), "event_key": None}
        with pytest.raises(store.StoreError, match="date_order"):
            await store.insert_events(client, "sid", [row])
        with pytest.raises(store.StoreError, match="category"):
            await store.insert_events(client, "sid", [{**row, "category": "bogus", "end_date": None}])
        with pytest.raises(store.StoreError, match="scope_key"):
            await store.stage_snapshot(
                client, source="registrar", scope="Fall 2026", source_url="u", fetched_from=None,
                page_last_updated=None, content_hash="h", row_count=0, header=None, checks={},
            )
        with pytest.raises(store.StoreError, match="outcome"):
            await store.finish_run(client, "rid", "done", {})

    _run(fn, db)
    assert db.calls == []


# -- Pruning -------------------------------------------------------------------


def test_prune_never_targets_the_current_snapshot():
    db = FakeDB()
    old = db.add_snapshot("registrar", "fall-2026", status="current", age={"hours": 1}, digest="old-hash", current=True)
    db.add_snapshot("registrar", "fall-2026", status="superseded", age={"days": 8})
    db.add_snapshot("registrar", "fall-2026", status="superseded", age={"days": 7})
    seen = []
    original = db._delete_campus_snapshots

    def spy(params, body, prefer):
        seen.append((params["id"], {r["snapshot_id"] for r in db.current.values()}))
        return original(params, body, prefer)

    db._delete_campus_snapshots = spy
    report = _refresh(db)
    assert report.results[0].status == "swapped"
    new = db.current[("registrar", "fall-2026")]["snapshot_id"]
    assert len(seen) == 1
    ids, pointed = seen[0]
    assert pointed == {new} and new not in ids
    assert old in db.snapshots and db.snapshots[old]["status"] == "superseded"  # the rollback target
    assert len(db.snapshots) == 2  # the pointer and the rollback target; both week-old ones are gone


def test_a_stale_current_status_orphan_is_pruned_after_24_hours():
    # A crash between the flip and the status update leaves the old snapshot at
    # status='current' with nothing pointing at it. A status filter would keep it.
    db = FakeDB()
    orphan = db.add_snapshot("registrar", "fall-2026", status="current", age={"days": 3})
    keeper = db.add_snapshot("registrar", "fall-2026", status="superseded", age={"days": 2})
    recent = db.add_snapshot("registrar", "fall-2026", status="current", age={"hours": 2})
    live = db.add_snapshot("registrar", "fall-2026", status="current", age={"minutes": 5}, current=True)

    async def fn(client):
        return await store.prune(client, "registrar", "fall-2026")

    pruned, _ = _run(fn, db)
    # newest non-pointer good snapshot (`recent`) is the rollback target; `orphan` is old and unreferenced
    assert pruned == 2
    assert set(db.snapshots) == {recent, live}
    assert orphan not in db.snapshots and keeper not in db.snapshots


def test_prune_by_age_alone_leaves_young_snapshots_and_old_rejected_ones_go():
    now = datetime.now(timezone.utc)
    snaps = [
        {"id": "cur", "status": "current", "created_at": (now - timedelta(minutes=1)).isoformat()},
        {"id": "sup", "status": "superseded", "created_at": (now - timedelta(days=5)).isoformat()},
        {"id": "rej-old", "status": "rejected", "created_at": (now - timedelta(days=2)).isoformat()},
        {"id": "rej-new", "status": "rejected", "created_at": (now - timedelta(hours=3)).isoformat()},
        {"id": "staged-old", "status": "staged", "created_at": (now - timedelta(days=4)).isoformat()},
    ]
    doomed = store.plan_prune(snaps, {"cur"}, now=now)
    assert sorted(doomed) == ["rej-old", "staged-old"]  # `sup` is the rollback target, `rej-new` is young


def test_a_23503_on_prune_skips_the_prune_not_the_refresh():
    db = FakeDB()
    db.add_snapshot("registrar", "fall-2026", status="current", rows=40, digest="old-hash", current=True)
    db.add_snapshot("registrar", "fall-2026", status="superseded", age={"days": 5})
    db.add_snapshot("registrar", "fall-2026", status="superseded", age={"days": 4})
    db.delete_error_23503 = True
    report = _refresh(db)
    assert report.outcome == "success" and report.results[0].status == "swapped"
    assert any("prune skipped" in n for n in report.results[0].notes)
    assert db.count("DELETE", "campus_snapshots") == 1
    assert db.runs[report.run_id]["outcome"] == "success"


# -- The flip is a compare-and-swap ---------------------------------------------


def test_a_lost_flip_race_rejects_our_snapshot_and_prunes_nothing():
    db = FakeDB()
    old = db.add_snapshot("registrar", "fall-2026", status="current", rows=40, digest="old-hash", current=True)
    db.add_snapshot("registrar", "fall-2026", status="superseded", age={"days": 5})
    rival = db.add_snapshot("registrar", "fall-2026", status="staged", digest="rival")

    def rival_wins(d):
        d.current[("registrar", "fall-2026")]["snapshot_id"] = rival

    db.on_flip_patch = rival_wins
    report = _refresh(db)
    res = report.results[0]
    assert res.status == "failed" and res.reason == "lost_race"
    assert report.outcome == "failed"
    assert db.current[("registrar", "fall-2026")]["snapshot_id"] == rival
    ours = [s for s in db.snapshots.values() if s["id"] not in (old, rival) and s["status"] == "rejected"]
    assert len(ours) == 1 and ours[0]["checks"]["outcome"]["detail"] == "lost_race"
    assert db.count("DELETE", "campus_snapshots") == 0
    assert db.snapshots[old]["status"] == "current"  # untouched: we did not supersede it


def test_first_flip_conflict_is_the_same_lost_race():
    db = FakeDB()
    orig = db._post_campus_current

    def rival_first(params, body, prefer):
        rival = db.add_snapshot("registrar", "fall-2026", status="current", digest="rival", current=True)
        return orig(params, body, prefer)  # now a 409

    db._post_campus_current = rival_first
    report = _refresh(db)
    assert report.results[0].reason == "lost_race" and report.outcome == "failed"
    assert db.count("DELETE", "campus_snapshots") == 0


def test_flip_requires_exactly_one_row():
    db = FakeDB()
    old = db.add_snapshot("registrar", "fall-2026", status="current", current=True)
    new = db.add_snapshot("registrar", "fall-2026", status="staged")

    async def fn(client):
        assert await store.flip(client, "registrar", "fall-2026", new, old) is True
        assert await store.flip(client, "registrar", "fall-2026", old, old) is False  # pointer no longer at `old`

    _run(fn, db)
    assert db.current[("registrar", "fall-2026")]["snapshot_id"] == new


# -- Single flight --------------------------------------------------------------


def test_refuses_to_start_while_another_run_is_in_flight():
    db = FakeDB()
    busy = str(uuid.uuid4())
    db.runs[busy] = {"id": busy, "outcome": "running", "started_at": _ago(minutes=5)}
    report = _refresh(db)
    assert report.outcome == "busy" and report.results == []
    assert db.count("POST", "campus_snapshots") == 0 and db.count("POST", "campus_refresh_runs") == 0


def test_an_old_running_row_counts_as_crashed_and_does_not_block():
    db = FakeDB()
    stale = str(uuid.uuid4())
    db.runs[stale] = {"id": stale, "outcome": "running", "started_at": _ago(minutes=45)}
    assert _refresh(db).outcome == "success"
    assert db.runs[stale]["outcome"] == "running"  # left alone


# -- Fetching -------------------------------------------------------------------


def _page(name="registrar-calendar-fall-2026.html"):
    return httpx.Response(200, text=(FIX / name).read_text(encoding="utf-8"), headers=HTML)


def _fetch_refresh(db, units, pages, **kw):
    async def fn(client):
        return await run_refresh(client, list(units), **kw.pop("run", {}))

    return _run(fn, db, pages=pages, **kw)


def test_network_happy_path_goes_through_robots_and_the_fetcher():
    db = FakeDB()
    report, sent = _fetch_refresh(db, (REG,), {REG_URL: _page()})
    assert report.outcome == "success" and report.results[0].status == "swapped"
    sent = [r for r in sent if r.url.host == "www.sjsu.edu"]
    assert str(sent[0].url) == "https://www.sjsu.edu/robots.txt"
    page_req = next(r for r in sent if str(r.url) == REG_URL)
    assert "SJSUCopilotBot" in page_req.headers["user-agent"]
    snap = db.snapshots[db.current[("registrar", "fall-2026")]["snapshot_id"]]
    assert snap["source_url"] == REG_URL and snap["fetched_from"] == REG_URL


def test_a_robots_denied_source_is_skipped_without_fetching_the_page():
    db = FakeDB()
    report, sent = _fetch_refresh(
        db, (REG,), {REG_URL: _page()}, robots="User-agent: *\nDisallow: /registrar/\n"
    )
    assert report.results[0].status == "skipped" and report.results[0].reason == "robots_denied"
    assert report.outcome == "failed"  # nothing refreshed
    assert not any(str(r.url) == REG_URL for r in sent)
    assert db.count("POST", "campus_snapshots") == 0


def test_a_truncated_fetch_fails_drift():
    db = FakeDB()
    old = db.add_snapshot("registrar", "fall-2026", status="current", rows=40, digest="old-hash", current=True)
    with patch.object(refresh, "MAX_BYTES", 6000):
        report, _ = _fetch_refresh(db, (REG,), {REG_URL: _page()})
    res = report.results[0]
    assert res.truncated and "complete" in res.failed_checks
    assert res.status == "failed"
    assert db.current[("registrar", "fall-2026")]["snapshot_id"] == old
    assert db.count("PATCH", "campus_current") == 0


def test_not_posted_is_quiet_for_a_new_term_and_a_failure_for_a_held_one():
    pages = {REG_URL: httpx.Response(404, text="not found", headers=HTML)}
    db = FakeDB()
    report, _ = _fetch_refresh(db, (REG,), pages)
    assert report.results[0].status == "not_posted" and report.outcome == "success"
    assert db.count("POST", "campus_snapshots") == 0
    assert db.runs[report.run_id]["error"] is None

    held = FakeDB()
    held.add_snapshot("registrar", "fall-2026", status="current", current=True)
    report, _ = _fetch_refresh(held, (REG,), pages)
    assert report.results[0].status == "failed" and "gone" in report.results[0].reason


def test_a_server_error_fails_the_unit_and_leaves_the_pointer():
    db = FakeDB()
    old = db.add_snapshot("registrar", "fall-2026", status="current", current=True)
    report, _ = _fetch_refresh(db, (REG,), {REG_URL: httpx.Response(503)})
    assert report.results[0].status == "failed" and report.outcome == "failed"
    assert db.current[("registrar", "fall-2026")]["snapshot_id"] == old


def test_the_refresh_only_ever_fetches_www_sjsu_edu():
    for unit in refresh.plan_units(date(2026, 10, 1)):
        assert terms.host_allowed(unit.url)
        assert unit.url.startswith("https://www.sjsu.edu/")


# -- Planning, dry run and the CLI ------------------------------------------------


def test_plan_units():
    units = refresh.plan_units(date(2026, 10, 1))
    assert [(u.source, u.scope) for u in units] == [
        ("registrar", "fall-2026"), ("registrar", "spring-2027"), ("registrar", "summer-2027"),
        ("academic", "ay-2026-2027"),
    ]
    only = refresh.plan_units(date(2026, 10, 1), term="spring-2027")
    assert [(u.source, u.scope) for u in only] == [("registrar", "spring-2027"), ("academic", "ay-2026-2027")]
    assert [u.source for u in refresh.plan_units(date(2026, 10, 1), source="academic")] == ["academic"]


def test_dry_run_writes_nothing_and_needs_no_database():
    db = FakeDB()
    report = _refresh(db, (REG, ACA), dry_run=True)
    assert report.outcome == "success" and report.run_id is None
    assert [r.status for r in report.results] == ["dry_run", "dry_run"]
    assert db.calls == []


def test_cli_dry_run_prints_the_table(capsys, monkeypatch):
    for k in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(refresh, "load_dotenv", lambda *a, **k: None)
    code = asyncio.run(
        refresh._main(["--dry-run", "--fixture-dir", str(FIX), "--term", "fall-2026"])
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "registrar fall-2026" in out and "academic ay-2026-2027" in out
    assert "2026-09-15" in out and "drop_without_w_last" in out
    assert "ok   core_keys" in out


# -- Review follow-ups (2026-10-01) ---------------------------------------------

SPRING = Unit("registrar", "spring-2027")
SPRING_URL = SPRING.url


def test_the_spring_layout_refreshes_end_to_end_from_its_fixture():
    db = FakeDB()
    report = _refresh(db, (SPRING,))
    res = report.results[0]
    assert res.status == "swapped", (res.reason, res.failed_checks)
    assert res.checks["term_dates"]["ok"] and res.checks["header"]["ok"]
    assert ("registrar", "spring-2027") in db.current


def test_another_terms_page_at_this_terms_url_fails_term_dates():
    # Fall 2026's content served at the Spring 2027 URL parses inside spring's
    # window, but its first day of instruction is months from spring's start.
    db = FakeDB()
    report, _ = _fetch_refresh(db, (SPRING,), {SPRING_URL: _page()})
    res = report.results[0]
    assert res.status == "failed" and "term_dates" in res.failed_checks
    assert ("registrar", "spring-2027") not in db.current
    assert db.count("POST", "campus_current") == 0


def test_a_redirected_term_url_is_not_posted_for_a_new_term_and_a_failure_for_a_held_one():
    pages = {
        SPRING_URL: httpx.Response(301, headers={"location": REG_URL}),
        REG_URL: _page(),
    }
    db = FakeDB()
    report, _ = _fetch_refresh(db, (SPRING,), pages)
    res = report.results[0]
    assert res.status == "not_posted" and "redirected" in res.reason
    assert db.count("POST", "campus_snapshots") == 0

    held = FakeDB()
    held.add_snapshot("registrar", "spring-2027", status="current", current=True)
    report, _ = _fetch_refresh(held, (SPRING,), pages)
    assert report.results[0].status == "failed" and "gone" in report.results[0].reason


def test_an_interrupted_run_is_recorded_as_failed_not_success():
    db = FakeDB()

    async def boom(*_a, **_kw):
        raise asyncio.CancelledError

    async def fn(client):
        with patch.object(refresh, "process_unit", boom), pytest.raises(asyncio.CancelledError):
            await run_refresh(client, [REG, ACA], fixture_dir=FIX)

    _run(fn, db)
    (run,) = db.runs.values()
    assert run["outcome"] == "failed"
    assert "interrupted after 0 of 2 units" in run["error"]


def test_a_non_unique_409_on_the_first_flip_is_not_a_lost_race():
    db = FakeDB()

    def fk_violation(params, body, prefer):
        return 409, {"code": "23503", "message": "violates foreign key"}, {}

    db._post_campus_current = fk_violation
    report = _refresh(db)
    res = report.results[0]
    assert res.status == "failed" and res.reason.startswith("publication unconfirmed")
    staged = [s for s in db.snapshots.values() if s["status"] == "rejected"]
    assert staged == []  # not rejected as a lost race: the flip may or may not have landed


def test_fixture_dir_without_dry_run_is_refused_before_anything_runs():
    with patch.dict("os.environ", ENV):
        code = asyncio.run(refresh._main(["--fixture-dir", str(FIX)]))
    assert code == 2
