"""
Registration terms and deadlines API (R3).

Run from backend/ with:
    python -m pytest tests/test_registration_api.py

PostgREST is faked with respx using the request shapes campus/store.py writes
and services/registration.py reads: `campus_current` pointers, `campus_snapshots`
by id, `reg_term_events` by snapshot_id.
"""
import asyncio
import os
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

import main
import ratelimit
from services import registration
from .conftest import AUTH_HEADERS, GUEST_HEADERS, SUPABASE_URL

client = TestClient(main.app)

# The autouse fixture replaces registration.pacific_today; keep the real one.
REAL_PACIFIC_TODAY = registration.pacific_today

REST = f"{SUPABASE_URL}/rest/v1"
SNAP_FALL = "11111111-1111-4111-8111-111111111111"
SNAP_AY = "22222222-2222-4222-8222-222222222222"
NOW = datetime(2026, 10, 1, 18, 0, tzinfo=timezone.utc)
TODAY = date(2026, 10, 1)


def _event(label, date_raw, start, end=None, key=None, category="registrar"):
    return {"category": category, "label_raw": label, "date_raw": date_raw,
            "start_date": start, "end_date": end, "event_key": key}


SEP15 = "Drop, add, audit and excess-units deadlines"
FALL_ROWS = [
    _event("Last day of instruction", "Mon, Dec 7", "2026-12-07", None, "instruction_last_day"),
    *[
        _event(SEP15, "Tue, Sep 15", "2026-09-15", None, k)
        for k in ("drop_without_w_last", "add_drop_deadline", "audit_crnc_deadline",
                  "excess_units_deadline", "instructor_drops_deadline")
    ],
    _event("First day of instruction", "Wed, Aug 19", "2026-08-19", None, "instruction_first_day"),
]
AY_ROWS = [
    _event("Labor Day", "Mon, Sep 7", "2026-09-07", None, None, "academic"),
    _event("Winter break", "Dec 24 - Jan 1", "2026-12-24", "2027-01-01", None, "academic"),
    _event("Spring recess", "Mar 22 - 26", "2027-03-22", "2027-03-26", None, "academic"),
    _event("Summer start", "Jun 1", "2026-06-01", None, None, "academic"),
]


class Fake:
    """A PostgREST stand-in that records what was asked of it."""

    def __init__(self, pointers=None, rows=None, verified="2026-10-01T10:00:00+00:00"):
        self.calls = []
        self.verified = verified
        self.pointers = pointers if pointers is not None else {
            ("registrar", "fall-2026"): SNAP_FALL, ("academic", "ay-2026-2027"): SNAP_AY}
        self.rows = rows if rows is not None else {SNAP_FALL: FALL_ROWS, SNAP_AY: AY_ROWS}

    def __call__(self, request: httpx.Request):
        table = request.url.path.rsplit("/", 1)[-1]
        q = dict(request.url.params)
        self.calls.append((table, q))

        def strip(v):
            return v.split(".", 1)[1]

        if table == "campus_current":
            out = [
                {"source_key": s, "scope_key": k, "snapshot_id": sid, "verified_at": self.verified}
                for (s, k), sid in self.pointers.items()
                if ("source_key" not in q or s == strip(q["source_key"]))
                and ("scope_key" not in q or k == strip(q["scope_key"]))
            ]
        elif table == "campus_snapshots":
            ids = strip(q["id"]).strip("()").split(",")
            out = [{"id": i, "source_url": f"https://www.sjsu.edu/x/{i[:4]}.php",
                    "page_last_updated": "2026-09-30", "fetched_at": "2026-10-01T09:00:00+00:00"}
                   for i in ids]
        elif table == "reg_term_events":
            out = self.rows.get(strip(q["snapshot_id"]), [])
        else:
            return httpx.Response(404, json={"code": "PGRST205"})
        return httpx.Response(200, json=out)

    def count(self, table):
        return sum(1 for t, _ in self.calls if t == table)


