"""PostgREST writes for the campus snapshot tables (20261001000100).

Mirrors `kb/store.py`: there is no Postgres driver in this repo, so every access
is PostgREST with the service key, and a failed request raises `StoreError`.

The shape of a refresh, and what this module guarantees about it:

1. A snapshot is inserted as `staged`, then its rows go in in batches. Nothing
   reads a staged snapshot: readers resolve through `campus_current` only.
2. **The flip is a compare-and-swap.** `flip` updates `campus_current` only
   where it still names the snapshot this refresh read at the start. One row
   back means we won. Zero means another refresh got there first, and the caller
   rejects its own snapshot and does not prune. The first time for a (source,
   scope) there is no pointer to compare, so it is an insert, and a unique
   violation (409) is the same lost race.
3. **Prune by age and pointer, never by status.** `status` can lag the pointer: a
   crash between the flip and the status update leaves the old snapshot at
   `current` with nothing pointing at it, and a status filter would keep it for
   ever (about 7,000 rows each time, for the schedule). `prune` deletes snapshots
   that `campus_current` does not name and that are older than 24 h, whatever
   their status, and keeps the newest one that is worth rolling back to.
   `campus_current_snapshot_fkey` is ON DELETE RESTRICT, so a prune that did hit
   the current snapshot would fail whole with 23503 and delete nothing; the
   caller treats that as "skip the prune this run".
4. **Values stay inside the CHECK constraints** (source, scope, status, category,
   outcome, date order); `_check_*` raises before the request is sent.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

from campus import terms

logger = logging.getLogger(__name__)

HTTP_TIMEOUT = 30.0
BATCH_SIZE = 1000

SNAPSHOT_STATUSES = ("staged", "current", "superseded", "rejected")
RUN_OUTCOMES = ("running", "success", "partial", "failed")
EVENT_CATEGORIES = ("registrar", "academic", "payment")

SINGLE_FLIGHT_MINUTES = 30
PRUNE_MIN_AGE_HOURS = 24


class StoreError(RuntimeError):
    """A write failed. `status` and `code` carry PostgREST's answer when there was one."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


def _base_url() -> str:
    url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    if not url:
        raise StoreError("SUPABASE_URL is not set")
    return url


def _service_key() -> str:
    key = os.getenv("SUPABASE_SERVICE_KEY", "").strip()
    if not key:
        # No table here grants anything to anon (20261001000100 section 7).
        raise StoreError("SUPABASE_SERVICE_KEY is not set")
    return key


