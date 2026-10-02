r"""The requirement-tag vocabulary and course keys.

The vocabulary is `UI/src/config/requirement_tags.json`, shared with
`UI/src/config/requirementTags.ts`. Parsing lives only in the JSON's `cells`
table: neither language carries logic of its own, so the two cannot drift. The
one piece of code both must share is the normalisation, which is mirrored here
EXACTLY (no case folding, nothing else):

    TS : raw.trim().replace(/\s+/g, ' ').replace(/:\s+/g, ':')
    Py : re.sub(r":\s+", ":", re.sub(r"\s+", " ", raw.strip()))

`tests/test_requirement_tags.py` runs both on a list of tricky inputs. Known
divergence, measured and left alone because SJSU's cells contain none of them:
Python's `\s`/`strip` also treat U+001C..U+001F and U+0085 as whitespace, and
JavaScript's treat U+FEFF as whitespace. A cell with one of those is "unknown" in
one language and may be known in the other.

`REQUIREMENT_TAGS_PATH` overrides the file location, as `AUDIENCES_CONFIG_PATH`
does for `audiences.py`, for a deployment that ships the backend without the UI
tree. A missing or malformed file raises: these values are stored in the
database, so a silent default is worse than a failed refresh.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

_DEFAULT_PATH = (
    Path(__file__).resolve().parents[3] / "UI" / "src" / "config" / "requirement_tags.json"
)


def config_path() -> Path:
    override = os.getenv("REQUIREMENT_TAGS_PATH", "").strip()
    return Path(override) if override else _DEFAULT_PATH


@lru_cache(maxsize=1)
def _vocabulary() -> dict[str, Any]:
    data = json.loads(config_path().read_text(encoding="utf-8"))
    if not isinstance(data.get("tags"), list) or not isinstance(data.get("cells"), dict):
        raise ValueError(f"{config_path()} must have a 'tags' list and a 'cells' object")
    return data


def reload() -> None:
    """Drop the cached vocabulary (tests that change the path call this)."""
    _vocabulary.cache_clear()


def tag_ids() -> list[str]:
    return [t["id"] for t in _vocabulary()["tags"]]


def cells() -> dict[str, list[str]]:
    return _vocabulary()["cells"]


def normalise_cell(raw: str) -> str:
    """Trim, collapse whitespace runs to one space, drop whitespace after a colon."""
    return re.sub(r":\s+", ":", re.sub(r"\s+", " ", raw.strip()))


@dataclass(frozen=True)
class CellTags:
    tags: list[str] = field(default_factory=list)
    raw: str = ""
    known: bool = False


def tags_for_cell(raw: str | None) -> CellTags:
    """Tags for a Satisfies cell. Unknown cells give no tags; `raw` is always kept."""
    raw = raw or ""
    hit = cells().get(normalise_cell(raw))
    return CellTags(tags=list(hit) if hit is not None else [], raw=raw, known=hit is not None)


def course_key(subject: str, number: str) -> str:
    """('cs', '146') -> 'CS 146'. Whitespace is collapsed, case is upper."""
    subject = re.sub(r"\s+", " ", str(subject).strip()).upper()
    number = re.sub(r"\s+", "", str(number)).upper()
    return f"{subject} {number}"
