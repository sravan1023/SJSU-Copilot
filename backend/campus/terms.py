"""Term keys, the fixed URLs the refresh may fetch, and nominal term windows.

Pure functions, no I/O.

**Only fixed templates on www.sjsu.edu.** A refresh never builds a URL from page
content, and the host allowlist has exactly one entry. `gcp-web` (an internal
hostname that shows up in some of SJSU's markup) is never used.

A *scope key* is what `campus_snapshots.scope_key` stores. It is a term
(`fall-2026`) or, for the academic calendar, which SJSU publishes per academic
year, a year span (`ay-2026-2027`). `SCOPE_KEY_RE` repeats the migration's check
constraint, so a bad key fails here with a message instead of in the insert.
"""
from __future__ import annotations

import re
from datetime import date
from urllib.parse import urlsplit

ALLOWED_HOSTS = ("www.sjsu.edu",)
BASE = "https://www.sjsu.edu"

SEASONS = ("spring", "summer", "fall", "winter")

# `\Z`, not `$`: Python's `$` also matches before a trailing newline, Postgres's
# does not, and these keys are interpolated into fixed URLs.
TERM_KEY_RE = re.compile(r"^(spring|summer|fall|winter)-20[0-9]{2}\Z")
AY_KEY_RE = re.compile(r"^ay-20[0-9]{2}-20[0-9]{2}\Z")
# The same set as campus_snapshots_scope_key_check in 20261001000100.
SCOPE_KEY_RE = re.compile(
    r"^((spring|summer|fall|winter)-20[0-9]{2}|ay-20[0-9]{2}-20[0-9]{2})\Z"
)

SOURCES = ("schedule", "registrar", "academic", "exams", "bursar")


def is_term_key(key: str) -> bool:
    return bool(TERM_KEY_RE.fullmatch(key or ""))


def is_ay_key(key: str) -> bool:
    """`ay-2026-2027`: the pattern, and the second year is the first plus one."""
    if not AY_KEY_RE.fullmatch(key or ""):
        return False
    return int(key[8:12]) == int(key[3:7]) + 1


def split_term(term_key: str) -> tuple[str, int]:
    if not is_term_key(term_key):
        raise ValueError(f"not a term key: {term_key!r}")
    season, year = term_key.split("-")
    return season, int(year)


def ay_key(start_year: int) -> str:
    return f"ay-{start_year}-{start_year + 1}"


def ay_years(key: str) -> tuple[int, int]:
    if not is_ay_key(key):
        raise ValueError(f"not an academic-year key: {key!r}")
    return int(key[3:7]), int(key[8:12])


# -- URLs ---------------------------------------------------------------------


def url_for(source: str, scope_key: str) -> str:
    """The one URL a (source, scope) pair is fetched from."""
    if source == "registrar":
        season, year = split_term(scope_key)
        return f"{BASE}/registrar/calendar/{season}-{year}.php"
    if source == "academic":
        start, end = ay_years(scope_key)
        return f"{BASE}/classes/calendar/{start}-{end}.php"
    if source == "schedule":
        split_term(scope_key)
        return f"{BASE}/classes/schedules/{scope_key}.php"
    if source == "exams":
        split_term(scope_key)
        return f"{BASE}/classes/final-exam-schedule/{scope_key}.php"
    if source == "bursar":
        # The bursar's page carries a season and no year; the year is read from
        # the page itself (SERVICES_BUILD_PLAN C1).
        season, _ = split_term(scope_key)
        return f"{BASE}/bursar/fees-due-dates/payment-due-dates/{season}.php"
    raise ValueError(f"unknown source: {source!r}")


def host_allowed(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "https" and (parts.hostname or "").lower() in ALLOWED_HOSTS


# -- Terms --------------------------------------------------------------------

# Nominal (month, day) start and end of each season. They are not SJSU's actual
# dates (those are what the refresh reads); they only size the window inside
# which a year-less "Tue, Sep 15" is placed.
_NOMINAL = {
    "spring": ((1, 25), (5, 25)),
    "summer": ((6, 1), (8, 10)),
    "fall": ((8, 20), (12, 15)),
    "winter": ((1, 2), (1, 22)),
}


def term_window(term_key: str) -> tuple[date, date]:
    """Nominal (start, end) of a term."""
    season, year = split_term(term_key)
    (sm, sd), (em, ed) = _NOMINAL[season]
    return date(year, sm, sd), date(year, em, ed)


def current_term(today: date) -> str:
    """The term `today` falls in, with the gaps between terms rolled forward.

    Winter is not a regular term: January before spring starts counts as spring,
    which is when spring's calendar is the live one.
    """
    if today.month < 6:
        return f"spring-{today.year}"
    if (today.month, today.day) < (8, 15):
        return f"summer-{today.year}"
    return f"fall-{today.year}"


_ORDER = ("spring", "summer", "fall")


def next_terms(today: date, n: int) -> list[str]:
    """The `n` regular terms after the current one (not including it)."""
    season, year = split_term(current_term(today))
    idx = _ORDER.index(season)
    out = []
    for _ in range(max(0, n)):
        idx += 1
        if idx == len(_ORDER):
            idx, year = 0, year + 1
        out.append(f"{_ORDER[idx]}-{year}")
    return out


def current_ay(today: date) -> str:
    """The academic year containing `today` (it starts with fall)."""
    return ay_key(today.year if (today.month, today.day) >= (8, 15) else today.year - 1)
