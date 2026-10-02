"""R6: registration intent classifier. Pure functions, offline."""
import html
import json
import re
from pathlib import Path

import pytest

from services.reg_intent import classify

ROOT = Path(__file__).resolve().parent.parent
KEYS = json.loads((ROOT / "campus" / "registration" / "event_keys.json").read_text(encoding="utf-8"))
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "campus"

CARD_CASES = [
    ("drop_without_w_last", "When is the last day to drop without a W?"),
    ("add_drop_last", "What is the add/drop deadline?"),
    ("drop_full_refund_last", "last day to drop for a full refund"),
    ("census_date", "When is census date this semester?"),
    ("waitlist_end", "When does the waitlist end?"),
    ("permission_number_required", "When do I need a permission number to add?"),
    ("instruction_first_day", "When is the first day of instruction?"),
    ("instruction_last_day", "When is the last day of classes?"),
    ("finals_period", "when is finals week?"),
    ("grades_due", "When are grades due?"),
    ("grades_viewable", "When are grades viewable?"),
    ("late_drop_petition_last", "late drop petition deadline"),
    ("graduation_application_priority_deadline",
     "What is the graduation application deadline for priority registration?"),
    ("commencement", "When is commencement?"),
    ("registration_period_new_grad", "When is the registration period for new graduate students?"),
    ("holiday_labor_day", "Is campus closed on Labor Day?"),
    ("holiday_thanksgiving", "when is thanksgiving"),
    ("audit_crcr_last", "deadline to request credit/no credit"),
    ("instructor_drops_last", "When is the instructor drops deadline?"),
]


@pytest.mark.parametrize("key,question", CARD_CASES)
def test_card_positives(key, question):
    r = classify(question)
    assert r is not None and r.route == "card", (question, r)
    assert r.event_keys == (key,), (question, r)


def test_every_card_key_has_a_case_or_is_covered():
    # Guards the table above against silently losing the spec's required keys.
    required = {
        "drop_without_w_last", "add_drop_last", "drop_full_refund_last", "census_date",
        "waitlist_end", "permission_number_required", "instruction_first_day",
        "instruction_last_day", "finals_period", "grades_due", "grades_viewable",
        "late_drop_petition_last", "graduation_application_priority_deadline", "commencement",
    }
    assert required <= {k for k, _ in CARD_CASES}
    assert required <= set(KEYS["keys"])


def test_multi_key_and_open_ended_go_to_context():
    r = classify("What are the drop deadlines?")
    assert r.route == "context"
    assert {"drop_without_w_last", "drop_full_refund_last", "add_drop_last"} <= set(r.event_keys)
    r = classify("When are the drop without a W and the refund deadlines?")
    assert r.route == "context" and len(r.event_keys) == 2
    r = classify("important dates for fall")
    assert r.route == "context" and r.term_hint == "fall"
    r = classify("what are the campus holidays this semester?")
    assert r.route == "context" and "holiday_labor_day" in r.event_keys
    # Several deadlines in one registrar cell are separate keys, so no card.
    assert classify("what are the Sep 15 deadlines? audit request and drop without a W").route == "context"


def test_specific_beats_generic_and_contained_spans_dropped():
    r = classify("when is the add/drop deadline")  # "drop deadline" is also a group phrase
    assert r.route == "card" and r.event_keys == ("add_drop_last",)
    r = classify("when does the late add post census requirement begin")
    assert r.event_keys == ("late_add_post_census_begins",)  # not also census_date


@pytest.mark.parametrize("q", [
    "what happens if I miss the drop deadline",
    "can I drop after the deadline if I'm sick",
    "should I drop before census date",
    "how do I get a permission number",
    "why is the waitlist deadline so early",
    "is there an exception to the last day to drop without a W",
    "can I file a late drop petition",
    "late drop petition deadline if I have a medical reason",
    "what is the add/drop deadline unless I have a hold",
    "what happens at the census date",
])
def test_policy_and_conditional_questions_abstain(q):
    r = classify(q)
    assert r is not None and r.route == "abstain", (q, r)


def test_abstain_words_inside_an_event_name_do_not_abstain():
    # "late" and "petition" are part of the event's own name, so they are blanked
    # out before the abstain check; the same words elsewhere still abstain.
    assert classify("late drop petition deadline").route == "card"
    assert classify("when is the last day to file a late drop petition").route == "card"
    assert classify("can I file a late drop petition").route == "abstain"
    assert classify("late drop petition deadline, I was late to class").route == "abstain"


def test_final_exam_with_course_codes():
    for q, code in [
        ("when is the CS 146 final exam?", "CS 146"),
        ("what time is my math 30p final", "MATH 30P"),
        ("ENGL 1A final exam date", "ENGL 1A"),
    ]:
        r = classify(q)
        assert r.route == "card" and r.intent == "final_exam" and r.course_code == code, (q, r)
    assert classify("can I take my CS 146 final early").route == "abstain"
    assert classify("CS 146 and CS 157A final exam times").route == "context"
    r = classify("what is the final exam schedule")
    assert r.route == "context" and r.intent == "final_exam" and r.course_code is None


