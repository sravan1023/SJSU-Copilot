"""Audience configuration, shared with the UI.

One file describes all four audiences -- their labels, suggestions, source
collections and prompt text -- and both halves of the app read it:
`UI/src/config/audiences.ts` imports it, and this module loads the same bytes.
Duplicating it in two languages is a correctness bug waiting to happen, because
the prompt half and the UI half would drift silently and nothing would fail.

**Where it lives.** The JSON sits under `UI/src/config/` rather than a
repo-root `shared/` directory. Vite's root is `UI/`, and `UI/vite.config.js`
sets no `server.fs.allow` and no alias, so a file outside that root is an
unverified build risk for a gain of tidiness only. The backend reaching into
`UI/` is the uglier half of that trade and is made explicit here rather than
hidden. `AUDIENCES_CONFIG_PATH` overrides the location for a deployment that
ships the backend without the UI tree.

**Fails loudly.** The values here become prompt text and search scoping, so a
missing or malformed file is not something to paper over with a default.

The four ids are exactly the values the database check constraints allow on
`profiles.active_audience`, `profile_audience_details.audience` and
`conversations.audience`. `backend/tests/test_audiences.py` asserts that
against the migration, so adding a fifth fails the suite rather than failing in
the browser as "save failed".
"""
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

# Kept in sync with 20260917000100_audience_model.sql's three check
# constraints. The test compares against the migration text, not against this
# tuple, so this is a convenience rather than the authority.
AUDIENCE_IDS = ("student", "alumni", "guest", "faculty")

# The six values of the affiliation_kind enum. A different concept: what a
# person claims to be, not which experience they get.
AFFILIATION_KINDS = (
    "student",
    "alumni",
    "faculty",
    "staff",
    "applicant",
    "community",
)

_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "UI" / "src" / "config" / "audiences.json"


def config_path() -> Path:
    """Where the shared config is read from. Env wins, for deployments."""
    override = os.getenv("AUDIENCES_CONFIG_PATH", "").strip()
    return Path(override) if override else _DEFAULT_PATH


@lru_cache(maxsize=1)
def _config() -> dict[str, Any]:
    """Parse the shared JSON once.

    Cached rather than read at import so the path can be overridden in a test
    with patch.dict(os.environ, ...) -- the idiom the rest of this suite uses --
    by calling `reload()` first. Import-time reads are what make
    runtime.py's constants untestable.
    """
    path = config_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(
            f"audience configuration is missing at {path}. It is shared with "
            "UI/src/config/audiences.ts; set AUDIENCES_CONFIG_PATH if the "
            "backend is deployed without the UI tree."
        ) from exc

    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise RuntimeError(f"audience configuration at {path} is not valid JSON") from exc

    audiences = parsed.get("audiences")
    if not isinstance(audiences, dict) or not audiences:
        raise RuntimeError(f"audience configuration at {path} has no 'audiences' object")

    missing = [a for a in AUDIENCE_IDS if a not in audiences]
    if missing:
        raise RuntimeError(
            f"audience configuration at {path} is missing: {', '.join(missing)}"
        )

    return parsed


def reload() -> None:
    """Drop the cached parse. For tests and for AUDIENCES_CONFIG_PATH changes."""
    _config.cache_clear()


def default_audience() -> str:
    return _config().get("default", "student")


def all_audiences() -> dict[str, dict]:
    return _config()["audiences"]


def is_audience(value: str | None) -> bool:
    """True when the value is one the database will accept."""
    return bool(value) and value in all_audiences()


def get(audience: str | None) -> dict:
    """One audience's config, falling back to the default.

    Never raises on an unknown id. `ChatRequest.audience` is client-supplied
    and the server must not trust it for anything authorization-shaped -- it
    selects prompt text and source collections only -- so an unrecognised value
    degrades to the default rather than erroring.
    """
    audiences = all_audiences()
    if audience and audience in audiences:
        return audiences[audience]
    return audiences[default_audience()]


def prompt_context(audience: str | None) -> str:
    return get(audience).get("promptContext", "")


def offices(audience: str | None) -> list[str]:
    return list(get(audience).get("offices", []))


def source_collections(audience: str | None) -> list[str]:
    """Domains this audience's retrieval should prefer and scope to."""
    collections = get(audience).get("sourceCollections") or []
    return [c for c in collections if isinstance(c, str) and c]


def site_filter(audience: str | None) -> str:
    """The `site:` fragment for a search query.

    One domain yields `site:sjsu.edu`; several yield
    `(site:a OR site:b)`. DuckDuckGo's `site:` already matches subdomains, so
    only distinct registrable domains belong in sourceCollections.
    """
    domains = source_collections(audience)
    if not domains:
        return ""
    if len(domains) == 1:
        return f"site:{domains[0]}"
    return "(" + " OR ".join(f"site:{d}" for d in domains) + ")"


def affiliation_to_audience() -> dict[str, str]:
    return dict(_config().get("affiliationToAudience", {}))
