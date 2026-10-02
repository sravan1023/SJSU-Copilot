"""Refresh SJSU's registration calendars into campus snapshots, failing closed.

    python -m campus.registration.refresh [--dry-run] [--fixture-dir DIR]
                                          [--source registrar|academic] [--term fall-2026]

An offline pipeline like `kb/`: it is never run inside a request, and it calls
`load_dotenv()` itself. This step covers the deadline sources (the registrar term
calendar and the academic calendar); the schedule and exam sources arrive in B2
and the bursar in C1, and slot into `plan_units`, `PARSERS` and `run_checks`.

For each (source, scope) unit, in order:

1. **Fetch.** robots.txt is checked, the per-host delay is kept (the larger of
   robots' crawl-delay and `KB_CRAWL_DELAY`), and the page comes through
   `kb.fetcher.conditional_get(max_bytes=8_000_000, extract=False)`, which walks
   redirects by hand and re-checks the host at every hop. `--fixture-dir` reads
   files instead, so the whole flow runs offline.
2. **Parse** on a worker thread (this is a CLI, not the server's blocking pool).
3. **Unchanged?** A page whose parsed content hashes the same as the current
   snapshot only re-verifies the pointer (`verified_at`) and stages nothing.
4. **Drift checks.** Header tuple, row floor, at least 70% of the previous
   snapshot's rows, weekday self-check, core `event_key`s, a complete (not
   truncated) fetch, a "Last Updated" line. Any failure stages nothing as
   current: a `rejected` snapshot records why, and readers stay where they were.
5. **Stage** a `staged` snapshot and batch-insert its rows, then confirm the row
   count.
6. **Flip** `campus_current` with a compare-and-swap (see `campus.store`). A lost
   race rejects our snapshot and prunes nothing.
7. **Supersede** the old snapshot, **prune** old unreferenced ones (never fatal).
8. A **run record** in `campus_refresh_runs`, with per-source stats.

A 404 for a term nobody has seen before is `not_posted` and quiet. A 404 for a
term we already hold is a failure. A refresh refuses to start if another one
began in the last 30 minutes.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import math
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv

from campus import dates, store, terms
from campus.registration import event_keys
from campus.registration.parse_calendar import (
    REGISTRAR_LAYOUTS,
    CalendarParse,
    parse_academic_calendar,
    parse_registrar_calendar,
)
from kb.fetcher import Outcome, conditional_get
from kb.robots import USER_AGENT, RobotsCache

logger = logging.getLogger(__name__)

MAX_BYTES = 8_000_000
DEFAULT_CRAWL_DELAY = 1.0

PARSERS = {
    "registrar": parse_registrar_calendar,
    "academic": parse_academic_calendar,
}
# Row floors. They allow a newly posted term to be partial; the 70% check is what
# protects a term we already hold.
MIN_ROWS = {"registrar": 10, "academic": 10}
PREVIOUS_FRACTION = 0.70
MAX_REJECTED_FRACTION = 0.25
MAX_WEEKDAY_REJECT_FRACTION = 0.10
# A registrar page's first day of instruction and finals must sit this close to
# the term's nominal start and end, or the page is another term's content (a
# copied placeholder, or a redirect we did not catch). Fall 2026 is 1 and 6 days
# off nominal; Spring 2027 is 2 and 6.
TERM_DATE_TOLERANCE_DAYS = 21


# -- Units --------------------------------------------------------------------


@dataclass(frozen=True)
class Unit:
    source: str
    scope: str

    @property
    def url(self) -> str:
        return terms.url_for(self.source, self.scope)

    @property
    def fixture_name(self) -> str:
        if self.source == "registrar":
            return f"registrar-calendar-{self.scope}.html"
        if self.source == "academic":
            return f"academic-calendar-{self.scope[3:]}.html"
        raise ValueError(self.source)


def ay_for_term(term_key: str) -> str:
    season, year = terms.split_term(term_key)
    return terms.ay_key(year if season == "fall" else year - 1)


def plan_units(today: date, *, source: str | None = None, term: str | None = None) -> list[Unit]:
    """The current term and the next two (decision 7: SJSU sends no ETag, so
    discovery stays shallow), plus the academic year they sit in."""
    if term:
        term_keys = [term]
    else:
        term_keys = [terms.current_term(today), *terms.next_terms(today, 2)]
    units = []
    if source in (None, "registrar"):
        units += [Unit("registrar", t) for t in term_keys]
    if source in (None, "academic"):
        units.extend(Unit("academic", ay) for ay in dict.fromkeys(ay_for_term(t) for t in term_keys))
    return units


# -- Hashing and drift checks ---------------------------------------------------


def content_hash(parsed: CalendarParse) -> str:
    """Hash of what was parsed, not of the HTML (which carries per-fetch noise)."""
    canonical = {
        "headers": [list(h) for h in parsed.headers],
        "last_updated": parsed.last_updated.isoformat() if parsed.last_updated else None,
        "rows": [
            [r["category"], r["label_raw"], r["date_raw"], r["start_date"].isoformat(),
             r["end_date"].isoformat(), r["event_key"]]
            for r in parsed.rows
        ],
        "rejected": [[r["label_raw"], r["date_raw"], r["reason"]] for r in parsed.rejected],
    }
    blob = json.dumps(canonical, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _weekday_mismatches(rows: list[dict]) -> list[dict]:
    """Rows whose stated weekday disagrees with the date we stored.

    The normaliser already refuses such a date, so this is an independent
    re-check of its output: it fails only if the normaliser has a bug.
    """
    bad = []
    for r in rows:
        try:
            items = dates.split_glued(r["date_raw"])
        except dates.DateReject:
            continue
        if not items or not dates.has_weekday_prefix(items[0]):
            continue
        for item, stored in ((items[0], r["start_date"]), (items[-1], r["end_date"])):
            stated = dates.leading_weekday(item)
            if stated is not None and stated != stored.weekday():
                bad.append(r)
                break
    return bad


def _term_dates_check(term_key: str, rows: list[dict]) -> tuple[bool, str]:
    """Is this page really `term_key`'s? Its first day of instruction must be near
    the term's nominal start and its finals near the nominal end. Without this, a
    fall page served at the spring URL parses cleanly inside spring's window."""
    nominal_start, nominal_end = terms.term_window(term_key)
    probes = (
        ("instruction_first_day", nominal_start),
        ("finals_period", nominal_end),
    )
    problems, seen = [], []
    for key, nominal in probes:
        starts = [r["start_date"] for r in rows if r["event_key"] == key]
        if not starts:
            continue  # core_keys reports a missing key
        off = min(abs((s - nominal).days) for s in starts)
        seen.append(f"{key} {off}d from nominal")
        if off > TERM_DATE_TOLERANCE_DAYS:
            problems.append(f"{key} is {off} days from {term_key}'s nominal {nominal.isoformat()}")
    if problems:
        return False, "; ".join(problems)
    return True, ", ".join(seen) if seen else "no anchor rows (see core_keys)"


