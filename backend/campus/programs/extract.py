"""Read course codes and titles out of a department page. Pure functions, no I/O.

A page is first flattened into lines (`page_lines`): the text of `<main>`, with
a line break at every block element and `<br>`, table cells joined by " | ",
and headings marked. A heading is an `<h2>`-`<h6>`, or a line that is nothing
but bold text -- the Biomedical Engineering page titles its sections that way.

Two readers then work on the lines:

* `titled` takes lines shaped `SUBJ NUM - Title`, the form most department
  lists use: "Math 30 - Calculus I", "AE 015 [pdf] - Air & Space Flight",
  "BME 115 - Foundations of Biomedical Engineering 4 unit(s)". A code that only
  appears in passing ("Math 30 or 30X" in a prerequisite cell) is not taken.
* `codes` takes shorthand lists with no titles: "MATH 30, 31, 32, 123 - 13
  units", "PHYS 50/51", "CMPE 30, ENGR 10, ME 20". It is opt-in per source,
  because on most pages such mentions are prerequisites or prose, not the
  program's own list.

Codes are normalised the way campus/registration/keys.course_key and the UI's
normalizeCourseCode do ("CS 146"), with leading zeros dropped ("AE 015" is
"AE 15" on the class schedule).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import lxml.html

from campus.registration.keys import course_key

_HEADINGS = {"h2", "h3", "h4", "h5", "h6"}
_BLOCKS = _HEADINGS | {
    "h1", "p", "li", "tr", "div", "dt", "dd", "table", "ul", "ol",
    "header", "footer", "section", "aside", "blockquote",
}
_BOLD = {"strong", "b"}
# Private-use markers around bold text, so a line that is only bold can be told
# apart from one that merely contains some.
_BOLD_OPEN, _BOLD_CLOSE = "", ""
_HEADING_MARK = ""

# Words shaped like a subject that are not one. Subjects are 2-6 characters, so
# only short words matter.
_STOP = frozenset({
    "AND", "OR", "THE", "OF", "IN", "TO", "FOR", "AT", "BY", "ON", "IS", "IF",
    "UNIT", "UNITS", "FALL", "SPRING", "WINTER", "TOTAL", "ABOUT", "FROM", "WITH",
    "ROOM", "PAGE", "STEP", "YEAR", "YEARS", "GRADE", "LEVEL", "AREA", "AREAS",
    "MIN", "MAX", "GPA", "TOP", "PART", "PHASE", "WEEK", "WEEKS", "NOTE",
    "PHONE", "FAX", "EMAIL", "SUITE", "ZIP", "CA", "BLDG", "TEL",
})

_SUBJ = r"(?P<subj>[A-Za-z]{2,5}\d?)\.?"
_NUM = r"0*(?P<num>\d{1,3}[A-Za-z]{0,3})"
_TITLED = re.compile(
    rf"^{_SUBJ}\s*{_NUM}\s*(?:\[(?:pdf|docx?)\]\s*)?[-–—:]\s*(?P<title>.+)$"
)
# One subject followed by numbers joined by commas, slashes or "and".
_SHORTHAND = re.compile(
    rf"\b{_SUBJ}\s+(?P<list>0*\d{{1,3}}[A-Za-z]{{0,3}}"
    r"(?:\s*(?:,|/|\band\b)\s*0*\d{1,3}[A-Za-z]{0,3})*)\b"
)
_LIST_NUM = re.compile(r"0*(\d{1,3}[A-Za-z]{0,3})")
# Everything from a unit count onward is not part of the title:
# "Calculus I 3 unit(s) (B4) (or MATH 30X)" -> "Calculus I".
_UNITS_TAIL = re.compile(r"(?:^|\s+)\d+(?:\.\d+)?\s*(?:-\s*\d+\s*)?units?(?:\(s\))?\b.*$", re.I)


@dataclass(frozen=True)
class Line:
    text: str
    heading: bool = False


@dataclass(frozen=True)
class Course:
    code: str
    title: str | None = None
    group: str | None = None


def _norm_code(subj: str, num: str) -> str | None:
    subject = subj.upper()
    if subject in _STOP or not subject[0].isalpha():
        return None
    number = num.upper().lstrip("0") or "0"
    if not number[0].isdigit():
        return None
    return course_key(subject, number)


def page_lines(html: str) -> list[Line]:
    """The text of the page's `<main>` (or `<body>`), one Line per visual line."""
    doc = lxml.html.fromstring(html)
    roots = doc.xpath("//main") or doc.xpath("//body") or [doc]
    root = roots[0]
    for el in root.xpath(".//script | .//style | .//noscript | .//nav"):
        el.drop_tree()
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue  # comments and processing instructions
        tag = el.tag.lower()
        if tag == "br":
            el.tail = "\n" + (el.tail or "")
        elif tag in ("td", "th"):
            el.tail = " | " + (el.tail or "")
        elif tag in _BOLD:
            el.text = _BOLD_OPEN + (el.text or "")
            el.tail = _BOLD_CLOSE + (el.tail or "")
        elif tag in _BLOCKS:
            marker = _HEADING_MARK if tag in _HEADINGS else ""
            el.text = "\n" + marker + (el.text or "")
            el.tail = "\n" + (el.tail or "")

    out: list[Line] = []
    for raw in root.text_content().split("\n"):
        heading = _HEADING_MARK in raw
        stripped = raw.replace(_HEADING_MARK, "")
        squashed = " ".join(stripped.split())
        bold_only = (
            squashed.startswith(_BOLD_OPEN)
            and squashed.endswith(_BOLD_CLOSE)
            and squashed.count(_BOLD_OPEN) == 1
        )
        text = squashed.replace(_BOLD_OPEN, "").replace(_BOLD_CLOSE, "").strip(" |")
        text = " ".join(text.split())
        if text:
            out.append(Line(text, heading or bold_only))
    return out