@pytest.fixture(autouse=True)
def _env():
    registration.clear_cache()
    ratelimit.clear_rate_limits()
    env = {"SUPABASE_SERVICE_KEY": "service-key-for-tests"}
    with patch.dict(os.environ, env), patch.object(registration, "pacific_today", lambda now=None: TODAY):
        os.environ.pop("REG_API_ENABLED", None)
        yield
    registration.clear_cache()
    ratelimit.clear_rate_limits()


def _mock(fake):
    mock = respx.mock(assert_all_called=False)
    mock.route(url__startswith=REST).mock(side_effect=fake)
    return mock


# -- auth and switches -------------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/registration/terms", "/api/registration/deadlines"])
def test_no_token_is_401_and_guest_is_200(path):
    assert client.get(path).status_code == 401
    with _mock(Fake()):
        assert client.get(path, headers=GUEST_HEADERS).status_code == 200
        assert client.get(path, headers=AUTH_HEADERS).status_code == 200


@pytest.mark.parametrize("path", ["/api/registration/terms", "/api/registration/deadlines"])
def test_flag_off_is_503_and_supabase_is_never_called(path):
    fake = Fake()
    with patch.dict(os.environ, {"REG_API_ENABLED": "false"}), _mock(fake):
        res = client.get(path, headers=AUTH_HEADERS)
    assert res.status_code == 503
    assert fake.calls == []


def test_invalid_term_is_422_without_a_query():
    fake = Fake()
    with _mock(fake):
        for bad in ("fall-26", "autumn-2026", "fall-2026;drop", "", "x" * 40):
            res = client.get("/api/registration/deadlines", params={"term": bad}, headers=AUTH_HEADERS)
            assert res.status_code == 422, bad
    assert fake.calls == []


def test_supabase_error_timeout_and_missing_tables_are_503():
    with respx.mock(assert_all_called=False) as mock:
        mock.route(url__startswith=REST).mock(return_value=httpx.Response(500, text="boom"))
        res = client.get("/api/registration/deadlines?term=fall-2026", headers=AUTH_HEADERS)
        assert res.status_code == 503
        assert "boom" not in res.text
    registration.clear_cache()
    with respx.mock(assert_all_called=False) as mock:
        mock.route(url__startswith=REST).mock(side_effect=httpx.ReadTimeout("slow"))
        assert client.get("/api/registration/terms", headers=AUTH_HEADERS).status_code == 503
    registration.clear_cache()
    with respx.mock(assert_all_called=False) as mock:
        mock.route(url__startswith=REST).mock(return_value=httpx.Response(404, json={"code": "PGRST205"}))
        assert client.get("/api/registration/deadlines?term=fall-2026", headers=AUTH_HEADERS).status_code == 503


def test_unconfigured_supabase_is_503():
    with patch.dict(os.environ, {"SUPABASE_SERVICE_KEY": ""}):
        res = client.get("/api/registration/terms", headers=AUTH_HEADERS)
    assert res.status_code == 503


# -- deadlines ---------------------------------------------------------------------


def test_a_term_with_no_snapshot_is_an_empty_200():
    with _mock(Fake()):
        res = client.get("/api/registration/deadlines?term=spring-2027", headers=GUEST_HEADERS)
    body = res.json()
    assert res.status_code == 200
    assert (body["events"], body["snapshot"], body["status"]) == ([], None, "not_loaded")
    assert body["term"] == "spring-2027"


