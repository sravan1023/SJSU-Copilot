"""
Term keys and the date normaliser (SERVICES_BUILD_PLAN R1). Pure functions.

Run from backend/ with:
    python -m pytest tests/test_campus_dates.py
"""
from datetime import date

import pytest

from campus import dates, terms
from campus.dates import DateReject

FALL_26_ANCHOR = terms.term_window("fall-2026")
FALL_26 = dates.inference_window(*FALL_26_ANCHOR)


def test_term_key_matches_the_migration_scope_check():
    for ok in ("fall-2026", "spring-2027", "summer-2026", "winter-2027"):
        assert terms.TERM_KEY_RE.match(ok) and terms.SCOPE_KEY_RE.match(ok)
    for bad in ("Fall 2026", "fall-26", "fall-2026 ", "autumn-2026", "ay-2026-2027", "fall-3026"):
        assert not terms.TERM_KEY_RE.match(bad), bad
    assert terms.SCOPE_KEY_RE.match("ay-2026-2027")
    assert terms.is_ay_key("ay-2026-2027") and not terms.is_ay_key("ay-2026-2028")


def test_urls_are_fixed_templates_on_one_host():
    assert terms.url_for("registrar", "fall-2026") == "https://www.sjsu.edu/registrar/calendar/fall-2026.php"
    assert terms.url_for("academic", "ay-2026-2027") == "https://www.sjsu.edu/classes/calendar/2026-2027.php"
    assert terms.url_for("schedule", "spring-2027").endswith("/classes/schedules/spring-2027.php")
    assert terms.url_for("exams", "fall-2026").endswith("/classes/final-exam-schedule/fall-2026.php")
    assert terms.url_for("bursar", "fall-2026").endswith("/payment-due-dates/fall.php")
    for source, scope in (("registrar", "fall-2026"), ("academic", "ay-2026-2027")):
        assert terms.host_allowed(terms.url_for(source, scope))
    assert terms.ALLOWED_HOSTS == ("www.sjsu.edu",)
    assert not terms.host_allowed("https://gcp-web.sjsu.edu/x")
    assert not terms.host_allowed("http://www.sjsu.edu/x")
    with pytest.raises(ValueError):
        terms.url_for("registrar", "../etc")


def test_current_and_next_terms():
    assert terms.current_term(date(2026, 10, 1)) == "fall-2026"
    assert terms.next_terms(date(2026, 10, 1), 2) == ["spring-2027", "summer-2027"]
    assert terms.current_term(date(2027, 3, 1)) == "spring-2027"
    assert terms.next_terms(date(2027, 3, 1), 3) == ["summer-2027", "fall-2027", "spring-2028"]
    assert terms.current_ay(date(2026, 10, 1)) == "ay-2026-2027"
    assert terms.current_ay(date(2027, 3, 1)) == "ay-2026-2027"


def test_month_spellings():
    for word, n in (("Aug.", 8), ("Aug", 8), ("Sep", 9), ("Sep.", 9), ("Sept", 9), ("April", 4),
                    ("June", 6), ("Jan.", 1), ("december", 12)):
        assert dates.month_number(word) == n
    with pytest.raises(DateReject):
        dates.month_number("Foo")


def test_single_date_and_range():
    r = dates.parse_weekday_dates("Mon, April 13", FALL_26)
    assert (r.start, r.end) == (date(2026, 4, 13), date(2026, 4, 13))
    r = dates.parse_weekday_dates("Tue, April 21 - Tue, Sep 15", FALL_26)
    assert (r.start, r.end) == (date(2026, 4, 21), date(2026, 9, 15))


def test_glued_dates_split_at_the_weekday_prefix():
    assert dates.split_glued("Thu, Nov. 26 Fri, Nov. 27") == ["Thu, Nov. 26", "Fri, Nov. 27"]
    assert dates.split_glued("Wed, Dec. 16Thu, Dec. 17") == ["Wed, Dec. 16", "Thu, Dec. 17"]
    r = dates.parse_weekday_dates("Wed, Dec. 16Thu, Dec. 17", FALL_26)
    assert (r.start, r.end) == (date(2026, 12, 16), date(2026, 12, 17))
    r = dates.parse_weekday_dates("Thu, Nov. 26 Fri, Nov. 27", FALL_26)
    assert (r.start, r.end) == (date(2026, 11, 26), date(2026, 11, 27))


def test_the_weekday_must_confirm_the_candidate_nearest_the_term():
    # Dec 25 is in the Fall 2026 window in 2025 (a Thursday) and 2026 (a Friday).
    start, end = FALL_26
    assert start <= date(2025, 12, 25) <= end and start <= date(2026, 12, 25) <= end
    assert dates.parse_weekday_dates(
        "Fri, Dec. 25", FALL_26, anchor=FALL_26_ANCHOR
    ).start == date(2026, 12, 25)
    # 'Thu, Dec. 25' is last year's weekday copied onto this year's page. It fits
    # only Dec 25, 2025, a year from the term: rejected, never published as 2025.
    with pytest.raises(DateReject, match="stale weekday"):
        dates.parse_weekday_dates("Thu, Dec. 25", FALL_26, anchor=FALL_26_ANCHOR)
    # And as a range it can no longer become a 13-month span.
    with pytest.raises(DateReject):
        dates.parse_weekday_dates("Thu, Dec. 25 - Fri, Jan. 22", FALL_26, anchor=FALL_26_ANCHOR)


