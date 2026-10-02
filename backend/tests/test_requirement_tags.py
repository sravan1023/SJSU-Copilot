"""
The requirement-tag vocabulary and its Python normaliser (SERVICES_BUILD_PLAN F4).

Run from backend/ with:
    python -m pytest tests/test_requirement_tags.py

The vocabulary is UI/src/config/requirement_tags.json, shared with the TypeScript
normaliser. The agreement test runs both on the same inputs through Node (24, with
type stripping) and is skipped when Node is absent.
"""
import json
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest
from lxml import html as lxml_html

from campus.registration import keys

ROOT = Path(__file__).resolve().parents[2]
TS_MODULE = ROOT / "UI" / "src" / "config" / "requirementTags.ts"
FIXTURE = Path(__file__).parent / "fixtures" / "campus" / "schedule-fall-2026.trimmed.html"

TRICKY = [
    "GE:3B+US23",
    "GE: 3B+US23",
    "GE:   3B+US23",
    "  GE: UD 2/5  ",
    "GE:\t1Bor4",
    "GE:\n4+US1",
    "GE:  Area   4",
    "AI:US1",
    "AI : US1",
    "ge: 3b",
    "GE : 3B",
    "GE: 3B : x",
    "GWAR",
    "",
    "   ",
    "\u00a0GE: 3B\u00a0",
    ":  :",
    "a:\u2003b",
    "PE: PhysEd",
]


def test_tag_ids_are_unique():
    ids = keys.tag_ids()
    assert ids and len(ids) == len(set(ids)), [i for i, n in Counter(ids).items() if n > 1]


def test_every_id_used_in_cells_exists_in_tags():
    known = set(keys.tag_ids())
    for cell, tags in keys.cells().items():
        assert set(tags) <= known, (cell, set(tags) - known)


def test_every_satisfies_cell_in_the_fixture_is_known():
    doc = lxml_html.fromstring(FIXTURE.read_text(encoding="utf-8"))
    table = doc.get_element_by_id("classSchedule")
    header = [" ".join(th.text_content().split()) for th in table.iter("th")]
    col = header.index("Satisfies")
    seen = set()
    for tr in table.iter("tr"):
        tds = tr.findall("td")
        if len(tds) > col:
            text = tds[col].text_content()
            if text.strip():
                seen.add(text)
    assert len(seen) >= 30
    unknown = [c for c in seen if not keys.tags_for_cell(c).known]
    assert unknown == []


def test_unknown_cells_keep_their_raw_string():
    res = keys.tags_for_cell("GE: Z9 (new area)")
    assert res.tags == [] and res.known is False and res.raw == "GE: Z9 (new area)"
    blank = keys.tags_for_cell("")
    assert blank.tags == [] and blank.known is False
    # raw is the input, not the normalised key
    assert keys.tags_for_cell("GE: 3B+US23").raw == "GE: 3B+US23"
    assert keys.tags_for_cell("GE:3B+US23").known


def test_known_cells_and_multi_area_tagging():
    assert keys.tags_for_cell("GE: UD 2/5").tags == ["GE:UD2", "GE:UD5"]
    spaced, glued = keys.tags_for_cell("GE: 3B+US23"), keys.tags_for_cell("GE:3B+US23")
    assert spaced.tags == glued.tags and "GE:3B" in spaced.tags
    assert keys.tags_for_cell("GE: 4+US").tags == ["GE:4"]


def test_course_key():
    assert keys.course_key("cs", "146") == "CS 146"
    assert keys.course_key(" Math ", " 30p ") == "MATH 30P"
    assert keys.course_key("CMPE", "172") == "CMPE 172"


def test_path_override(tmp_path, monkeypatch):
    alt = tmp_path / "tags.json"
    alt.write_text(
        json.dumps({"tags": [{"id": "X:1", "label": "x", "system": "other"}], "cells": {"X:1": ["X:1"]}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("REQUIREMENT_TAGS_PATH", str(alt))
    keys.reload()
    try:
        assert keys.tag_ids() == ["X:1"]
        assert keys.tags_for_cell("X:   1").tags == ["X:1"]
    finally:
        monkeypatch.delenv("REQUIREMENT_TAGS_PATH")
        keys.reload()
    assert "GE:3B" in keys.tag_ids()


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_python_and_typescript_normalisation_agree():
    script = (
        f"import {{ normalizeCell, tagsForCell }} from {json.dumps(TS_MODULE.as_uri())};"
        "const inputs = JSON.parse(process.argv[1]);"
        "console.log(JSON.stringify(inputs.map(r => [normalizeCell(r), tagsForCell(r).tags, tagsForCell(r).known])));"
    )
    inputs = TRICKY + list(keys.cells())
    out = subprocess.run(
        ["node", "--input-type=module", "-e", script, json.dumps(inputs)],
        capture_output=True, text=True, timeout=60, encoding="utf-8",
    )
    assert out.returncode == 0, out.stderr
    ts = json.loads(out.stdout)
    for raw, (norm, tags, known) in zip(inputs, ts):
        py = keys.tags_for_cell(raw)
        assert keys.normalise_cell(raw) == norm, repr(raw)
        assert py.tags == tags and py.known == known, repr(raw)
