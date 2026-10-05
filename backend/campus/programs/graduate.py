"""Graduate program definitions: hand-curated requirement rules.

A master's degree is not a list of courses but a set of rules: core courses
that are all required, "6 units from one specialization", "9 units, at least 3
from Area A", "any CMPE course numbered 200 or higher except ...", a project
that needs 15 units first. No department page states those rules in a form a
generic reader can trust, so each program is written by hand in
`graduate/<id>.json` from its department page (or, for MS CS, from the catalog
text a student copied, since the catalog cannot be fetched).

What keeps a hand-written file honest:

* `validate` checks the file is internally consistent: every code is in
  `courses` with a title and units, choices and options resolve, prerequisites
  name real courses and requirements, and the requirements add up to
  `total_units`.
* `drift` checks it against the live pages: a course in the file that no source
  page mentions any more is an error (the department dropped or renamed it);
  a course on a page that the file doesn't know is a warning to review. A course
  that carries `evidence` (it was added because a MyProgress report counted it,
  though the page doesn't list it) is exempt, and the evidence says why.

The UI (degreeProgress/requirements.ts) interprets the rules; this module only
publishes them.

A choice with `infer: true` (the specialization) is not asked: the UI works it
out from the courses the student marked. `merge_groups` names requirement
groups the UI shows as one section, because their course lists overlap (an MS AI
specialization course is usually also an elective).

Rule bodies, used directly or per option under `choice` + `by_option`:
  all:  [codes]                every course is required
  from: [codes]                courses totalling the requirement's units
  pools: [{id, label, from, from_requirements?, min_units?, max_units?}]
  from_requirements: [ids]     also courses listed by those requirements, if unused
  match: {subject, min_number, exclude}   any course of that subject at or above the number
  suggest: [codes]             shown and planned for a `match` rule
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from campus import terms
from campus.programs.extract import page_lines
from campus.registration.parse_calendar import parse_last_updated

GRAD_DIR = Path(__file__).with_name("graduate")
CODE_RE = re.compile(r"^[A-Z][A-Z0-9]{0,5} \d{1,3}[A-Z]{0,3}$")
ID_RE = re.compile(r"^[a-z0-9-]{1,40}$")
SOURCE_KINDS = ("page", "catalog-copy")
BODY_KEYS = ("all", "from", "pools", "match", "from_requirements")
# Course mentions in page text: "CMPE 252", "Engr 200W", "MATH 279A".
_MENTION = re.compile(r"\b([A-Za-z]{2,5}\d?)\s?0*(\d{1,3}[A-Za-z]{0,3})\b")


def load_definitions(directory: Path = GRAD_DIR) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.json"))]


def _bodies(req: dict) -> list[tuple[str, dict]]:
    """(label, body) for a requirement: itself, or one per option."""
    if "by_option" in req:
        return [(f"{req['id']}[{opt}]", body) for opt, body in req["by_option"].items()]
    return [(req["id"], req)]


def body_codes(body: dict) -> list[str]:
    codes = list(body.get("all", [])) + list(body.get("from", [])) + list(body.get("suggest", []))
    for pool in body.get("pools", []):
        codes += pool.get("from", [])
    return codes


def validate(defn: dict) -> list[str]:
    errors: list[str] = []
    err = errors.append
    pid = defn.get("id", "?")

    if not isinstance(defn.get("id"), str) or not ID_RE.match(defn["id"]):
        err(f"bad id {defn.get('id')!r}")
    if defn.get("level") != "graduate":
        err(f"{pid}: level must be 'graduate'")
    for key in ("name", "department"):
        if not isinstance(defn.get(key), str) or not defn[key].strip():
            err(f"{pid}: missing {key}")
    aliases = defn.get("aliases", [])
    if not isinstance(aliases, list) or not all(isinstance(a, str) and a.strip() for a in aliases):
        err(f"{pid}: aliases must be a list of non-empty strings")
    total = defn.get("total_units")
    if not isinstance(total, int) or not 1 <= total <= 60:
        err(f"{pid}: total_units must be an integer 1-60")

    sources = defn.get("sources", [])
    if not sources:
        err(f"{pid}: no sources")
    for s in sources:
        kind = s.get("kind")
        if kind not in SOURCE_KINDS:
            err(f"{pid}: source kind must be one of {SOURCE_KINDS}")
        elif kind == "page" and not terms.host_allowed(s.get("url", "")):
            err(f"{pid}: page source {s.get('url')} is not on {terms.ALLOWED_HOSTS}")
        elif kind == "catalog-copy" and not re.match(r"^\d{4}-\d{2}-\d{2}$", s.get("copied", "")):
            err(f"{pid}: a catalog-copy source needs a 'copied' date")

    courses = defn.get("courses", {})
    for code, info in courses.items():
        if not CODE_RE.match(code):
            err(f"{pid}: course code {code!r} is not in 'SUBJ NUM' form")
        if not isinstance(info.get("title"), str) or not info["title"].strip():
            err(f"{pid}: {code} has no title")
        if not isinstance(info.get("units"), int) or not 1 <= info["units"] <= 6:
            err(f"{pid}: {code} units must be an integer 1-6")
        if "evidence" in info and (not isinstance(info["evidence"], str) or not info["evidence"].strip()):
            err(f"{pid}: {code} evidence must say where the course came from")

    choices = {c["id"]: {o["id"] for o in c.get("options", [])} for c in defn.get("choices", [])}
    if len(choices) != len(defn.get("choices", [])):
        err(f"{pid}: duplicate choice ids")
    for c in defn.get("choices", []):
        if "infer" in c and not isinstance(c["infer"], bool):
            err(f"{pid}: choice {c['id']!r} infer must be true or false")

    req_ids = [r.get("id") for r in defn.get("requirements", [])]
    if len(set(req_ids)) != len(req_ids):
        err(f"{pid}: duplicate requirement ids")
    unit_sum = 0
    for req in defn.get("requirements", []):
        rid = req.get("id")
        if not isinstance(rid, str) or not ID_RE.match(rid):
            err(f"{pid}: bad requirement id {rid!r}")
            continue
        units = req.get("units")
        if not isinstance(units, int) or units <= 0:
            err(f"{pid}: {rid} needs positive integer units")
            continue
        unit_sum += units
        if "choice" in req:
            if req["choice"] not in choices:
                err(f"{pid}: {rid} names unknown choice {req['choice']!r}")
            elif set(req.get("by_option", {})) != choices[req["choice"]]:
                err(f"{pid}: {rid} must define every option of {req['choice']!r} and nothing else")
        elif "by_option" in req:
            err(f"{pid}: {rid} has by_option without a choice")
        for label, body in _bodies(req):
            present = [k for k in BODY_KEYS if k in body]
            if not present:
                err(f"{pid}: {label} has no rule ({', '.join(BODY_KEYS)})")
            for code in body_codes(body):
                if code not in courses:
                    err(f"{pid}: {label} lists {code}, which is not in courses")
            for ref in body.get("from_requirements", []) + [
                r for p in body.get("pools", []) for r in p.get("from_requirements", [])
            ]:
                if ref not in req_ids or ref == rid:
                    err(f"{pid}: {label} draws from unknown requirement {ref!r}")
            if "all" in body:
                total_all = sum(courses.get(c, {}).get("units", 0) for c in body["all"])
                if total_all != units:
                    err(f"{pid}: {label} lists {total_all} units of required courses but needs {units}")
            match = body.get("match")
            if match is not None and (not isinstance(match.get("subject"), str) or not isinstance(match.get("min_number"), int)):
                err(f"{pid}: {label} match needs a subject and an integer min_number")
            min_sum = 0
            for pool in body.get("pools", []):
                pool_id = pool.get("id")
                if not pool.get("from") and not pool.get("from_requirements"):
                    err(f"{pid}: {label} pool {pool_id!r} is empty")
                lo, hi = pool.get("min_units"), pool.get("max_units")
                for name, v in (("min_units", lo), ("max_units", hi)):
                    if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v <= 0):
                        err(f"{pid}: {label} pool {pool_id!r} {name} must be a positive integer")
                if isinstance(lo, int) and isinstance(hi, int) and lo > hi:
                    err(f"{pid}: {label} pool {pool_id!r} min_units is above its max_units")
                if isinstance(hi, int) and hi > units:
                    err(f"{pid}: {label} pool {pool_id!r} max_units is above the requirement's {units} units")
                if isinstance(lo, int):
                    min_sum += lo
                    # A pool drawing on other requirements can't be sized here.
                    if not pool.get("from_requirements"):
                        offered = sum(courses.get(c, {}).get("units", 0) for c in pool.get("from", []))
                        if offered < lo:
                            err(f"{pid}: {label} pool {pool_id!r} lists {offered} units but needs at least {lo}")
            if min_sum > units:
                err(f"{pid}: {label} pool minimums add up to {min_sum}, above the requirement's {units} units")
    if isinstance(total, int) and unit_sum != total:
        err(f"{pid}: requirements add up to {unit_sum} units, not {total}")
    groups = {r.get("group") for r in defn.get("requirements", []) if r.get("group")}
    for g in defn.get("merge_groups", []):
        if g not in groups:
            err(f"{pid}: merge_groups names {g!r}, which no requirement uses")
    conditions = defn.get("conditions")
    if conditions is not None and (
        not isinstance(conditions, list) or not 1 <= len(conditions) <= 8
        or not all(isinstance(c, str) and c.strip() and len(c) <= 240 for c in conditions)
    ):
        err(f"{pid}: conditions must be 1-8 non-empty strings of at most 240 characters")

    for code, pre in defn.get("prerequisites", {}).items():
        if code not in courses:
            err(f"{pid}: prerequisite for unknown course {code}")
        for c in pre.get("courses", []):
            if c not in courses:
                err(f"{pid}: {code} requires unknown course {c}")
        for r in pre.get("requirements", []):
            if r not in req_ids:
                err(f"{pid}: {code} requires unknown requirement {r!r}")
        if "min_units" in pre and not isinstance(pre["min_units"], int):
            err(f"{pid}: {code} min_units must be an integer")
        if pre.get("basis") not in ("documented", "guide"):
            err(f"{pid}: {code} prerequisite basis must be 'documented' or 'guide'")
    return errors


def unpublished_changes(defn: dict, published: dict) -> list[str]:
    """Top-level keys where a published program file differs from its rule file.

    The builder adds `built_at` and replaces `sources` with what it fetched, so
    those two are expected to differ; anything else means the published file
    is stale (someone edited the rule file and didn't rebuild).
    """
    ignore = {"built_at", "sources"}
    keys = (set(defn) | set(published)) - ignore
    return sorted(k for k in keys if defn.get(k) != published.get(k))


def page_codes(html: str) -> set[str]:
    """Every course code mentioned in a page's main text."""
    found: set[str] = set()
    for line in page_lines(html):
        for subj, num in _MENTION.findall(line.text):
            found.add(f"{subj.upper()} {num.upper().lstrip('0') or '0'}")
    return found


def drift(defn: dict, pages: dict[str, str]) -> tuple[list[str], list[str]]:
    """(codes in the definition that no page mentions, codes on the pages the definition lacks).

    Only codes of subjects the definition uses are compared, so a page's passing
    mention of, say, "Fall 2026" or "Room 285" cannot count.
    """
    on_pages: set[str] = set()
    for html in pages.values():
        on_pages |= page_codes(html)
    known = set(defn.get("courses", {}))
    # A course added on other evidence (a MyProgress report) isn't expected on the pages.
    vouched = {c for c, info in defn.get("courses", {}).items() if info.get("evidence")}
    # Codes a rule names only to exclude them are known too, for the unlisted side.
    excluded = {
        c for req in defn.get("requirements", []) for _, body in _bodies(req)
        for c in (body.get("match") or {}).get("exclude", [])
    }
    subjects = {c.split(" ", 1)[0] for c in known}
    on_pages = {c for c in on_pages if c.split(" ", 1)[0] in subjects}
    missing = sorted(known - on_pages - vouched)
    unlisted = sorted(on_pages - known - excluded)
    return missing, unlisted


@dataclass
class GradBuilt:
    defn: dict
    sources: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.defn["id"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def payload(self, built_at: date) -> dict:
        return {**self.defn, "built_at": built_at.isoformat(), "sources": self.sources}

    def index_entry(self) -> dict:
        dates = [s.get("last_updated") or s.get("copied") for s in self.sources]
        dates = [d for d in dates if d]
        return {
            "id": self.defn["id"],
            "level": "graduate",
            "name": self.defn["name"],
            "department": self.defn["department"],
            "college": self.defn.get("college", ""),
            "aliases": list(self.defn.get("aliases", [])),
            "total_units": self.defn["total_units"],
            "course_count": len(self.defn.get("courses", {})),
            "last_updated": min(dates) if dates else None,
        }


def page_urls(defn: dict) -> list[str]:
    return [s["url"] for s in defn.get("sources", []) if s.get("kind") == "page"]


def build_graduate(defn: dict, pages: dict, today: date, *, stale_after) -> GradBuilt:
    """`pages` maps url -> an object with .html / .error / .final_url (build.Page)."""
    out = GradBuilt(defn)
    out.errors.extend(validate(defn))
    fetched: dict[str, str] = {}
    for s in defn.get("sources", []):
        if s.get("kind") != "page":
            out.sources.append({k: v for k, v in s.items()})
            out.warnings.append(f"{s.get('url')}: copied by hand on {s.get('copied')}; not checked against a live page")
            continue
        page = pages.get(s["url"])
        if page is None or page.html is None:
            out.errors.append(f"{s['url']}: {page.error if page else 'not fetched'}")
            continue
        fetched[s["url"]] = page.html
        updated = parse_last_updated(page.html)
        if updated is None:
            out.warnings.append(f"{s['url']}: no 'Last Updated' line")
        elif today - updated > stale_after:
            out.warnings.append(f"{s['url']}: last updated {updated.isoformat()}, over two years ago")
        out.sources.append({"url": s["url"], "kind": "page", "last_updated": updated.isoformat() if updated else None})
    if fetched and out.ok:
        missing, unlisted = drift(defn, fetched)
        if missing:
            out.errors.append(f"not on any source page any more: {', '.join(missing)}")
        if unlisted:
            out.warnings.append(f"on the pages but not in the definition (review): {', '.join(unlisted)}")
    return out