def run_checks(
    unit: Unit, parsed: CalendarParse, *, truncated: bool, previous_rows: int | None
) -> dict[str, dict]:
    checks: dict[str, dict] = {}

    def add(name: str, ok: bool, detail: str) -> None:
        checks[name] = {"ok": bool(ok), "detail": detail}

    add("complete", not truncated, "fetch hit the byte cap" if truncated else "whole page read")

    if unit.source == "registrar":
        header_ok = bool(parsed.headers) and not parsed.unknown_headers and any(
            layout in parsed.headers for layout in REGISTRAR_LAYOUTS
        )
    else:
        header_ok = bool(parsed.headers) and not parsed.unknown_headers
    add(
        "header",
        header_ok,
        f"headers {[list(h) for h in parsed.headers]}"
        + (f", unknown {[list(h) for h in parsed.unknown_headers]}" if parsed.unknown_headers else ""),
    )

    floor = MIN_ROWS[unit.source]
    add("min_rows", len(parsed.rows) >= floor, f"{len(parsed.rows)} rows, floor {floor}")

    if previous_rows is None:
        add("rows_vs_previous", True, "no previous snapshot")
    else:
        need = math.ceil(previous_rows * PREVIOUS_FRACTION)
        add(
            "rows_vs_previous",
            len(parsed.rows) >= need,
            f"{len(parsed.rows)} rows against {previous_rows} before (need >= {need})",
        )

    total = parsed.data_rows or 1
    add(
        "rejected_rows",
        len(parsed.rejected) <= MAX_REJECTED_FRACTION * total,
        f"{len(parsed.rejected)} of {parsed.data_rows} rejected",
    )
    malformed = [r for r in parsed.rejected if r["reason"] in ("unexpected column count", "empty label")]
    add("row_shape", not malformed, f"{len(malformed)} malformed rows")

    weekday_rejects = [r for r in parsed.rejected if "weekday" in r["reason"]]
    mismatched = _weekday_mismatches(parsed.rows)
    add(
        "weekday",
        not mismatched and len(weekday_rejects) <= MAX_WEEKDAY_REJECT_FRACTION * total,
        f"{len(weekday_rejects)} weekday rejections, {len(mismatched)} stored rows disagree",
    )

    if unit.source == "registrar":
        missing = [k for k in event_keys.CORE_REGISTRAR_KEYS if k not in parsed.event_keys]
        add("core_keys", not missing, f"missing {missing}" if missing else "all present")
        ok, detail = _term_dates_check(unit.scope, parsed.rows)
        add("term_dates", ok, detail)

    add(
        "last_updated",
        parsed.last_updated is not None,
        parsed.last_updated.isoformat() if parsed.last_updated else "no 'Last Updated' line",
    )
    return checks


