"""
The registrar and academic calendar parsers (SERVICES_BUILD_PLAN R1), run on the
F1 fixtures. Pure: no network, no database.

Run from backend/ with:
    python -m pytest tests/test_campus_calendar.py
"""
from datetime import date
from pathlib import Path

import pytest

from campus.registration import event_keys
from campus.registration.parse_calendar import (
    parse_academic_calendar,
    parse_registrar_calendar,
)

FIX = Path(__file__).parent / "fixtures" / "campus"


@pytest.fixture(scope="module")
def registrar():
    return parse_registrar_calendar(
        (FIX / "registrar-calendar-fall-2026.html").read_text(encoding="utf-8"), "fall-2026"
    )


@pytest.fixture(scope="module")
def academic():
    return parse_academic_calendar(
        (FIX / "academic-calendar-2026-2027.html").read_text(encoding="utf-8"), "ay-2026-2027"
    )


def _by_key(parse, key):
    return [r for r in parse.rows if r["event_key"] == key]


def test_all_32_registrar_rows_parse_with_no_rejections(registrar):
    # 30 rows in `Dates | Event Description` plus 2 in `Date | Deadline`. None is
    # rejected: every printed weekday agrees with exactly one candidate year.
    assert registrar.data_rows == 32
    assert registrar.rejected == []
    assert registrar.headers == [("Dates", "Event Description"), ("Date", "Deadline")]
    assert registrar.unknown_headers == []
    distinct_cells = {(r["date_raw"], r["label_raw"]) for r in registrar.rows}
    assert len(distinct_cells) == 32


def test_last_updated_is_read_from_the_page(registrar, academic):
    assert registrar.last_updated == date(2026, 9, 28)
    assert academic.last_updated == date(2026, 6, 18)


def test_end_is_never_before_start(registrar, academic):
    for r in registrar.rows + academic.rows:
        assert r["end_date"] >= r["start_date"], r


def test_labels_and_dates_are_kept_word_for_word(registrar):
    row = _by_key(registrar, "grades_due")[0]
    assert row["label_raw"] == "Grades Due from Faculty" and row["date_raw"] == "Fri, Dec. 18"
    thanksgiving = _by_key(registrar, "holiday_thanksgiving")[0]
    assert thanksgiving["label_raw"] == "Thanksgiving Holidays - Campus Closed"
    # Block boundaries become spaces; the glued cell keeps both dates.
    assert thanksgiving["date_raw"] == "Thu, Nov. 26 Fri, Nov. 27"


def test_glued_date_cells(registrar):
    t = _by_key(registrar, "holiday_thanksgiving")[0]
    assert (t["start_date"], t["end_date"]) == (date(2026, 11, 26), date(2026, 11, 27))
    c = _by_key(registrar, "commencement")[0]
    assert c["date_raw"] == "Wed, Dec. 16 Thu, Dec. 17"
    assert (c["start_date"], c["end_date"]) == (date(2026, 12, 16), date(2026, 12, 17))


def test_year_boundary_across_december_and_january(registrar):
    winter = _by_key(registrar, "winter_recess")[0]
    assert (winter["start_date"], winter["end_date"]) == (date(2026, 12, 25), date(2027, 1, 22))
    assert _by_key(registrar, "holiday_christmas")[0]["start_date"] == date(2026, 12, 25)
    assert _by_key(registrar, "final_grade_submission_deadline")[0]["start_date"] == date(2027, 1, 3)
    assert _by_key(registrar, "grades_finalized")[0]["start_date"] == date(2027, 1, 12)
    assert _by_key(registrar, "grades_due")[0]["start_date"] == date(2026, 12, 18)


def test_a_weekday_mismatch_row_is_rejected_with_a_reason():
    html = (FIX / "registrar-calendar-fall-2026.html").read_text(encoding="utf-8")
    bad = html.replace("<p>Tue, Dec. 8</p>", "<p>Wed, Dec. 8</p>")
    assert bad != html
    parsed = parse_registrar_calendar(bad, "fall-2026")
    assert parsed.data_rows == 32 and len(parsed.rejected) == 1
    rej = parsed.rejected[0]
    assert rej["date_raw"] == "Wed, Dec. 8" and "weekday mismatch" in rej["reason"]
    assert not any(r["date_raw"] == "Wed, Dec. 8" for r in parsed.rows)


def test_core_registrar_keys_are_present(registrar):
    assert set(event_keys.CORE_REGISTRAR_KEYS) <= registrar.event_keys
    assert _by_key(registrar, "census_date")[0]["start_date"] == date(2026, 9, 16)
    assert _by_key(registrar, "instruction_first_day")[0]["start_date"] == date(2026, 8, 19)
    assert _by_key(registrar, "instruction_last_day")[0]["start_date"] == date(2026, 12, 7)
    finals = _by_key(registrar, "finals_period")[0]
    assert (finals["start_date"], finals["end_date"]) == (date(2026, 12, 9), date(2026, 12, 16))


