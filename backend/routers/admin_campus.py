"""Operator routes for the campus refresh.

Gated like `admin_kb.py`: `run_ingestion` in `public.admin_grants`, 403 for a
guest or a missing grant, 503 when grants are unreadable.

**The refresh never runs in this process.** Parsing the schedule page costs
seconds of CPU and 100+ MB (SERVICES_BUILD_PLAN F1), which on the request event
loop would stall every chat stream. It runs as `python -m
campus.registration.refresh` in a child process, and this route returns 202.

**Single flight, twice.** An in-process guard (409) stops a double click; the
CLI's own database guard (`campus_refresh_runs`, exit code 3 = busy) covers a
second backend worker or a cron run. Neither is the other's substitute.

Argv is built from validated values only: `--source` from a fixed enum and
`--term` from `TERM_KEY_RE`. The child's output is discarded, not piped (an
unread pipe would fill and block it); the outcome is in `campus_refresh_runs`.
"""
import logging
import subprocess
import sys
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

import observability
import runtime
from auth import require_capability
from campus import terms
from services import registration

logger = logging.getLogger(__name__)
router = APIRouter(tags=["campus"])

RUN_INGESTION = Depends(require_capability("run_ingestion"))

BACKEND_DIR = Path(__file__).resolve().parent.parent
RECENT_RUNS = 10

_child = None  # the live Popen, or None
_spawning = False  # set before the await so two POSTs can't both pass the check


class RefreshRequest(BaseModel):
    source: Literal["registrar", "academic"] | None = None
    term: str | None = Field(default=None, max_length=32)

    # Validated with the same Python regex the CLI uses, not a Field `pattern`:
    # pydantic compiles `pattern` with its Rust engine, which rejects the `\Z`
    # in TERM_KEY_RE, and one shared definition beats two that can drift.
    @field_validator("term")
    @classmethod
    def _term_key(cls, v: str | None) -> str | None:
        if v is not None and not terms.is_term_key(v):
            raise ValueError("term must look like fall-2026")
        return v


def build_argv(source: str | None, term: str | None) -> list[str]:
    argv = [sys.executable, "-m", "campus.registration.refresh"]
    if source:
        argv += ["--source", source]
    if term:
        argv += ["--term", term]
    return argv


def _spawn(argv: list[str], cwd: str):
    return subprocess.Popen(
        argv,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _running() -> bool:
    """Whether the last child is alive. Polling also reaps it and logs its exit."""
    global _child
    if _child is None:
        return False
    code = _child.poll()
    if code is None:
        return True
    logger.info(
        "campus refresh exited",
        extra={"request_id": observability.current_request_id(), "exit_code": code,
               "busy": code == 3},
    )
    _child = None
    return False


@router.post("/admin/campus/refresh", status_code=202, dependencies=[RUN_INGESTION])
async def trigger_refresh(req: RefreshRequest = RefreshRequest()):
    global _child, _spawning
    if _spawning or _running():
        raise HTTPException(status_code=409, detail="a campus refresh is already running")
    argv = build_argv(req.source, req.term)
    _spawning = True
    try:
        _child = await runtime.run_blocking(_spawn, argv, str(BACKEND_DIR))
    except OSError as exc:
        logger.error("campus refresh could not start", extra={"error": type(exc).__name__})
        raise HTTPException(status_code=503, detail="could not start the refresh")
    finally:
        _spawning = False
    logger.info(
        "campus refresh started",
        extra={"request_id": observability.current_request_id(), "pid": _child.pid,
               "source": req.source, "term": req.term},
    )
    return {
        "ok": True,
        "pid": _child.pid,
        "message": "refresh started; check GET /api/admin/campus/status for the outcome",
    }


@router.get("/admin/campus/status", dependencies=[RUN_INGESTION])
async def campus_status():
    try:
        runs = await registration.get_rows(
            "campus_refresh_runs",
            {
                "select": "id,started_at,finished_at,outcome,stats,error",
                "order": "started_at.desc",
                "limit": str(RECENT_RUNS),
            },
        )
        pointers = await registration.get_rows(
            "campus_current", {"select": "source_key,scope_key,snapshot_id,verified_at"}
        )
        snaps: dict[str, dict] = {}
        if pointers:
            ids = ",".join(p["snapshot_id"] for p in pointers)
            snaps = {
                s["id"]: s
                for s in await registration.get_rows(
                    "campus_snapshots",
                    {"select": "id,page_last_updated,row_count,fetched_at", "id": f"in.({ids})"},
                )
            }
    except registration.RegistrationUnavailable as exc:
        logger.warning("campus status unavailable", extra={"reason": str(exc)})
        raise HTTPException(status_code=503, detail="campus tables are unavailable")

    return {
        "refresh_running": _spawning or _running(),
        "runs": runs,
        "current": [
            {
                "source_key": p["source_key"],
                "scope_key": p["scope_key"],
                "verified_at": p["verified_at"],
                "page_last_updated": snaps.get(p["snapshot_id"], {}).get("page_last_updated"),
                "row_count": snaps.get(p["snapshot_id"], {}).get("row_count"),
                "fetched_at": snaps.get(p["snapshot_id"], {}).get("fetched_at"),
            }
            for p in sorted(pointers, key=lambda p: (p["source_key"], p["scope_key"]))
        ],
    }