def test_shape_grouping_sorting_and_academic_window():
    with _mock(Fake()):
        res = client.get("/api/registration/deadlines?term=fall-2026", headers=GUEST_HEADERS)
    body = res.json()
    assert res.status_code == 200 and body["status"] == "ok"
    events = body["events"]
    # Five stored rows for the Sep 15 cell show once, with every key.
    sep15 = [e for e in events if e["label_raw"] == SEP15]
    assert len(sep15) == 1
    assert len(sep15[0]["event_keys"]) == 5
    assert "drop_without_w_last" in sep15[0]["event_keys"]
    assert sep15[0]["date_raw"] == "Tue, Sep 15"  # verbatim
    # Academic rows are kept only inside the term's window.
    labels = [e["label_raw"] for e in events]
    assert "Labor Day" in labels
    # Academic rows are kept when they overlap the nominal window widened by
    # ACADEMIC_WINDOW_MARGIN_DAYS: a break that straddles the end of fall
    # (Dec 24 - Jan 1) belongs on fall's list; spring and summer rows do not.
    assert "Winter break" in labels
    assert not {"Spring recess", "Summer start"} & set(labels)
    assert len(events) == 5  # First day, Labor Day, Sep 15 (once), Last day, Winter break
    starts = [e["start_date"] for e in events]
    assert starts == sorted(starts)
    assert set(events[0]) == {"category", "label_raw", "date_raw", "start_date", "end_date",
                              "event_keys", "passed"}
    snap = body["snapshot"]
    assert set(snap) == {"term", "source_url", "page_last_updated", "fetched_at", "verified_at", "stale"}
    assert snap["term"] == "fall-2026" and snap["stale"] is False
    assert body["academic_snapshot"]["term"] == "ay-2026-2027"
    assert "Fall 2026" in body["how"]


def test_academic_row_straddling_the_window_edge_is_included():
    rows = {SNAP_FALL: FALL_ROWS, SNAP_AY: AY_ROWS + [
        _event("Break spanning the start", "Aug 15 - 21", "2026-08-15", "2026-08-21", None, "academic")]}
    with _mock(Fake(rows=rows)):
        body = client.get("/api/registration/deadlines?term=fall-2026", headers=GUEST_HEADERS).json()
    assert "Break spanning the start" in [e["label_raw"] for e in body["events"]]


def test_events_without_a_date_are_kept_last_and_never_passed():
    rows = {SNAP_FALL: FALL_ROWS + [_event("See department", "TBA", None)], SNAP_AY: []}
    with _mock(Fake(rows=rows)):
        events = client.get("/api/registration/deadlines?term=fall-2026", headers=GUEST_HEADERS).json()["events"]
    assert events[-1]["label_raw"] == "See department"
    assert events[-1]["passed"] is False


def test_passed_uses_the_pacific_date_not_utc():
    # 23:30 Pacific on Sep 15 is 06:30 UTC on Sep 16.
    late = datetime(2026, 9, 16, 6, 30, tzinfo=timezone.utc)
    assert REAL_PACIFIC_TODAY(late) == date(2026, 9, 15)
    assert late.date() == date(2026, 9, 16)
    rows = {SNAP_FALL: FALL_ROWS, SNAP_AY: []}

    async def go(today):
        registration.clear_cache()
        with _mock(Fake(rows=rows)):
            out = await registration.term_events("fall-2026", today=today, now=late)
        return {e["label_raw"]: e["passed"] for e in out["events"]}

    on_the_day = asyncio.run(go(REAL_PACIFIC_TODAY(late)))
    assert on_the_day[SEP15] is False  # due today: not passed
    assert on_the_day["First day of instruction"] is True
    assert on_the_day["Last day of instruction"] is False
    # The UTC date would wrongly call the Sep 15 deadline passed.
    assert asyncio.run(go(late.date()))[SEP15] is True


def test_stale_after_48_hours():
    async def go(verified):
        registration.clear_cache()
        with _mock(Fake(verified=verified.isoformat())):
            out = await registration.term_events("fall-2026", today=TODAY, now=NOW)
        return out["snapshot"]["stale"]

    assert asyncio.run(go(NOW - timedelta(hours=47))) is False
    assert asyncio.run(go(NOW - timedelta(hours=49))) is True


def test_pointer_reads_are_cached():
    fake = Fake()
    with _mock(fake):
        for _ in range(3):
            assert client.get("/api/registration/deadlines?term=fall-2026", headers=GUEST_HEADERS).status_code == 200
    # Pointer and snapshot for registrar and for academic are read once; events are not cached.
    assert fake.count("campus_current") == 2
    assert fake.count("campus_snapshots") == 2
    assert fake.count("reg_term_events") == 6
    registration.clear_cache()
    with _mock(fake):
        client.get("/api/registration/deadlines?term=fall-2026", headers=GUEST_HEADERS)
    assert fake.count("campus_current") == 4