@pytest.mark.parametrize("q", [
    "do I need an F-1 visa final exam letter",
    "H-1B final exam",
    "I-20 final exam deadline",
    "W-2 final exam question",
    "my DS 160 exam appointment",
    "final exam on dec 9",
    "when is exam 2",
])
def test_visa_and_word_lookalikes_are_not_course_codes(q):
    r = classify(q)
    assert r is None or r.course_code is None, (q, r)
    assert r is None or r.route != "card" or r.intent != "final_exam", (q, r)


def test_term_hints():
    assert classify("when is the census date for fall 2026").term_hint == "fall 2026"
    assert classify("when is the census date in spring").term_hint == "spring"
    assert classify("when is the census date this semester").term_hint == "this semester"
    assert classify("when is the census date next semester").term_hint == "next semester"
    assert classify("when is the census date").term_hint is None


@pytest.mark.parametrize("q", [
    "", "   ", "thanks!", "what is the capital of France",
    "where is the library", "who is the CS department chair",
    "how much is parking", "tell me about CS 146",
    "when does the F-1 OPT application open",
])
def test_non_registration_questions_return_none(q):
    assert classify(q) is None, q


def _fixture_cells():
    cells = []
    for name in ("registrar-calendar-fall-2026.html", "academic-calendar-2026-2027.html"):
        raw = (FIXTURES / name).read_text(encoding="utf-8", errors="replace")
        for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", raw, re.S):
            text = html.unescape(re.sub(r"<[^>]+>", " ", cell))
            cells.append(re.sub(r"\s+", " ", text).strip())
    return [c for c in cells if c]


def test_every_key_has_a_label_pattern_matching_a_real_fixture_label():
    cells = _fixture_cells()
    assert len(cells) > 60  # the fixtures actually loaded
    missing = []
    for key, spec in KEYS["keys"].items():
        assert spec["label_patterns"] and spec["question_patterns"], key
        assert spec["intent"] in ("deadline", "term_dates"), key
        if not any(re.search(p, c, re.I) for p in spec["label_patterns"] for c in cells):
            missing.append(key)
    assert not missing, missing


def test_group_keys_all_exist():
    for name, g in KEYS["groups"].items():
        assert g["keys"], name
        assert set(g["keys"]) <= set(KEYS["keys"]), name


def test_sep_15_cell_yields_five_distinct_keys():
    cell = next(c for c in _fixture_cells() if "Add/Drop Classes via MySJSU" in c)
    found = {
        k for k, s in KEYS["keys"].items()
        if any(re.search(p, cell, re.I) for p in s["label_patterns"])
    }
    assert found == {
        "drop_without_w_last", "add_drop_last", "audit_crcr_last",
        "excess_units_petition_last", "instructor_drops_last",
    }


# -- R7 fixes (2026-10-01): each is a miss the labelled set measured --------------


@pytest.mark.parametrize("q,route", [
    # A break is more than the holiday: Nov 25 (non-instructional) to Nov 27.
    ("When is thanksgiving break?", "context"),
    # "Semester end" is instruction, finals and make-up day, not one date.
    ("When does the semester end?", "context"),
    # Several rows wanted, or no date asked for.
    ("tell me about the census date", "context"),
    ("what deadlines are coming up before census?", "context"),
    # A final exam plus another event is two questions.
    ("When is the CS 146 final exam and the census date?", "context"),
])
def test_multi_row_questions_are_never_cards(q, route):
    r = classify(q)
    assert r is not None and r.route == route, (q, r)


@pytest.mark.parametrize("q", [
    "Is there a fee to drop a class after the refund deadline?",  # 'after' inside a pattern gap
    "my add/drop deadline passed, what now",
    "Does dropping before the census date affect my financial aid?",
    "When is the last day to drop a class with a W grade?",  # the late-drop process
])
def test_consequence_and_gap_wording_never_cards(q):
    r = classify(q)
    assert r is None or r.route != "card", (q, r)


@pytest.mark.parametrize("q", [
    "Is the Rec Center open on Labor Day?",
    "When is the H-1B cap registration period?",
    "What are the dining hall hours during spring break?",
])
def test_facility_and_immigration_questions_are_not_lookups(q):
    assert classify(q) is None


def test_final_needs_the_word_final_and_not_final_day():
    assert classify("When is the CS 146 exam 2?") is None or classify("When is the CS 146 exam 2?").route != "card"
    r = classify("What's the final day to drop CS 146?")
    assert r is None or r.intent != "final_exam", r
    r = classify("When is the CS 146 final?")
    assert r and r.route == "card" and r.course_code == "CS 146"


def test_subject_codes_ending_in_a_digit_and_capitalised_me():
    r = classify("BUS1 21 final exam time")
    assert r and r.course_code == "BUS1 21", r
    r = classify("When is the ME 20 final?")
    assert r and r.course_code == "ME 20", r


def test_a_followup_is_capped_at_context():
    text = "When do classes start for fall 2026? and when do they end?"
    assert classify("When do classes start for fall 2026?").route == "card"
    assert classify(text, followup=True).route == "context"