def _headers(prefer: str | None = None, extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {
        "apikey": _service_key(),
        "Authorization": f"Bearer {_service_key()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    if extra:
        headers.update(extra)
    return headers


def configured() -> bool:
    return bool(os.getenv("SUPABASE_URL", "").strip()) and bool(
        os.getenv("SUPABASE_SERVICE_KEY", "").strip()
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: date | datetime | str | None) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else value.isoformat()


def _error(res: httpx.Response, method: str, path: str) -> StoreError:
    code = None
    try:
        body = res.json()
        code = body.get("code") if isinstance(body, dict) else None
    except ValueError:
        pass
    return StoreError(
        f"{method} {path} -> {res.status_code} {res.text[:300]}", status=res.status_code, code=code
    )


async def _request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    params: dict | list | None = None,
    json_body: Any = None,
    prefer: str | None = None,
) -> Any:
    res = await client.request(
        method,
        f"{_base_url()}/rest/v1/{path}",
        params=params,
        json=json_body,
        headers=_headers(prefer),
        timeout=HTTP_TIMEOUT,
    )
    if res.status_code >= 400:
        raise _error(res, method, path)
    if res.status_code == 204 or not res.content:
        return None
    try:
        return res.json()
    except ValueError:
        return None


# -- Value checks (the CHECK constraints, ahead of the request) -----------------


def _check_scope(source: str, scope: str) -> None:
    if source not in terms.SOURCES:
        raise StoreError(f"source_key {source!r} is not one of {terms.SOURCES}")
    if not terms.SCOPE_KEY_RE.fullmatch(scope or ""):
        raise StoreError(f"scope_key {scope!r} would fail campus_snapshots_scope_key_check")
    if (source == "academic" and not terms.is_ay_key(scope)) or (
        source != "academic" and not terms.is_term_key(scope)
    ):
        raise StoreError(f"scope_key {scope!r} does not belong to source {source!r}")


def _event_row(snapshot_id: str, row: dict) -> dict:
    category = row.get("category")
    if category not in EVENT_CATEGORIES:
        raise StoreError(f"category {category!r} would fail reg_term_events_category_check")
    if not row.get("label_raw") or row.get("date_raw") is None:
        raise StoreError("label_raw and date_raw are required")
    start, end = row.get("start_date"), row.get("end_date")
    if end is not None and (start is None or end < start):
        raise StoreError(
            f"end {end} before start {start} would fail reg_term_events_date_order_check"
        )
    # Every key on every row, None included: PostgREST rejects a bulk insert whose
    # objects do not all carry the same ones (PGRST102).
    return {
        "snapshot_id": snapshot_id,
        "category": category,
        "label_raw": row["label_raw"],
        "date_raw": row["date_raw"],
        "start_date": _iso(start),
        "end_date": _iso(end),
        "event_key": row.get("event_key"),
    }


# -- Pointers and snapshots -------------------------------------------------------


async def get_current(client: httpx.AsyncClient, source: str, scope: str) -> dict | None:
    """The pointer row with the snapshot it names under `snapshot` (hash, row count).

    Two plain GETs rather than a PostgREST embed: the foreign key is composite,
    and an embed across it is one more thing that has never run against the real
    schema.
    """
    rows = await _request(
        client,
        "GET",
        "campus_current",
        params={
            "select": "source_key,scope_key,snapshot_id,verified_at",
            "source_key": f"eq.{source}",
            "scope_key": f"eq.{scope}",
            "limit": "1",
        },
    )
    if not rows:
        return None
    pointer = dict(rows[0])
    snaps = await _request(
        client,
        "GET",
        "campus_snapshots",
        params={
            "select": "id,content_hash,row_count,status,page_last_updated,fetched_at",
            "id": f"eq.{pointer['snapshot_id']}",
            "limit": "1",
        },
    )
    pointer["snapshot"] = snaps[0] if snaps else None
    return pointer


async def stage_snapshot(
    client: httpx.AsyncClient,
    *,
    source: str,
    scope: str,
    source_url: str,
    fetched_from: str | None,
    page_last_updated: date | None,
    content_hash: str,
    row_count: int,
    header: Any,
    checks: dict,
    status: str = "staged",
) -> str:
    _check_scope(source, scope)
    if status not in SNAPSHOT_STATUSES:
        raise StoreError(f"status {status!r} would fail campus_snapshots_status_check")
    if row_count < 0:
        raise StoreError("row_count must be >= 0")
    rows = await _request(
        client,
        "POST",
        "campus_snapshots",
        json_body=[
            {
                "source_key": source,
                "scope_key": scope,
                "source_url": source_url,
                "fetched_from": fetched_from,
                "page_last_updated": _iso(page_last_updated),
                "content_hash": content_hash,
                "row_count": row_count,
                "header": header,
                "checks": checks,
                "status": status,
            }
        ],
        prefer="return=representation",
    )
    if not rows or not rows[0].get("id"):
        raise StoreError("snapshot insert returned no id")
    return rows[0]["id"]


async def insert_events(
    client: httpx.AsyncClient, snapshot_id: str, rows: list[dict], *, batch_size: int | None = None
) -> int:
    """Insert term events in batches. Any failure raises, and the caller must not flip."""
    batch_size = batch_size or BATCH_SIZE
    payload = [_event_row(snapshot_id, r) for r in rows]  # validate everything first
    for i in range(0, len(payload), batch_size):
        await _request(
            client,
            "POST",
            "reg_term_events",
            json_body=payload[i : i + batch_size],
            prefer="return=minimal",
        )
    return len(payload)


async def count_events(client: httpx.AsyncClient, snapshot_id: str) -> int:
    """How many reg_term_events rows a snapshot has, by `Prefer: count=exact`."""
    res = await client.request(
        "GET",
        f"{_base_url()}/rest/v1/reg_term_events",
        params={"select": "id", "snapshot_id": f"eq.{snapshot_id}"},
        headers=_headers("count=exact", {"Range-Unit": "items", "Range": "0-0"}),
        timeout=HTTP_TIMEOUT,
    )
    if res.status_code >= 400:
        raise _error(res, "GET", "reg_term_events")
    total = (res.headers.get("content-range") or "").rsplit("/", 1)[-1]
    if not total.isdigit():
        raise StoreError(f"no row count in content-range {res.headers.get('content-range')!r}")
    return int(total)


async def set_snapshot_status(
    client: httpx.AsyncClient, snapshot_id: str, status: str, *, checks: dict | None = None
) -> None:
    if status not in SNAPSHOT_STATUSES:
        raise StoreError(f"status {status!r} would fail campus_snapshots_status_check")
    body: dict[str, Any] = {"status": status}
    if checks is not None:
        body["checks"] = checks
    await _request(
        client,
        "PATCH",
        "campus_snapshots",
        params={"id": f"eq.{snapshot_id}"},
        json_body=body,
        prefer="return=minimal",
    )


async def flip(
    client: httpx.AsyncClient,
    source: str,
    scope: str,
    new_snapshot_id: str,
    previous_snapshot_id: str | None,
) -> bool:
    """Point (source, scope) at `new_snapshot_id`, only if nobody has moved it.

    True: we won, readers now see the new snapshot. False: we lost the race.
    """
    _check_scope(source, scope)
    if previous_snapshot_id is None:
        try:
            await _request(
                client,
                "POST",
                "campus_current",
                json_body=[
                    {
                        "source_key": source,
                        "scope_key": scope,
                        "snapshot_id": new_snapshot_id,
                        "verified_at": _now().isoformat(),
                    }
                ],
                prefer="return=minimal",
            )
        except StoreError as exc:
            # Only a unique violation on (source_key, scope_key) means another
            # refresh inserted the pointer first. Any other 409 (a 23503 foreign
            # key violation, say) is a real error, and the caller treats it as
            # "publication unconfirmed" rather than rejecting our snapshot.
            if exc.code == "23505":
                return False
            raise
        return True

    rows = await _request(
        client,
        "PATCH",
        "campus_current",
        params={
            "source_key": f"eq.{source}",
            "scope_key": f"eq.{scope}",
            "snapshot_id": f"eq.{previous_snapshot_id}",
        },
        json_body={"snapshot_id": new_snapshot_id, "verified_at": _now().isoformat()},
        prefer="return=representation",
    )
    return bool(rows) and len(rows) == 1


async def touch_verified(
    client: httpx.AsyncClient, source: str, scope: str, snapshot_id: str
) -> bool:
    """Record that an unchanged page was re-checked. False if the pointer moved."""
    rows = await _request(
        client,
        "PATCH",
        "campus_current",
        params={
            "source_key": f"eq.{source}",
            "scope_key": f"eq.{scope}",
            "snapshot_id": f"eq.{snapshot_id}",
        },
        json_body={"verified_at": _now().isoformat()},
        prefer="return=representation",
    )
    return bool(rows)


class PruneSkipped(Exception):
    """The prune was refused (RESTRICT, 23503). Not a failed refresh."""


def plan_prune(
    snapshots: list[dict], current_ids: set[str], *, now: datetime, min_age_hours: int = PRUNE_MIN_AGE_HOURS
) -> list[str]:
    """Which snapshot ids to delete. Pure, so the rule is testable on its own.

    A snapshot goes if `campus_current` does not name it and it is older than the
    cut-off, whatever its `status`. The newest non-pointer snapshot that was ever
    good (`superseded`, or a `current` orphan left by a crash) is kept so there is
    something to roll back to. A `rejected` or `staged` one is no rollback
    target; it goes by age alone, which also spares a staged snapshot another
    refresh is still filling.
    """
    cutoff = now - timedelta(hours=min_age_hours)
    others = sorted(
        (s for s in snapshots if s["id"] not in current_ids),
        key=lambda s: s["created_at"],
        reverse=True,
    )
    keep = next((s["id"] for s in others if s.get("status") in ("superseded", "current")), None)
    return [
        s["id"]
        for s in others
        if s["id"] != keep and _parse_ts(s["created_at"]) < cutoff
    ]


def _parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def prune(
    client: httpx.AsyncClient,
    source: str,
    scope: str,
    *,
    now: datetime | None = None,
    min_age_hours: int = PRUNE_MIN_AGE_HOURS,
) -> int:
    """Delete old, unreferenced snapshots of (source, scope). Returns how many.

    Raises `PruneSkipped` on 23503: the database refused because something still
    names a snapshot we selected (a stale read), and deleted nothing.
    """
    now = now or _now()
    current = await _request(
        client,
        "GET",
        "campus_current",
        params={"select": "snapshot_id", "source_key": f"eq.{source}", "scope_key": f"eq.{scope}"},
    ) or []
    snapshots = await _request(
        client,
        "GET",
        "campus_snapshots",
        params={
            "select": "id,status,created_at",
            "source_key": f"eq.{source}",
            "scope_key": f"eq.{scope}",
            "order": "created_at.desc",
        },
    ) or []
    doomed = plan_prune(
        snapshots, {c["snapshot_id"] for c in current}, now=now, min_age_hours=min_age_hours
    )
    if not doomed:
        return 0
    try:
        await _request(
            client,
            "DELETE",
            "campus_snapshots",
            params={"id": f"in.({','.join(doomed)})"},
            prefer="return=minimal",
        )
    except StoreError as exc:
        if exc.code == "23503" or "23503" in str(exc):
            raise PruneSkipped(str(exc)) from exc
        raise
    return len(doomed)


# -- Run records and the single-flight guard ------------------------------------


async def other_refresh_running(
    client: httpx.AsyncClient, *, minutes: int = SINGLE_FLIGHT_MINUTES
) -> bool:
    """True if a run is `running` and started within the last `minutes`.

    An older `running` row is a crashed run and does not block.
    """
    since = (_now() - timedelta(minutes=minutes)).isoformat()
    rows = await _request(
        client,
        "GET",
        "campus_refresh_runs",
        params={
            "select": "id",
            "outcome": "eq.running",
            "started_at": f"gte.{since}",
            "limit": "1",
        },
    )
    return bool(rows)


async def start_run(client: httpx.AsyncClient) -> str:
    rows = await _request(
        client,
        "POST",
        "campus_refresh_runs",
        json_body=[{"outcome": "running"}],
        prefer="return=representation",
    )
    if not rows or not rows[0].get("id"):
        raise StoreError("run insert returned no id")
    return rows[0]["id"]


async def finish_run(
    client: httpx.AsyncClient,
    run_id: str | None,
    outcome: str,
    stats: dict,
    error: str | None = None,
) -> None:
    if not run_id:
        return
    if outcome not in RUN_OUTCOMES or outcome == "running":
        raise StoreError(f"outcome {outcome!r} would fail campus_refresh_runs_outcome_check")
    try:
        await _request(
            client,
            "PATCH",
            "campus_refresh_runs",
            params={"id": f"eq.{run_id}"},
            json_body={
                "outcome": outcome,
                "stats": stats,
                "error": (error[:2000] if error else None),
                "finished_at": _now().isoformat(),
            },
            prefer="return=minimal",
        )
    except (StoreError, httpx.HTTPError) as exc:
        # Never let closing the run record raise out of the refresh's `finally`:
        # that would lose the report. A run left 'running' stops blocking new
        # refreshes after 30 minutes.
        logger.warning("could not close refresh run %s: %s", run_id, exc)
