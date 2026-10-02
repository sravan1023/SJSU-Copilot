"""Date normaliser for the printed calendars. Pure functions, no I/O.

SJSU's pages print no year in the registrar calendar ('Tue, Sep. 15'), spell
months several ways ('Aug.', 'Sep', 'Sept.', 'April'), glue two dates into one
cell with no separator ('Wed, Dec. 16Thu, Dec. 17') and write ranges as
'Tue, April 21 - Tue, Sep 15'. This turns a cell into `(start, end)` dates, or
rejects it with a reason. The rules:

* **The year is inferred inside a window**, [term start - 9 months, term end +
  2 months]. A month and day can fall inside it twice: Dec 25 is in the Fall
  2026 window in both 2025 and 2026, and so is Jan 3.
* **The stated weekday confirms the candidate nearest the term.** When a month
  and day has two candidate years, the one nearest the term (`anchor`) is the
  expected one, and the printed weekday must agree with it. Two candidates a year
  apart differ by only one or two weekdays, so a stale weekday copied from last
  year's page ('Thu, Dec. 25') would otherwise match the far year and be
  published with confidence. Weekday matches only the far year: rejected. No
  match at all: rejected. A date with no weekday and two candidates: rejected.
  Never guess.
* A glued pair is a range only when the two dates are within a week of each
  other: 'Thu, Nov. 26 Fri, Nov. 27' is Nov 26 to Nov 27. Two unrelated dates
  run together are rejected, not turned into a months-long range.
* Any range longer than `MAX_RANGE_DAYS` is rejected.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date

MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

# A weekday word followed by a comma, not preceded by a letter. The negative
# look-behind is what splits 'Dec. 16Thu, Dec. 17' (a digit precedes 'Thu') while
# leaving words such as 'Month,' alone.
_WEEKDAY_PREFIX = re.compile(
    r"(?<![A-Za-z])(?P<wd>Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\.?,\s*"
)
_DATE_ITEM = re.compile(
    r"^(?P<mon>[A-Za-z]{3,9})\.?\s+(?P<day>\d{1,2})(?!\d)(?:,?\s*(?P<year>\d{4}))?$"
)
_TOKEN = re.compile(
    r"(?:(?P<mon>[A-Za-z]{3,9})\.?\s+)?(?P<day>\d{1,2})(?!\d)(?:,\s*(?P<year>\d{4})(?!\d))?"
)
_SEPARATORS = re.compile(r"^[\s,\-–—]*$")
_TRAILING_SEP = re.compile(r"[\s\-–—]+$")
_DASH = re.compile(r"[\-–—]")

# The longest legitimate range on SJSU's calendars is a registration period
# (Apr 21 - Sep 15, 147 days). Anything longer is a year-inference error.
MAX_RANGE_DAYS = 200
# Two dates glued together with no dash are a range only if they are this close.
MAX_GLUED_DAYS = 7


class DateReject(ValueError):
    """The cell cannot be turned into dates. `str(exc)` is the reason."""


@dataclass(frozen=True)
class DateRange:
    start: date
    end: date


def month_number(word: str) -> int:
    """'Aug.', 'Sept', 'sep', 'April' -> month number. Anything else raises."""
    w = word.strip().rstrip(".").lower()
    if w == "sept":
        w = "september"
    if w in MONTHS:
        return MONTHS[w]
    if len(w) == 3:
        for name, num in MONTHS.items():
            if name.startswith(w):
                return num
    raise DateReject(f"unknown month {word!r}")


def weekday_number(word: str) -> int:
    w = word.lower()[:3]
    for i, name in enumerate(_WEEKDAYS):
        if name.startswith(w):
            return i
    raise DateReject(f"unknown weekday {word!r}")


def add_months(d: date, months: int) -> date:
    idx = d.year * 12 + (d.month - 1) + months
    year, month0 = divmod(idx, 12)
    month = month0 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def inference_window(
    term_start: date, term_end: date, *, before: int = 9, after: int = 2
) -> tuple[date, date]:
    """[term start - `before` months, term end + `after` months]."""
    return add_months(term_start, -before), add_months(term_end, after)


def _day_name(i: int) -> str:
    return _WEEKDAYS[i][:3].title()


def resolve_date(
    month: int,
    day: int,
    window: tuple[date, date],
    *,
    weekday: int | None = None,
    year: int | None = None,
    anchor: tuple[date, date] | None = None,
) -> date:
    """Place a month and day in the window, or raise `DateReject`.

    An explicit year must still belong to the requested calendar's window.
    `anchor` is the term's (start, end): with two candidate years, the one
    nearest it is the expected date and the weekday must agree with that one.
    """
    if year is not None:
        try:
            d = date(year, month, day)
        except ValueError:
            raise DateReject(f"invalid date {year}-{month:02d}-{day:02d}") from None
        if not window[0] <= d <= window[1]:
            raise DateReject(f"{d.isoformat()} is outside the inference window")
        if weekday is not None and d.weekday() != weekday:
            raise DateReject(
                f"weekday mismatch: stated {_day_name(weekday)}, "
                f"{d.isoformat()} is a {_day_name(d.weekday())}"
            )
        return d

    candidates: list[date] = []
    for y in range(window[0].year, window[1].year + 1):
        try:
            d = date(y, month, day)
        except ValueError:
            continue
        if window[0] <= d <= window[1]:
            candidates.append(d)
    if not candidates:
        raise DateReject(f"{month}/{day} is outside the inference window")
    if weekday is not None:
        matching = [d for d in candidates if d.weekday() == weekday]
        if not matching:
            raise DateReject(
                f"weekday mismatch: stated {_day_name(weekday)}, no candidate "
                f"({', '.join(d.isoformat() for d in candidates)}) agrees"
            )
        if len(matching) > 1:
            raise DateReject("weekday matches more than one candidate year")
        if anchor is not None and len(candidates) > 1:
            expected = min(candidates, key=lambda d: _distance(d, anchor))
            if matching[0] != expected:
                raise DateReject(
                    f"weekday mismatch: stated {_day_name(weekday)} fits only "
                    f"{matching[0].isoformat()}, a year away from the term; "
                    f"{expected.isoformat()} is a {_day_name(expected.weekday())} "
                    "(stale weekday?)"
                )
        return matching[0]
    if len(candidates) > 1:
        raise DateReject(
            "ambiguous year with no weekday: " + ", ".join(d.isoformat() for d in candidates)
        )
    return candidates[0]


def _distance(d: date, span: tuple[date, date]) -> int:
    """Days from `d` to the nearest edge of `span`; 0 inside it."""
    if d < span[0]:
        return (span[0] - d).days
    if d > span[1]:
        return (d - span[1]).days
    return 0


def _check_range(start: date, end: date) -> None:
    if end < start:
        raise DateReject(f"end {end.isoformat()} is before start {start.isoformat()}")
    if (end - start).days > MAX_RANGE_DAYS:
        raise DateReject(
            f"range {start.isoformat()} .. {end.isoformat()} is longer than "
            f"{MAX_RANGE_DAYS} days"
        )


def has_weekday_prefix(text: str) -> bool:
    return _WEEKDAY_PREFIX.search(text) is not None


def leading_weekday(item: str) -> int | None:
    """The weekday number an item such as 'Tue, Sep. 15' starts with, if any."""
    m = _WEEKDAY_PREFIX.match(item)
    return weekday_number(m.group("wd")) if m else None


def split_glued(text: str) -> list[str]:
    """Split a cell at each weekday prefix: 'Thu, Nov. 26Fri, Nov. 27' -> two items.

    Separators left dangling at the end of an item (' - ') are removed. A cell
    with no weekday prefix comes back as one item.
    """
    text = " ".join(text.split())
    starts = [m.start() for m in _WEEKDAY_PREFIX.finditer(text)]
    if not starts:
        return [text] if text else []
    if text[: starts[0]].strip(" -–—"):
        raise DateReject(f"text before the first weekday: {text[: starts[0]]!r}")
    items = []
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else len(text)
        items.append(_TRAILING_SEP.sub("", text[s:e]).strip())
    return items


def parse_weekday_dates(
    text: str,
    window: tuple[date, date],
    *,
    anchor: tuple[date, date] | None = None,
    year_hint: int | None = None,
) -> DateRange:
    """A registrar cell: 'Mon, April 13', 'Tue, April 21 - Tue, Sep 15' or a glued pair.

    `anchor` is the term's nominal (start, end), see `resolve_date`. `year_hint`
    is the year a page's own marker row printed above this row ('2026'); the
    start date must fall in it, so a disagreement between inference and the page
    rejects the row instead of publishing either.
    """
    items = split_glued(text)
    if not items:
        raise DateReject("empty date cell")
    if len(items) > 2:
        raise DateReject(f"{len(items)} dates in one cell")
    normalised = " ".join(text.split())
    glued = False
    if len(items) == 2:
        second = list(_WEEKDAY_PREFIX.finditer(normalised))[1].start()
        glued = not _DASH.search(normalised[:second].split(",", 1)[-1])
    resolved = []
    for item in items:
        m = _WEEKDAY_PREFIX.match(item)
        if m is None:
            raise DateReject(f"no weekday prefix in {item!r}")
        rest = item[m.end():].strip()
        dm = _DATE_ITEM.match(rest)
        if dm is None:
            raise DateReject(f"cannot read date {rest!r}")
        year = int(dm.group("year")) if dm.group("year") else None
        resolved.append(
            resolve_date(
                month_number(dm.group("mon")),
                int(dm.group("day")),
                window,
                weekday=weekday_number(m.group("wd")),
                year=year,
                anchor=anchor,
            )
        )
    start, end = resolved[0], resolved[-1]
    _check_range(start, end)
    if glued and (end - start).days > MAX_GLUED_DAYS:
        raise DateReject(
            f"two dates run together with no dash, {(end - start).days} days apart"
        )
    if year_hint is not None and start.year != year_hint:
        raise DateReject(
            f"inferred {start.isoformat()} but the page's year marker says {year_hint}"
        )
    return DateRange(start, end)


def parse_prose_dates(
    text: str, window: tuple[date, date], *, default_year: int | None = None
) -> DateRange:
    """An academic-calendar cell: 'December 7, 2026', 'December 9-11, 14-15',
    'November 26, 2026- November 27, 2026', 'May 26 - 28'.

    A day with no month takes the previous month; a date with no year takes
    `default_year`, else is placed in the window (and rejected if that is
    ambiguous: these cells print no weekday to decide with). The result runs from
    the first date to the last.
    """
    text = " ".join(text.replace("*", " ").split())
    if not text:
        raise DateReject("empty date cell")
    tokens = list(_TOKEN.finditer(text))
    if not tokens:
        raise DateReject(f"no date in {text!r}")
    leftover = _TOKEN.sub("", text)
    if not _SEPARATORS.match(leftover):
        raise DateReject(f"unrecognised text in date cell: {leftover.strip()!r}")

    month = None
    resolved = []
    for t in tokens:
        if t.group("mon"):
            month = month_number(t.group("mon"))
        if month is None:
            raise DateReject(f"day with no month in {text!r}")
        year = int(t.group("year")) if t.group("year") else default_year
        resolved.append(resolve_date(month, int(t.group("day")), window, year=year))
    start, end = resolved[0], resolved[-1]
    _check_range(start, end)
    return DateRange(start, end)