# -- Fetching -------------------------------------------------------------------


def crawl_delay() -> float:
    try:
        return float(os.getenv("KB_CRAWL_DELAY", "").strip() or DEFAULT_CRAWL_DELAY)
    except ValueError:
        return DEFAULT_CRAWL_DELAY


@dataclass
class Fetched:
    kind: str  # ok | not_found | skipped | error
    html: str | None = None
    detail: str | None = None
    fetched_from: str | None = None
    bytes: int = 0
    truncated: bool = False
    ms: int = 0


class Pacer:
    """One request at a time per host, `delay` seconds apart."""

    def __init__(self, sleep=asyncio.sleep):
        self._last: dict[str, float] = {}
        self._sleep = sleep

    async def wait(self, host: str, delay: float) -> None:
        last = self._last.get(host)
        if last is not None and delay > 0:
            remaining = delay - (time.monotonic() - last)
            if remaining > 0:
                await self._sleep(remaining)
        self._last[host] = time.monotonic()


async def fetch_unit(
    client: httpx.AsyncClient,
    robots: RobotsCache | None,
    pacer: Pacer,
    unit: Unit,
    *,
    fixture_dir: Path | None,
) -> Fetched:
    started = time.monotonic()
    if fixture_dir is not None:
        path = fixture_dir / unit.fixture_name
        if not path.is_file():
            return Fetched("not_found", detail=f"no fixture {path.name}")
        data = path.read_bytes()
        return Fetched(
            "ok",
            html=data.decode("utf-8", errors="replace"),
            fetched_from=str(path),
            bytes=len(data),
            ms=int((time.monotonic() - started) * 1000),
        )

    url = unit.url
    if not terms.host_allowed(url):  # a fixed template, but the allowlist is the contract
        return Fetched("skipped", detail="off_allowlist")
    rules = await robots.for_url(url)
    await pacer.wait(rules.host, max(rules.crawl_delay or 0.0, crawl_delay()))
    res = await conditional_get(
        client, url, rules=rules, allowed_hosts=list(terms.ALLOWED_HOSTS),
        max_bytes=MAX_BYTES, extract=False,
    )
    ms = int((time.monotonic() - started) * 1000)
    if res.outcome in (Outcome.ROBOTS_DENIED, Outcome.ROBOTS_UNAVAILABLE):
        return Fetched("skipped", detail=res.outcome.value, ms=ms)
    if res.outcome is Outcome.HTTP_4XX and res.status in (404, 410):
        return Fetched("not_found", detail=f"HTTP {res.status}", ms=ms)
    if not res.ok:
        return Fetched("error", detail=f"{res.outcome.value} {res.detail or res.status or ''}".strip(), ms=ms)
    if res.final_url and res.final_url.rstrip("/") != url.rstrip("/"):
        # A term page that redirects is not that term's page: an unposted term's
        # URL can land on another term or an index. Not posted for a term we
        # don't hold; process_unit fails it for one we do.
        return Fetched("not_found", detail=f"redirected to {res.final_url}", ms=ms)
    return Fetched(
        "ok",
        html=res.html,
        fetched_from=res.final_url or url,
        bytes=len((res.html or "").encode("utf-8")),
        truncated=res.truncated,
        ms=ms,
    )