def test_january_rows_of_a_fall_term_belong_to_the_next_year():
    assert dates.parse_weekday_dates("Sun, Jan. 3", FALL_26).start == date(2027, 1, 3)
    assert dates.parse_weekday_dates("Tue, Jan. 12", FALL_26).start == date(2027, 1, 12)
    r = dates.parse_weekday_dates("Fri, Dec. 25 - Fri, Jan. 22", FALL_26)
    assert (r.start, r.end) == (date(2026, 12, 25), date(2027, 1, 22))


def test_a_weekday_mismatch_is_rejected_never_guessed():
    # Sep 15, 2026 is a Tuesday; no other year in the window has Sep 15.
    with pytest.raises(DateReject, match="weekday mismatch"):
        dates.parse_weekday_dates("Wed, Sep. 15", FALL_26)
    # A weekday that fits no candidate of a doubled date is rejected too.
    with pytest.raises(DateReject, match="weekday mismatch"):
        dates.parse_weekday_dates("Mon, Dec. 25", FALL_26)


def test_an_ambiguous_or_unparseable_cell_is_rejected():
    with pytest.raises(DateReject):
        dates.parse_prose_dates("December 9", FALL_26)  # no year, no weekday, two candidates
    with pytest.raises(DateReject):
        dates.parse_weekday_dates("Mon, Foo 13", FALL_26)
    with pytest.raises(DateReject):
        dates.parse_weekday_dates("Mon, April 13 Tue, April 14 Wed, April 15", FALL_26)
    with pytest.raises(DateReject, match="before start"):
        dates.parse_weekday_dates("Fri, Sep 18 - Tue, Sep 15", FALL_26)
    with pytest.raises(DateReject):
        dates.parse_weekday_dates("TBA", FALL_26)
    with pytest.raises(DateReject, match="invalid"):
        dates.parse_prose_dates("February 30, 2027", FALL_26)


def test_prose_dates():
    w = dates.inference_window(*terms.term_window("fall-2026"), before=2, after=2)
    r = dates.parse_prose_dates("November 26, 2026- November 27, 2026", w)
    assert (r.start, r.end) == (date(2026, 11, 26), date(2026, 11, 27))
    r = dates.parse_prose_dates("December 9-11, 14-15", w)
    assert (r.start, r.end) == (date(2026, 12, 9), date(2026, 12, 15))
    r = dates.parse_prose_dates("December 16 - 17", w)
    assert (r.start, r.end) == (date(2026, 12, 16), date(2026, 12, 17))
    r = dates.parse_prose_dates("December 25, 2026- January 2, 2027", w)
    assert r.end == date(2027, 1, 2)


# -- Review follow-ups (2026-10-01) ---------------------------------------------


def test_glued_dates_far_apart_are_rejected_not_made_a_range():
    # Two unrelated dates run together with no dash must not become Sep 7 - Nov 11.
    with pytest.raises(DateReject, match="no dash"):
        dates.parse_weekday_dates("Mon, Sep. 7Wed, Nov. 11", FALL_26, anchor=FALL_26_ANCHOR)
    # A dashed range of the same span is an explicit range and is kept.
    r = dates.parse_weekday_dates("Mon, Sep. 7 - Wed, Nov. 11", FALL_26, anchor=FALL_26_ANCHOR)
    assert (r.start, r.end) == (date(2026, 9, 7), date(2026, 11, 11))
    # Spring 2027 writes a range with no space before the dash.
    r = dates.parse_weekday_dates("Thu, Nov. 26- Fri, Nov. 27", FALL_26, anchor=FALL_26_ANCHOR)
    assert (r.start, r.end) == (date(2026, 11, 26), date(2026, 11, 27))


def test_a_range_longer_than_the_cap_is_rejected():
    assert dates.MAX_RANGE_DAYS == 200
    with pytest.raises(DateReject, match="longer than"):
        dates.parse_weekday_dates("Tue, April 21 - Fri, Jan. 22", FALL_26, anchor=FALL_26_ANCHOR)
    # The longest real range, a registration period, fits.
    r = dates.parse_weekday_dates("Tue, April 21 - Tue, Sep 15", FALL_26, anchor=FALL_26_ANCHOR)
    assert (r.end - r.start).days == 147


def test_a_year_marker_that_disagrees_with_inference_rejects_the_row():
    spring = terms.term_window("spring-2027")
    window = dates.inference_window(*spring)
    r = dates.parse_weekday_dates("Mon, Oct. 19", window, anchor=spring, year_hint=2026)
    assert r.start == date(2026, 10, 19)
    with pytest.raises(DateReject, match="year marker"):
        dates.parse_weekday_dates("Mon, Oct. 19", window, anchor=spring, year_hint=2027)


def test_term_keys_reject_a_trailing_newline():
    for key in ("fall-2026\n", "ay-2026-2027\n"):
        assert not terms.SCOPE_KEY_RE.match(key)
    assert not terms.TERM_KEY_RE.match("fall-2026\n")
    assert not terms.is_term_key("fall-2026\n")
