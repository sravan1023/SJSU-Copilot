"""Read side of Registration Info: terms and deadlines from the campus snapshots.

The offline refresh (`backend/campus/`) writes; this module only reads, through
the shared Supabase client with the service key (no table is granted to anon).
Readers resolve through `campus_current`, never by snapshot `status`, which can
lag the pointer after a crash.

**Why a pointer cache is safe.** A refresh prunes only snapshots older than 24 h
that no pointer names, so a pointer cached for 5 minutes cannot name a deleted
snapshot. A flip is seen after at most 5 minutes, which is fine for a page that
changes a few times a term.

**Pacific time.** "Today" is the calendar date in America/Los_Angeles. At 23:30
Pacific the UTC date is already tomorrow, and a deadline that is "today" would
read as passed.
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

import observability
import runtime
from campus import terms

logger = logging.getLogger(__name__)

PACIFIC = ZoneInfo("America/Los_Angeles")
POINTER_TTL_S = 300.0
NEGATIVE_TTL_S = 60.0  # a term the refresh has just loaded should appear soon
STALE_AFTER = timedelta(hours=48)
EVENT_LIMIT = 1000  # a term calendar is ~30 cells; this is a ceiling, not a page size
# Academic-calendar rows are kept if they overlap the term's nominal window
# widened by this much at each end (see term_events).
ACADEMIC_WINDOW_MARGIN_DAYS = 14

# Regular terms in calendar order. Winter is a January extra: it can be named
# explicitly but is never what "this semester" means (campus.terms.current_term
# treats January as spring).
_REGULAR = ("spring", "summer", "fall")
_SEASON_RE = re.compile(r"\b(spring|summer|fall|winter)\b(?:[\s\-]*(20\d{2}|'\d{2}))?", re.I)
_THIS_RE = re.compile(r"\b(this|current|now)\b", re.I)
_NEXT_RE = re.compile(r"\bnext\b", re.I)


class RegistrationUnavailable(RuntimeError):
    """Supabase failed, timed out, or lacks the campus tables."""


def enabled() -> bool:
    return os.getenv("REG_API_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


def pacific_today(now: datetime | None = None) -> date:
    now = now or datetime.now(timezone.utc)
    return now.astimezone(PACIFIC).date()


# -- Term resolution ------------------------------------------------------------


@dataclass(frozen=True)
class Resolution:
    term: str | None
    how: str


def term_label(term_key: str) -> str:
    season, year = terms.split_term(term_key)
    return f"{season.capitalize()} {year}"


def _fmt(d: date) -> str:
    return f"{d:%b} {d.day}"


def _regular_terms_around(today: date) -> list[str]:
    return [
        f"{s}-{y}" for y in (today.year - 1, today.year, today.year + 1) for s in _REGULAR
    ]


def _this_term(today: date) -> tuple[str, str]:
    """The regular term containing `today`, else the next to start, with the reason."""
    # The explanation names the term and never prints term_window's dates: those
    # are nominal sizing values (Fall: Aug 20 - Dec 15), not SJSU's calendar
    # (Fall 2026: Aug 19 - Dec 7), and this text is shown beside SJSU's real
    # dates. The real first and last days are events on the same page.
    when = f"today ({_fmt(today)}, {today.year}, Pacific)"
    candidates = _regular_terms_around(today)
    for key in candidates:
        start, end = terms.term_window(key)
        if start <= today <= end:
            return key, f"{when} falls in the {term_label(key)} term"
    for key in candidates:
        start, _ = terms.term_window(key)
        if start > today:
            return key, (
                f"{when} falls between terms, so this is the next term to start: "
                f"{term_label(key)}"
            )
    raise ValueError("no term found")  # unreachable: three years of candidates


def _semester_after(term_key: str) -> str:
    """Fall -> Spring (decision); Spring and Summer -> Fall.

    Summer is not skipped by accident: students asking about "next semester" in
    spring mean fall. A summer question names summer.
    """
    season, year = terms.split_term(term_key)
    return f"spring-{year + 1}" if season == "fall" else f"fall-{year}"


def _explicit(phrase: str, today: date) -> Resolution | None:
    m = _SEASON_RE.search(phrase)
    if not m:
        return None
    season = m.group(1).lower()
    if m.group(2):
        yr = int(m.group(2).lstrip("'"))
        yr += 2000 if yr < 100 else 0
        key = f"{season}-{yr}"
        if not terms.is_term_key(key):
            return None
        return Resolution(key, f"{term_label(key)} was named in the question")
    # A season without a year: the first one not yet over.
    for y in (today.year - 1, today.year, today.year + 1, today.year + 2):
        key = f"{season}-{y}"
        if terms.term_window(key)[1] >= today:
            return Resolution(key, f"{season.capitalize()} was named without a year, so the next one: {term_label(key)}")
    return None


def resolve_term(phrase: str | None, today: date) -> Resolution:
    """Which term a phrase means, and why. `term` is None if it cannot be told.

    Order: an explicit term wins; "this semester" is the term whose window holds
    `today`; between terms it is the next term to start; "next semester" is the
    term after that one.
    """
    text = (phrase or "").strip()
    if terms.is_term_key(text.lower()):
        key = text.lower()
        return Resolution(key, f"{term_label(key)} was named in the question")
    explicit = _explicit(text, today) if text else None
    if explicit:
        return explicit
    this, why = _this_term(today)
    if _NEXT_RE.search(text):
        nxt = _semester_after(this)
        return Resolution(nxt, f"'next semester' follows {term_label(this)}: {why}")
    if not text or _THIS_RE.search(text) or re.search(r"semester|term|quarter", text, re.I):
        return Resolution(this, why)
    return Resolution(None, f"could not tell which term {text[:40]!r} means")


# -- Supabase reads -------------------------------------------------------------


def _headers() -> dict[str, str]:
    key = os.getenv("SUPABASE_SERVICE_KEY", "").strip()
    return {"apikey": key, "Authorization": f"Bearer {key}", "Accept": "application/json"}


async def get_rows(path: str, params: dict) -> list[dict]:
    """GET a PostgREST table. Any failure is `RegistrationUnavailable`."""
    url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    if not url or not os.getenv("SUPABASE_SERVICE_KEY", "").strip():
        raise RegistrationUnavailable("Supabase is not configured")
    endpoint = f"{url}/rest/v1/{path}"
    try:
        shared = runtime.get_supabase_client()
        if shared is not None:
            res = await shared.get(endpoint, params=params, headers=_headers())
        else:
            async with httpx.AsyncClient(timeout=3.0, follow_redirects=False) as client:
                res = await client.get(endpoint, params=params, headers=_headers())
    except httpx.HTTPError as exc:
        raise RegistrationUnavailable(f"database unreachable ({type(exc).__name__})") from exc
    if res.status_code >= 400:
        # A missing table arrives as 404 (PGRST205); the status is enough to tell
        # an operator where to look, and the body can name internals.
        raise RegistrationUnavailable(f"database error {res.status_code}")
    try:
        rows = res.json()
    except ValueError as exc:
        raise RegistrationUnavailable("database returned invalid JSON") from exc
    if not isinstance(rows, list):
        raise RegistrationUnavailable("database returned an unexpected shape")
    return rows


_cache: dict[tuple[str, str], tuple[float, object]] = {}


def clear_cache() -> None:
    _cache.clear()


def _cache_get(key: tuple[str, str]):
    hit = _cache.get(key)
    if hit and hit[0] > time.monotonic():
        observability.incr("reg_pointer_cache_hit")
        return True, hit[1]
    return False, None


def _cache_put(key: tuple[str, str], value, ttl: float) -> None:
    if len(_cache) > 64:  # keys are bounded by terms and years; this is a backstop
        _cache.clear()
    _cache[key] = (time.monotonic() + ttl, value)


def _snapshot_block(term: str, pointer: dict, snap: dict, now: datetime) -> dict:
    verified = _ts(pointer.get("verified_at"))
    return {
        "term": term,
        "source_url": snap.get("source_url"),
        "page_last_updated": snap.get("page_last_updated"),
        "fetched_at": snap.get("fetched_at"),
        "verified_at": pointer.get("verified_at"),
        "stale": verified is None or now - verified > STALE_AFTER,
    }


def _ts(value) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def _pointer(source: str, scope: str) -> tuple[dict, dict] | None:
    """(pointer, snapshot row) for one (source, scope), or None. Cached."""
    key = (source, scope)
    hit, value = _cache_get(key)
    if hit:
        return value  # type: ignore[return-value]
    rows = await get_rows(
        "campus_current",
        {
            "select": "snapshot_id,verified_at",
            "source_key": f"eq.{source}",
            "scope_key": f"eq.{scope}",
            "limit": "1",
        },
    )
    if not rows:
        _cache_put(key, None, NEGATIVE_TTL_S)
        return None
    pointer = rows[0]
    snaps = await get_rows(
        "campus_snapshots",
        {
            "select": "id,source_url,page_last_updated,fetched_at",
            "id": f"eq.{pointer['snapshot_id']}",
            "limit": "1",
        },
    )
    if not snaps:
        # Pointer without a snapshot cannot happen under the FK; treat it as not loaded.
        _cache_put(key, None, NEGATIVE_TTL_S)
        return None
    value = (pointer, snaps[0])
    _cache_put(key, value, POINTER_TTL_S)
    return value


# -- Public API -----------------------------------------------------------------


def _ay_for_term(term_key: str) -> str:
    season, year = terms.split_term(term_key)
    return terms.ay_key(year if season == "fall" else year - 1)


async def _loaded_terms() -> list[tuple[str, dict, dict]]:
    key = ("registrar", "*")
    hit, value = _cache_get(key)
    if hit:
        return value  # type: ignore[return-value]
    pointers = await get_rows(
        "campus_current",
        {"select": "scope_key,snapshot_id,verified_at", "source_key": "eq.registrar"},
    )
    out: list[tuple[str, dict, dict]] = []
    if pointers:
        ids = ",".join(p["snapshot_id"] for p in pointers)
        snaps = {
            s["id"]: s
            for s in await get_rows(
                "campus_snapshots",
                {"select": "id,source_url,page_last_updated,fetched_at", "id": f"in.({ids})"},
            )
        }
        for p in pointers:
            if p["snapshot_id"] in snaps and terms.is_term_key(p["scope_key"]):
                out.append((p["scope_key"], p, snaps[p["snapshot_id"]]))
    _cache_put(key, out, POINTER_TTL_S)
    return out


def _resolved_block(term: str, how: str, loaded: set[str]) -> dict:
    return {"term": term, "label": term_label(term), "how": how, "loaded": term in loaded}


async def list_terms(today: date | None = None, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    today = today or pacific_today(now)
    with observability.stage("reg.query"):
        loaded = await _loaded_terms()
    names = {t for t, _, _ in loaded}
    this = resolve_term("this semester", today)
    nxt = resolve_term("next semester", today)
    observability.record("reg_term_resolution", this.how)
    return {
        "today": today.isoformat(),
        "terms": sorted(
            (
                {"term": t, "label": term_label(t), "snapshot": _snapshot_block(t, p, s, now)}
                for t, p, s in loaded
            ),
            key=lambda x: terms.term_window(x["term"])[0],
        ),
        "this": _resolved_block(this.term, this.how, names),  # type: ignore[arg-type]
        "next": _resolved_block(nxt.term, nxt.how, names),  # type: ignore[arg-type]
    }


def group_events(rows: list[dict], today: date) -> list[dict]:
    """One event per printed cell.

    The refresh stores one row per matched `event_key`, so a cell holding five
    deadlines is five identical rows apart from the key. Rows that agree on
    (category, label_raw, date_raw, start_date, end_date) are one event with
    their keys collected, in first-seen order.
    """
    grouped: dict[tuple, dict] = {}
    for r in rows:
        k = (r.get("category"), r.get("label_raw"), r.get("date_raw"), r.get("start_date"), r.get("end_date"))
        ev = grouped.get(k)
        if ev is None:
            start = _date(r.get("start_date"))
            last = _date(r.get("end_date")) or start
            ev = grouped[k] = {
                "category": r.get("category"),
                "label_raw": r.get("label_raw"),
                "date_raw": r.get("date_raw"),
                "start_date": r.get("start_date"),
                "end_date": r.get("end_date"),
                "event_keys": [],
                # An undated event cannot be called passed.
                "passed": last is not None and last < today,
            }
        key = r.get("event_key")
        if key and key not in ev["event_keys"]:
            ev["event_keys"].append(key)
    return sorted(
        grouped.values(), key=lambda e: (e["start_date"] is None, e["start_date"] or "")
    )


def _date(value) -> date | None:
    return date.fromisoformat(value) if value else None


def _in_window(row: dict, window: tuple[date, date]) -> bool:
    start = _date(row.get("start_date"))
    if start is None:
        return False
    end = _date(row.get("end_date")) or start
    return start <= window[1] and end >= window[0]


async def term_events(
    term: str, today: date | None = None, now: datetime | None = None
) -> dict:
    """Deadlines for one term: registrar rows, plus academic-calendar rows in its window."""
    if not terms.TERM_KEY_RE.match(term or ""):
        raise ValueError(f"not a term key: {term!r}")
    now = now or datetime.now(timezone.utc)
    today = today or pacific_today(now)
    out: dict = {"term": term, "label": term_label(term), "status": "not_loaded",
                 "snapshot": None, "academic_snapshot": None, "events": []}
    with observability.stage("reg.query"):
        reg = await _pointer("registrar", term)
        if reg is None:
            return out
        pointer, snap = reg
        rows = await get_rows(
            "reg_term_events",
            {
                "select": "category,label_raw,date_raw,start_date,end_date,event_key",
                "snapshot_id": f"eq.{pointer['snapshot_id']}",
                "order": "start_date.asc.nullslast",
                "limit": str(EVENT_LIMIT),
            },
        )
        academic = await _pointer("academic", _ay_for_term(term))
        academic_rows: list[dict] = []
        if academic is not None:
            # The nominal window is an approximation and SJSU's real term runs
            # past it at both ends: Fall 2026 starts Aug 19 (nominal Aug 20) and
            # Spring 2027's make-up day and commencement are May 26-28 (nominal
            # end May 25). Unwidened, those academic rows were silently dropped.
            start, end = terms.term_window(term)
            margin = timedelta(days=ACADEMIC_WINDOW_MARGIN_DAYS)
            window = (start - margin, end + margin)
            academic_rows = [
                r
                for r in await get_rows(
                    "reg_term_events",
                    {
                        "select": "category,label_raw,date_raw,start_date,end_date,event_key",
                        "snapshot_id": f"eq.{academic[0]['snapshot_id']}",
                        "order": "start_date.asc.nullslast",
                        "limit": str(EVENT_LIMIT),
                    },
                )
                if _in_window(r, window)
            ]
    out["status"] = "ok"
    out["snapshot"] = _snapshot_block(term, pointer, snap, now)
    if academic is not None:
        out["academic_snapshot"] = _snapshot_block(_ay_for_term(term), academic[0], academic[1], now)
    out["events"] = group_events(rows + academic_rows, today)
    verified = _ts(pointer.get("verified_at"))
    if verified:
        observability.record("reg_snapshot_age_h", int((now - verified).total_seconds() // 3600))
    return out
