"""Parsers for SJSU's registrar term calendar and academic calendar.

Pure functions over an HTML string: no network, no database. They use lxml
directly rather than BeautifulSoup, for consistency with the schedule parser
(SERVICES_BUILD_PLAN F1: on the 3.76 MB schedule page lxml is 4x faster, and
these pages are small enough that only the consistency matters).

Each returns a `CalendarParse`:

* `rows`: one dict per (event, key) with `category`, `label_raw`, `date_raw`,
  `start_date`, `end_date` and `event_key`. `label_raw` and `date_raw` are the
  cells word for word (whitespace collapsed, nothing else), because a card shows
  the source row exactly as SJSU printed it.
* `rejected`: rows that could not be turned into dates, each with a reason.
  Nothing is guessed: a row whose stated weekday matches no candidate year, or
  whose year is ambiguous, lands here and is never published.
* `last_updated`: the page's visible "Last Updated <Mon D, YYYY>" line.
* `headers`: the header tuple of every table, for the drift check, and
  `unknown_headers`, the ones this parser does not know.

**One cell, several deadlines.** The Sep 15 row of the registrar calendar holds
the drop-without-W, add/drop, audit/CR-NC, excess-units and instructor-drops
deadlines in one cell. `label_raw` stays the whole cell and one row is emitted
per matched `event_key`, all sharing the same label and dates. A cell that
matches no key is kept with `event_key = None`.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from datetime import date

from lxml import html as lxml_html

from campus import dates, terms
from campus.dates import DateReject
from campus.registration import event_keys

REGISTRAR_HEADERS = (("Dates", "Event Description"), ("Date", "Deadline"))
# Spring 2027's page (measured 2026-10-01) has one table with no <th> row at all:
# it opens with a year marker row ('2026' | '') and goes straight into
# 'Mon, Oct. 19' | 'Enrollment appointments ...'. A headerless table is accepted
# as this layout only if it really is a two-column date calendar (see
# `_looks_like_calendar`); it is recorded under this pseudo-header.
HEADERLESS_CALENDAR = ("(no header: date | event)",)
REGISTRAR_LAYOUTS = (REGISTRAR_HEADERS[0], HEADERLESS_CALENDAR)
_YEAR_MARKER = re.compile(r"^(20\d\d)$")
_MIN_CALENDAR_ROWS = 5
ACADEMIC_TERM_HEADER = re.compile(r"^(Spring|Summer|Fall|Winter)\s+(20\d\d)$")
ACADEMIC_HEADER_PREFIXES = (("Event", "Type", "Date"),)

_LAST_UPDATED = re.compile(r"Last Updated\s+([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})")
_BLOCKS = {"p", "li", "ul", "ol", "div", "br", "tr", "td", "th", "table", "h1", "h2", "h3", "h4"}


@dataclass
class CalendarParse:
    rows: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    last_updated: date | None = None
    headers: list[tuple[str, ...]] = field(default_factory=list)
    unknown_headers: list[tuple[str, ...]] = field(default_factory=list)
    data_rows: int = 0
    blank_cells: int = 0

    @property
    def event_keys(self) -> set[str]:
        return {r["event_key"] for r in self.rows if r["event_key"]}


def _doc(html: str):
    return lxml_html.fromstring(html)


def _text(el) -> str:
    """A cell's text with a space at every block boundary, whitespace collapsed.

    `text_content()` alone would run '<p>Wed, Dec. 16</p><p>Thu, Dec. 17</p>'
    together. Collapsing includes non-breaking spaces; nothing else is altered.
    """
    el = copy.deepcopy(el)
    for node in el.iter():
        if not isinstance(node.tag, str):
            continue
        if node.tag in _BLOCKS:
            node.text = " " + (node.text or "")
            node.tail = " " + (node.tail or "")
    return " ".join(el.text_content().split())


def parse_last_updated(html: str) -> date | None:
    m = _LAST_UPDATED.search(html)
    if not m:
        return None
    try:
        return date(int(m.group(3)), dates.month_number(m.group(1)), int(m.group(2)))
    except (ValueError, DateReject):
        return None


def _tables(doc):
    for table in doc.iter("table"):
        ths = table.xpath(".//tr[1]/th")
        header = tuple(_text(th) for th in ths)
        yield table, header


def _data_rows(table):
    for tr in table.iter("tr"):
        tds = tr.findall("td")
        if tds:
            yield [_text(td) for td in tds]


def _looks_like_calendar(table) -> bool:
    """A headerless table counts as a registrar calendar only if every row has two
    cells, and at least `_MIN_CALENDAR_ROWS` rows start with a weekday date. Any
    other headerless table stays unknown, which fails the refresh closed."""
    rows = list(_data_rows(table))
    if not rows or any(len(cells) != 2 for cells in rows):
        return False
    dated = sum(1 for cells in rows if dates.has_weekday_prefix(cells[0]))
    return dated >= _MIN_CALENDAR_ROWS


def _emit(result: CalendarParse, category: str, label: str, date_raw: str, rng) -> None:
    keys = event_keys.keys_for_label(label) or [None]
    for key in keys:
        result.rows.append(
            {
                "category": category,
                "label_raw": label,
                "date_raw": date_raw,
                "start_date": rng.start,
                "end_date": rng.end,
                "event_key": key,
            }
        )


def parse_registrar_calendar(html: str, term_key: str) -> CalendarParse:
    """Fall 2026's layout, `Dates | Event Description` and `Date | Deadline`
    (January), and Spring 2027's, one headerless table with year marker rows.

    A year marker row ('2026' | '') is not data: it sets the year the following
    rows' start dates must fall in, as a cross-check on year inference.
    """
    anchor = terms.term_window(term_key)
    window = dates.inference_window(*anchor)
    result = CalendarParse(last_updated=parse_last_updated(html))
    for table, header in _tables(_doc(html)):
        if not header and _looks_like_calendar(table):
            header = HEADERLESS_CALENDAR
        result.headers.append(header)
        if header not in REGISTRAR_HEADERS and header != HEADERLESS_CALENDAR:
            result.unknown_headers.append(header)
            continue
        year_hint: int | None = None
        for cells in _data_rows(table):
            if len(cells) == 2 and not cells[1] and _YEAR_MARKER.match(cells[0]):
                year_hint = int(cells[0])
                continue
            result.data_rows += 1
            if len(cells) != 2:
                result.rejected.append({"label_raw": " | ".join(cells), "date_raw": "",
                                        "reason": "unexpected column count"})
                continue
            date_raw, label = cells[0], cells[1]
            if not label:
                result.rejected.append(
                    {"label_raw": label, "date_raw": date_raw, "reason": "empty label"}
                )
                continue
            try:
                rng = dates.parse_weekday_dates(
                    date_raw, window, anchor=anchor, year_hint=year_hint
                )
            except DateReject as exc:
                result.rejected.append(
                    {"label_raw": label, "date_raw": date_raw, "reason": str(exc)}
                )
                continue
            _emit(result, "registrar", label, date_raw, rng)
    return result


def parse_academic_calendar(html: str, ay_key: str) -> CalendarParse:
    """`Event | Fall YYYY | Spring YYYY` (a date per term column) and
    `Event | Type | Date` (holidays, with years printed)."""
    start_year, end_year = terms.ay_years(ay_key)
    ay_window = (date(start_year, 6, 1), date(end_year, 8, 1))
    result = CalendarParse(last_updated=parse_last_updated(html))
    for table, header in _tables(_doc(html)):
        result.headers.append(header)
        columns = _academic_columns(header)
        if columns and any(
            term_key and terms.split_term(term_key)[1] != (
                start_year if terms.split_term(term_key)[0] == "fall" else end_year
            )
            for _, term_key in columns
        ):
            columns = None  # A redirected/old academic year is not this scope.
        if columns is None:
            result.unknown_headers.append(header)
            continue
        for cells in _data_rows(table):
            if len(cells) != len(header):
                result.data_rows += 1
                result.rejected.append({"label_raw": " | ".join(cells), "date_raw": "",
                                        "reason": "unexpected column count"})
                continue
            label = cells[0]
            if not label:
                result.data_rows += 1
                result.rejected.append({"label_raw": "", "date_raw": " | ".join(cells[1:]),
                                        "reason": "empty label"})
                continue
            for idx, term_key in columns:
                date_raw = cells[idx]
                if not date_raw:
                    result.blank_cells += 1
                    continue
                result.data_rows += 1
                window = ay_window
                if term_key:
                    # Year-less dates ('December 9-11, 14-15') sit inside their
                    # own term, so a tight window is unambiguous.
                    window = dates.inference_window(
                        *terms.term_window(term_key), before=2, after=2
                    )
                try:
                    rng = dates.parse_prose_dates(date_raw, window)
                except DateReject as exc:
                    result.rejected.append(
                        {"label_raw": label, "date_raw": date_raw, "reason": str(exc)}
                    )
                    continue
                _emit(result, "academic", label, date_raw, rng)
    return result


def _academic_columns(header: tuple[str, ...]) -> list[tuple[int, str | None]] | None:
    """(column index, term key or None) for each date column, or None if unknown."""
    if header in ACADEMIC_HEADER_PREFIXES:
        return [(2, None)]
    if len(header) >= 2 and header[0] == "Event":
        cols = []
        for i, h in enumerate(header[1:], start=1):
            m = ACADEMIC_TERM_HEADER.match(h)
            if not m:
                return None
            cols.append((i, f"{m.group(1).lower()}-{m.group(2)}"))
        return cols
    return None
