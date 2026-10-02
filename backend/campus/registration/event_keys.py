"""Map a calendar label to the stable `event_key`s a chat card matches on.

The vocabulary is `event_keys.json` beside this file (owned by the registration
intent classifier, which reads its `question_patterns`; this module reads only
`label_patterns`). Patterns match case-insensitively with `re.search`, so one
printed cell that holds several deadlines (Sep 15 holds five) matches several
keys, and the calendar parser emits one row per key.

`EVENT_KEYS_PATH` overrides the location. A missing or malformed file raises.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path

_DEFAULT_PATH = Path(__file__).resolve().parent / "event_keys.json"

# Keys a registrar term calendar must produce, or the page is treated as having
# drifted. Each is a deadline a student asks about by name.
CORE_REGISTRAR_KEYS = (
    "drop_without_w_last",
    "census_date",
    "instruction_first_day",
    "instruction_last_day",
    "finals_period",
    "grades_due",
)


def config_path() -> Path:
    override = os.getenv("EVENT_KEYS_PATH", "").strip()
    return Path(override) if override else _DEFAULT_PATH


@lru_cache(maxsize=1)
def _compiled() -> tuple[tuple[str, tuple[re.Pattern, ...]], ...]:
    data = json.loads(config_path().read_text(encoding="utf-8"))
    keys = data.get("keys")
    if not isinstance(keys, dict) or not keys:
        raise ValueError(f"{config_path()} has no 'keys'")
    out = []
    for key, spec in keys.items():
        patterns = tuple(re.compile(p, re.IGNORECASE) for p in spec.get("label_patterns", []))
        out.append((key, patterns))
    return tuple(out)


def reload() -> None:
    _compiled.cache_clear()


def all_keys() -> list[str]:
    return [k for k, _ in _compiled()]


def keys_for_label(label: str) -> list[str]:
    """Every key whose label pattern matches, in file order. Empty if none."""
    return [key for key, pats in _compiled() if any(p.search(label) for p in pats)]