# -- One unit -------------------------------------------------------------------


@dataclass
class UnitResult:
    source: str
    scope: str
    status: str = "failed"  # swapped | unchanged | not_posted | skipped | failed | dry_run
    reason: str | None = None
    bytes: int = 0
    truncated: bool = False
    fetch_ms: int = 0
    parse_ms: int = 0
    rows: int = 0
    rejected: int = 0
    failed_checks: list[str] = field(default_factory=list)
    swapped: bool = False
    notes: list[str] = field(default_factory=list)
    checks: dict = field(default_factory=dict)
    parsed: CalendarParse | None = None

    def stats(self) -> dict:
        return {
            "source": self.source, "scope": self.scope, "status": self.status,
            "reason": self.reason, "bytes": self.bytes, "truncated": self.truncated,
            "fetch_ms": self.fetch_ms, "parse_ms": self.parse_ms, "rows": self.rows,
            "rejected": self.rejected, "failed_checks": self.failed_checks,
            "swapped": self.swapped, "notes": self.notes,
        }


async def _reject_best_effort(client, snapshot_id: str | None, checks: dict, reason: str) -> None:
    if not snapshot_id:
        return
    try:
        await store.set_snapshot_status(
            client, snapshot_id, "rejected", checks={**checks, "outcome": {"ok": False, "detail": reason}}
        )
    except Exception as exc:  # the snapshot is unreferenced; pruning will find it
        logger.warning("could not mark snapshot %s rejected: %s", snapshot_id, exc)


async def process_unit(
    client: httpx.AsyncClient,
    robots: RobotsCache | None,
    pacer: Pacer,
    unit: Unit,
    *,
    dry_run: bool,
    fixture_dir: Path | None,
) -> UnitResult:
    out = UnitResult(unit.source, unit.scope)
    fetched = await fetch_unit(client, robots, pacer, unit, fixture_dir=fixture_dir)
    out.fetch_ms, out.bytes, out.truncated = fetched.ms, fetched.bytes, fetched.truncated

    if fetched.kind == "skipped":
        out.status, out.reason = "skipped", fetched.detail
        return out
    if fetched.kind == "error":
        out.status, out.reason = "failed", f"fetch: {fetched.detail}"
        return out
    if fetched.kind == "not_found":
        existing = None if dry_run else await store.get_current(client, unit.source, unit.scope)
        if existing:
            out.status, out.reason = "failed", "page gone: a term we hold now answers 404"
        else:
            out.status, out.reason = "not_posted", fetched.detail
        return out

    started = time.monotonic()
    parsed = await asyncio.to_thread(PARSERS[unit.source], fetched.html, unit.scope)
    out.parse_ms = int((time.monotonic() - started) * 1000)
    out.parsed = parsed
    out.rows, out.rejected = len(parsed.rows), len(parsed.rejected)
    digest = content_hash(parsed)

    previous = None if dry_run else await store.get_current(client, unit.source, unit.scope)
    previous_snapshot = (previous or {}).get("snapshot") or {}
    previous_id = previous["snapshot_id"] if previous else None

    checks = run_checks(
        unit, parsed, truncated=fetched.truncated, previous_rows=previous_snapshot.get("row_count")
    )
    out.checks = checks
    out.failed_checks = [n for n, c in checks.items() if not c["ok"]]

    if previous and previous_snapshot.get("content_hash") == digest and not out.failed_checks:
        if await store.touch_verified(client, unit.source, unit.scope, previous_id):
            out.status = "unchanged"
        else:
            out.status, out.reason = "failed", "lost_race"
        return out

    if dry_run:
        out.status = "failed" if out.failed_checks else "dry_run"
        out.reason = "drift: " + ", ".join(out.failed_checks) if out.failed_checks else None
        return out

    header = [list(h) for h in parsed.headers]
    common = dict(
        source=unit.source, scope=unit.scope, source_url=unit.url,
        fetched_from=fetched.fetched_from, page_last_updated=parsed.last_updated,
        content_hash=digest, header=header,
    )

    if out.failed_checks:
        out.status, out.reason = "failed", "drift: " + ", ".join(out.failed_checks)
        try:  # a rejected snapshot records why; it has no rows and never becomes current
            await store.stage_snapshot(client, row_count=0, checks=checks, status="rejected", **common)
        except (store.StoreError, httpx.HTTPError) as exc:
            out.notes.append(f"could not record the rejected snapshot: {exc}")
        return out

    snapshot_id = None
    try:
        snapshot_id = await store.stage_snapshot(
            client, row_count=len(parsed.rows), checks=checks, **common
        )
        await store.insert_events(client, snapshot_id, parsed.rows)
        staged = await store.count_events(client, snapshot_id)
        if staged != len(parsed.rows):
            raise store.StoreError(f"staged {staged} rows, expected {len(parsed.rows)}")
    except (store.StoreError, httpx.HTTPError) as exc:
        await _reject_best_effort(client, snapshot_id, checks, f"{type(exc).__name__}: {exc}")
        out.status, out.reason = "failed", f"store: {str(exc)[:200]}"
        return out

    try:
        won = await store.flip(client, unit.source, unit.scope, snapshot_id, previous_id)
    except (store.StoreError, httpx.HTTPError) as exc:
        # A timeout can arrive after the server committed the flip. Leave the
        # snapshot intact: only a confirmed lost CAS may reject it. Readers
        # continue using the pointer, even while status is still staged.
        out.status, out.reason = "failed", f"publication unconfirmed: {str(exc)[:200]}"
        return out

    if not won:
        await _reject_best_effort(client, snapshot_id, checks, "lost_race")
        out.status, out.reason = "failed", "lost_race"
        return out

    out.status, out.swapped = "swapped", True
    # From here the pointer is the truth. Nothing below may fail the refresh.
    try:
        await store.set_snapshot_status(client, snapshot_id, "current")
        if previous_id:
            await store.set_snapshot_status(client, previous_id, "superseded")
    except (store.StoreError, httpx.HTTPError) as exc:
        out.notes.append(f"status update after the flip failed: {str(exc)[:120]}")
    try:
        pruned = await store.prune(client, unit.source, unit.scope)
        out.notes.append(f"pruned {pruned}")
    except store.PruneSkipped:
        out.notes.append("prune skipped (RESTRICT, 23503)")
    except (store.StoreError, httpx.HTTPError) as exc:
        out.notes.append(f"prune failed: {str(exc)[:120]}")
    return out


