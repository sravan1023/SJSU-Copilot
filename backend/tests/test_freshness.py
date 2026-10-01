"""
Does the knowledge base know when not to answer?

Run from backend/ with:
    python -m pytest tests/test_freshness.py

**This is the highest-consequence test in Phase 4.** A false negative here is how
a stale add/drop deadline reaches a student with a citation attached, which is
worse than no answer at all. No network, no tokens, under a second.

The labelled set is `backend/bench/questions.jsonl`, which already tags eight
questions `fresh` and eight `faq` -- written for the benchmark, before any of
this existed, so it is not a set fitted to the classifier after the fact.

Only those two kinds are asserted. `followup` ("and what about transfers?"),
`long` and `restricted` are genuinely ambiguous in isolation, and several never
reach retrieval at all -- `prepare_rag_query`'s meta and conversational gates
catch them first.
"""
import json
import pathlib

import pytest

from services.freshness import explain, is_time_sensitive

QUESTIONS = pathlib.Path(__file__).resolve().parent.parent / "bench" / "questions.jsonl"


def _labelled(kind: str) -> list[tuple[str, str]]:
    rows = [
        json.loads(line)
        for line in QUESTIONS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return [
        (r["id"], r["messages"][-1]["content"]) for r in rows if r["kind"] == kind
    ]


def test_the_labelled_set_is_present():
    """A silently empty parametrize would make every assertion below vacuous."""
    assert len(_labelled("fresh")) == 8
    assert len(_labelled("faq")) == 8


@pytest.mark.parametrize("qid,question", _labelled("fresh"), ids=lambda v: v if isinstance(v, str) and v.startswith(("student", "alumni", "guest", "faculty")) else "")
def test_time_sensitive_questions_are_caught(qid, question):
    """The direction that matters. A miss here serves a stale date."""
    assert is_time_sensitive(question), f"{qid}: {question!r} was not flagged"


@pytest.mark.parametrize("qid,question", _labelled("faq"), ids=lambda v: v if isinstance(v, str) and v.startswith(("student", "alumni", "guest", "faculty")) else "")
def test_durable_questions_are_left_to_the_knowledge_base(qid, question):
    """A false positive is safe but permanently costs the KB that question."""
    assert not is_time_sensitive(question), (
        f"{qid}: {question!r} was flagged by {explain(question)}"
    )


# ── The three patterns that had to be dropped ─────────────────────────────────
#
# Each looked obviously correct and is a false positive on a real labelled
# question. Pinned individually so nobody re-adds one from first principles.


def test_deadline_alone_is_not_enough():
    """"Where do I find the calendar with faculty deadlines?" asks for a page."""
    assert not is_time_sensitive("Where do I find the SJSU academic calendar with faculty deadlines?")
    # But asking *when* one falls does expire.
    assert is_time_sensitive("When is the application deadline?")


def test_schedule_as_a_verb_is_not_temporal():
    assert not is_time_sensitive("How do I schedule a campus tour at SJSU?")


def test_still_is_about_eligibility_not_time():
    assert not is_time_sensitive("Can SJSU alumni still use the Career Center?")


def test_calendar_alone_is_not_enough():
    assert not is_time_sensitive("Where is the SJSU academic calendar?")


# ── Shape ─────────────────────────────────────────────────────────────────────


def test_a_bare_year_is_treated_as_dated():
    """A corpus cannot know that fall 2026 has passed."""
    assert is_time_sensitive("When are fall 2026 final grades due?")
    assert is_time_sensitive("What changed in the 2027 catalog?")


def test_relative_time_is_caught():
    for phrase in (
        "this semester", "this week", "this weekend", "this fall",
        "today", "tomorrow", "right now", "currently",
    ):
        assert is_time_sensitive(f"Is the library open {phrase}?"), phrase


def test_empty_and_whitespace_are_not_time_sensitive():
    assert not is_time_sensitive("")
    assert not is_time_sensitive("   ")
    assert not is_time_sensitive(None or "")


def test_explain_names_what_fired():
    """`kb.eval_retrieval` prints this, and it is how a surprise gets diagnosed."""
    hits = explain("Is the SJSU King Library open late during finals week this fall?")
    assert hits, "a flagged question must say why"
    assert not explain("Where can visitors park at San Jose State?")


def test_it_is_not_the_urgency_classifier():
    """conversation_state.URGENCY_PATTERN is a different axis, pointing the other way.

    A hurried user wants the fast KB answer. If these two were ever merged, the
    knowledge base would be bypassed precisely when it is most useful.
    """
    for hurried in ("I need this asap", "urgent, please", "I'm in a hurry"):
        assert not is_time_sensitive(hurried), hurried


# ── The false negative found by the corpus-coverage pass ───────────────────────


def test_a_modifier_between_this_and_the_unit_does_not_hide_it():
    """"this academic year" must flag, exactly as "this year" does.

    Found on 2026-09-30 while checking which bench questions could reach the
    knowledge base: `alumni-long-2`, "When are the career fairs this academic
    year, and how do employers register?", was not flagged. It is labelled `long`
    rather than `fresh`, so nothing above was looking at it, and a stored answer
    would have served last year's career fair dates.
    """
    assert is_time_sensitive("When are the career fairs this academic year?")
    for modifier in ("academic", "school", "calendar", "fiscal"):
        for unit in ("year", "semester", "term"):
            question = f"What changes this {modifier} {unit}?"
            assert is_time_sensitive(question), question


def test_every_labelled_question_is_classified_deliberately():
    """No question in the bench set may be unexamined.

    `fresh` and `faq` are asserted above. This covers the rest: each remaining
    question is printed with its verdict if it is time-sensitive, so a reviewer
    can see the whole set rather than only the two kinds with opinions attached.
    The assertion is weak on purpose -- the point is that adding a question to
    questions.jsonl forces someone to look at it, which is what failed for
    `alumni-long-2`.
    """
    rows = [
        json.loads(line)
        for line in QUESTIONS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    kinds = {r["kind"] for r in rows}
    assert kinds == {"faq", "fresh", "long", "followup", "restricted"}, kinds

    # Anything naming a time unit must be flagged, whatever its label says.
    for row in rows:
        question = row["messages"][-1]["content"]
        lowered = question.lower()
        if any(
            phrase in lowered
            for phrase in ("this academic year", "this semester", "this year", "this term")
        ):
            assert is_time_sensitive(question), f"{row['id']}: {question!r}"