def _clean_title(raw: str) -> str | None:
    title = raw.split("|", 1)[0]
    title = _UNITS_TAIL.sub("", title)
    title = " ".join(title.split()).strip(" -–—:;,")
    if not title or not re.search(r"[A-Za-z]{2}", title):
        return None
    return title


def _clean_group(text: str) -> str:
    return " ".join(text.split()).rstrip(":").strip()


def _lines_until(lines: list[Line], stop_at: str | None) -> list[Line]:
    if not stop_at:
        return lines
    needle = stop_at.casefold()
    for i, line in enumerate(lines):
        if line.heading and line.text.casefold().startswith(needle):
            return lines[:i]
    return lines


def extract_titled(lines: list[Line], *, groups: bool = False, stop_at: str | None = None) -> list[Course]:
    found: list[Course] = []
    group: str | None = None
    for line in _lines_until(lines, stop_at):
        m = _TITLED.match(line.text)
        if m is None:
            if line.heading:
                group = _clean_group(line.text)
            continue
        code = _norm_code(m["subj"], m["num"])
        title = _clean_title(m["title"])
        # No title, no course: "Phone: 408-924-4000" has the shape but not the content.
        if code is None or title is None:
            continue
        found.append(Course(code, title, group if groups else None))
    return found


def extract_codes(lines: list[Line], *, groups: bool = False, stop_at: str | None = None) -> list[Course]:
    found: list[Course] = []
    group: str | None = None
    for line in _lines_until(lines, stop_at):
        if line.heading:
            group = _clean_group(line.text)
            continue
        titled = _TITLED.match(line.text)
        for m in _SHORTHAND.finditer(line.text):
            nums = _LIST_NUM.findall(m["list"])
            for num in nums:
                code = _norm_code(m["subj"], num)
                if code is None:
                    continue
                title = None
                # "CHEM 1A - General Chemistry" on a shorthand page still has a title.
                if titled is not None and len(nums) == 1 and m.start() == 0:
                    title = _clean_title(titled["title"])
                found.append(Course(code, title, group if groups else None))
    return found


def merge(courses: list[Course]) -> list[Course]:
    """One entry per code, in first-seen order. A title beats no title; the first
    group a code appeared under is kept."""
    by_code: dict[str, Course] = {}
    for c in courses:
        prev = by_code.get(c.code)
        if prev is None:
            by_code[c.code] = c
        elif prev.title is None and c.title is not None:
            by_code[c.code] = Course(c.code, c.title, prev.group or c.group)
    return list(by_code.values())


def extract(html: str, *, mode: str = "titled", groups: bool = False, stop_at: str | None = None) -> list[Course]:
    lines = page_lines(html)
    if mode == "titled":
        return merge(extract_titled(lines, groups=groups, stop_at=stop_at))
    if mode == "codes":
        return merge(extract_codes(lines, groups=groups, stop_at=stop_at))
    raise ValueError(f"unknown mode: {mode!r}")