# -- The run --------------------------------------------------------------------


@dataclass
class RunReport:
    outcome: str  # success | partial | failed | busy
    results: list[UnitResult] = field(default_factory=list)
    run_id: str | None = None
    error: str | None = None

    def stats(self) -> dict:
        return {"units": [r.stats() for r in self.results]}


def run_outcome(results: list[UnitResult]) -> str:
    bad = [r for r in results if r.status in ("failed", "skipped")]
    good = [r for r in results if r.status in ("swapped", "unchanged", "dry_run")]
    if not bad:
        return "success"
    return "partial" if good else "failed"


async def run_refresh(
    client: httpx.AsyncClient,
    units: list[Unit],
    *,
    dry_run: bool = False,
    fixture_dir: Path | None = None,
    sleep=asyncio.sleep,
) -> RunReport:
    run_id = None
    if not dry_run:
        if await store.other_refresh_running(client):
            return RunReport("busy", error="another refresh started within the last 30 minutes")
        run_id = await store.start_run(client)

    robots = None if fixture_dir is not None else RobotsCache(client)
    pacer = Pacer(sleep)
    results: list[UnitResult] = []
    error = None
    finished = False
    try:
        for unit in units:
            try:
                results.append(
                    await process_unit(
                        client, robots, pacer, unit, dry_run=dry_run, fixture_dir=fixture_dir
                    )
                )
            except Exception as exc:  # one unit's bug must not hide the others' results
                logger.exception("refresh of %s/%s crashed", unit.source, unit.scope)
                res = UnitResult(unit.source, unit.scope, "failed", f"unexpected: {type(exc).__name__}: {exc}")
                results.append(res)
        finished = True
    finally:
        # Interrupted (Ctrl-C, cancellation) before every unit ran: that is a
        # failure, never a success computed from the units that happened to finish.
        outcome = run_outcome(results) if finished else "failed"
        bad = [f"{r.source}/{r.scope}: {r.reason}" for r in results if r.status in ("failed", "skipped")]
        if not finished:
            bad.append(f"interrupted after {len(results)} of {len(units)} units")
        error = "; ".join(bad)[:2000] or None
        if run_id:
            await store.finish_run(client, run_id, outcome, {"units": [r.stats() for r in results]}, error)
    return RunReport(outcome, results, run_id, error)