def test_the_sep_15_cell_maps_to_five_keys_sharing_label_and_dates(registrar):
    sep15 = [r for r in registrar.rows if r["date_raw"] == "Tue, Sep. 15"]
    assert {r["event_key"] for r in sep15} == {
        "drop_without_w_last",
        "add_drop_last",
        "audit_crcr_last",
        "excess_units_petition_last",
        "instructor_drops_last",
    }
    assert len({r["label_raw"] for r in sep15}) == 1
    assert "Last Day to Add/Drop Classes via MySJSU" in sep15[0]["label_raw"]
    assert all(r["start_date"] == r["end_date"] == date(2026, 9, 15) for r in sep15)


def test_a_cell_matching_no_key_is_stored_with_a_null_key():
    html = (FIX / "registrar-calendar-fall-2026.html").read_text(encoding="utf-8")
    odd = html.replace("Grades Due from Faculty", "Pigeon Appreciation Day")
    parsed = parse_registrar_calendar(odd, "fall-2026")
    rows = [r for r in parsed.rows if r["label_raw"] == "Pigeon Appreciation Day"]
    assert len(rows) == 1 and rows[0]["event_key"] is None
    assert rows[0]["start_date"] == date(2026, 12, 18)


def test_academic_calendar_has_a_row_per_term_column(academic):
    assert academic.rejected == []
    assert academic.headers == [("Event", "Fall 2026", "Spring 2027"), ("Event", "Type", "Date")]
    firsts = _by_key(academic, "instruction_first_day")
    assert [r["start_date"] for r in firsts] == [date(2026, 8, 19), date(2027, 1, 27)]
    assert len(academic.rows) == 26


def test_academic_ranges_and_explicit_years(academic):
    finals = _by_key(academic, "finals_period")
    assert [(r["start_date"], r["end_date"]) for r in finals] == [
        (date(2026, 12, 9), date(2026, 12, 15)),
        (date(2027, 5, 19), date(2027, 5, 25)),
    ]
    assert finals[0]["date_raw"] == "December 9-11, 14-15"
    winter = _by_key(academic, "winter_recess")[0]
    assert (winter["start_date"], winter["end_date"]) == (date(2026, 12, 25), date(2027, 1, 2))
    assert _by_key(academic, "holiday_thanksgiving")[0]["end_date"] == date(2026, 11, 27)


def test_an_unknown_table_header_is_reported_not_parsed():
    html = "<html><body><table><tr><th>Foo</th><th>Bar</th></tr><tr><td>a</td><td>b</td></tr></table></body></html>"
    parsed = parse_registrar_calendar(html, "fall-2026")
    assert parsed.rows == [] and parsed.unknown_headers == [("Foo", "Bar")]
    assert parsed.last_updated is None


# -- Spring 2027's layout (measured on the first live dry run, 2026-10-01) -------


@pytest.fixture(scope="module")
def spring():
    return parse_registrar_calendar(
        (FIX / "registrar-calendar-spring-2027.html").read_text(encoding="utf-8"), "spring-2027"
    )


def test_the_headerless_spring_layout_is_recognised(spring):
    from campus.registration.parse_calendar import HEADERLESS_CALENDAR

    assert spring.headers == [HEADERLESS_CALENDAR]
    assert spring.unknown_headers == []
    assert spring.last_updated == date(2026, 9, 28)


def test_spring_year_markers_are_not_data_and_every_row_parses(spring):
    # 35 <tr>: two are year markers ('2026' | '', '2027' | ''), the rest are events.
    assert spring.data_rows >= 30
    assert spring.rejected == [], spring.rejected
    starts = sorted(r["start_date"] for r in spring.rows)
    assert starts[0] == date(2026, 10, 19)  # enrollment appointments viewable
    assert starts[-1].year == 2027


def test_spring_core_keys_and_term_dates(spring):
    missing = [k for k in event_keys.CORE_REGISTRAR_KEYS if k not in spring.event_keys]
    assert missing == [], missing
    first = _by_key(spring, "instruction_first_day")
    assert first and all(r["start_date"].year == 2027 for r in first)


def test_a_headerless_table_that_is_not_a_calendar_stays_unknown():
    html = (
        "<html><body><p>Last Updated Sep 28, 2026</p><table>"
        + "".join(f"<tr><td>Row {i}</td><td>x</td><td>y</td></tr>" for i in range(8))
        + "</table></body></html>"
    )
    parsed = parse_registrar_calendar(html, "spring-2027")
    assert parsed.unknown_headers == [()]
    assert parsed.rows == []