def test_omitted_term_resolves_this_semester():
    with _mock(Fake()):
        body = client.get("/api/registration/deadlines", headers=GUEST_HEADERS).json()
    assert body["term"] == "fall-2026" and "today" in body["how"]


# -- terms -------------------------------------------------------------------------


def test_terms_lists_loaded_terms_this_and_next():
    with _mock(Fake()):
        body = client.get("/api/registration/terms", headers=GUEST_HEADERS).json()
    assert [t["term"] for t in body["terms"]] == ["fall-2026"]
    assert body["terms"][0]["snapshot"]["stale"] is False
    assert body["this"]["term"] == "fall-2026" and body["this"]["loaded"] is True
    assert body["next"]["term"] == "spring-2027" and body["next"]["loaded"] is False
    assert "Fall 2026" in body["this"]["how"]


# -- resolve_term ------------------------------------------------------------------


@pytest.mark.parametrize(
    "phrase,today,term",
    [
        ("this semester", date(2026, 10, 1), "fall-2026"),
        ("", date(2026, 10, 1), "fall-2026"),
        ("next semester", date(2026, 10, 1), "spring-2027"),  # decision: Fall -> Spring
        ("next semester", date(2027, 3, 1), "fall-2027"),
        ("this semester", date(2026, 7, 1), "summer-2026"),
        ("this semester", date(2026, 8, 14), "fall-2026"),  # between summer and fall
        ("this semester", date(2026, 12, 25), "spring-2027"),  # winter break
        ("this semester", date(2027, 1, 10), "spring-2027"),
        ("this semester", date(2026, 5, 30), "summer-2026"),  # between spring and summer
        ("what is the deadline for Spring 2027", date(2026, 10, 1), "spring-2027"),
        ("fall 2025", date(2026, 10, 1), "fall-2025"),
        ("spring", date(2026, 10, 1), "spring-2027"),  # no year: the next one
        ("fall", date(2026, 10, 1), "fall-2026"),  # still running
        ("fall 2026", date(2027, 2, 1), "fall-2026"),  # explicit beats today
        ("next semester fall 2027", date(2026, 10, 1), "fall-2027"),  # explicit beats "next"
        ("fall-2026", date(2027, 2, 1), "fall-2026"),
    ],
)
def test_resolve_term(phrase, today, term):
    assert registration.resolve_term(phrase, today).term == term


def test_resolve_term_explains_itself():
    r = registration.resolve_term("this semester", date(2026, 10, 1))
    assert r.how == "today (Oct 1, 2026, Pacific) falls in the Fall 2026 term"
    # Never the nominal sizing dates, which are not SJSU's (Fall 2026 is Aug 19 - Dec 7).
    assert "Aug 20" not in r.how and "Dec 15" not in r.how
    between = registration.resolve_term("this semester", date(2026, 8, 14)).how
    assert "between terms" in between and "Fall 2026" in between
    assert "named" in registration.resolve_term("Fall 2026", date(2026, 10, 1)).how
    assert registration.resolve_term("hello there", date(2026, 10, 1)).term is None


# -- rate-limit scope --------------------------------------------------------------


def test_registration_scope_is_isolated_from_chat():
    env = {"RATE_LIMIT_ENABLED": "true", "REG_RATE_USER_BURST": "2", "REG_RATE_USER_PER_MIN": "1",
           "REG_IP_RATE_BURST": "1000", "REG_IP_RATE_PER_MIN": "100000"}
    with patch.dict(os.environ, env), _mock(Fake()):
        codes = [client.get("/api/registration/terms", headers=AUTH_HEADERS).status_code for _ in range(5)]
    assert codes[0] == 200 and 429 in codes
    # Every bucket it spent is under the "reg:" prefix, so chat's keys are untouched.
    assert ratelimit._buckets and all(k.startswith("reg:") for k in ratelimit._buckets)