# -- Output ---------------------------------------------------------------------


def _fmt_range(r: dict) -> str:
    s, e = r["start_date"], r["end_date"]
    return s.isoformat() if s == e else f"{s.isoformat()} .. {e.isoformat()}"


def print_dry_run(report: RunReport) -> None:
    for res in report.results:
        print(f"\n== {res.source} {res.scope}  [{res.status}]" + (f"  {res.reason}" if res.reason else ""))
        if res.parsed is None:
            continue
        p = res.parsed
        print(f"   bytes {res.bytes}  truncated {res.truncated}  fetch {res.fetch_ms} ms  "
              f"parse {res.parse_ms} ms  last updated {p.last_updated}")
        print(f"   {len(p.rows)} rows, {len(p.rejected)} rejected")
        cells: dict[tuple[str, str], list[dict]] = {}
        for r in p.rows:
            cells.setdefault((r["date_raw"], r["label_raw"]), []).append(r)
        print(f"   {'date':<25} {'keys':<60} label")
        for (_, label), rows in cells.items():
            keys = ",".join(r["event_key"] or "-" for r in rows)
            print(f"   {_fmt_range(rows[0]):<25} {keys[:60]:<60} {label[:50]}")
        for rej in p.rejected:
            print(f"   REJECTED {rej['date_raw']!r}: {rej['reason']}  ({rej['label_raw'][:50]})")
        print("   drift checks:")
        for name, c in res.checks.items():
            print(f"     {'ok  ' if c['ok'] else 'FAIL'} {name:<17} {c['detail']}")


def print_summary(report: RunReport) -> None:
    print(f"\nrefresh {report.outcome}" + (f": {report.error}" if report.error else ""))
    for r in report.results:
        extra = f" ({r.reason})" if r.reason else ""
        print(f"  {r.source:<9} {r.scope:<14} {r.status:<10} rows {r.rows:<3} "
              f"rejected {r.rejected:<2} {' '.join(r.notes)}{extra}")


async def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh SJSU registration calendars.")
    parser.add_argument("--dry-run", action="store_true", help="fetch and parse, write nothing")
    parser.add_argument("--fixture-dir", type=Path, help="read pages from files, not the network")
    parser.add_argument("--source", choices=sorted(PARSERS), help="limit to one source")
    parser.add_argument("--term", help="limit to one term, e.g. fall-2026")
    parser.add_argument("--today", help="override today's date (YYYY-MM-DD), for testing")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    load_dotenv()  # a CLI has to do what main.py does for the server
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    if args.term and not terms.is_term_key(args.term):
        print(f"--term must look like fall-2026, got {args.term!r}")
        return 2
    if args.fixture_dir and not args.dry_run:
        # A fixture is a saved copy, not SJSU's current page; writing it would
        # publish stale content to live with a fresh fetched_at.
        print("--fixture-dir is for testing and requires --dry-run")
        return 2
    try:
        today = date.fromisoformat(args.today) if args.today else datetime.now(ZoneInfo("America/Los_Angeles")).date()
    except ValueError:
        parser.error("--today must be a valid YYYY-MM-DD date")
    units = plan_units(today, source=args.source, term=args.term)

    if not args.dry_run and not store.configured():
        print("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set (or use --dry-run)")
        return 2

    async with httpx.AsyncClient(follow_redirects=False, headers={"User-Agent": USER_AGENT}) as client:
        report = await run_refresh(
            client, units, dry_run=args.dry_run, fixture_dir=args.fixture_dir
        )

    if report.outcome == "busy":
        print(f"not started: {report.error}")
        return 3
    if args.dry_run:
        print_dry_run(report)
    print_summary(report)
    return 1 if report.outcome in ("failed", "partial") else 0


def _cli() -> int:
    try:
        return asyncio.run(_main())
    except store.StoreError as exc:
        print(f"\n{exc}\n")
        if any(m in str(exc) for m in ("42703", "PGRST205", "404", "does not exist")):
            print("The campus tables may not be on this project (20261001000100).")
        return 1


if __name__ == "__main__":
    sys.exit(_cli())
