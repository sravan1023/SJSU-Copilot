"""Does the answer to this question expire?

The knowledge base is a snapshot. For most questions that is fine -- how to apply
for CPT does not change between crawls. For some it is actively harmful: telling
a student the add/drop deadline from a corpus crawled five weeks ago is worse
than saying nothing, because it is wrong with the confidence of a citation.

So a question classified as time-sensitive skips the KB and goes to live search
regardless of how good the KB hits look.

**The asymmetry matters more than the accuracy.** A false positive costs latency
and loses a KB hit -- the answer is still correct, just slower. A false negative
serves a stale date. So this leans toward flagging, and the test that guards it
asserts every labelled time-sensitive question is caught, not that every FAQ is
left alone.

**Why this is not `conversation_state.URGENCY_PATTERN`.** That measures the
*user's* urgency -- "asap", "in a hurry", "running out of time" -- which is a
different axis pointing the opposite way. Someone in a hurry wants the 600ms KB
answer, not a six-second crawl. Merging the two would make the KB fire least
often exactly when it helps most.

## Calibration

The patterns below were fitted to `backend/bench/questions.jsonl`, which labels
eight questions `fresh` and eight `faq`. Three patterns that looked obviously
right on paper had to be dropped, and each one is a false positive on a real
labelled question:

* **`deadline` alone** -- "Where do I find the SJSU academic calendar with
  faculty deadlines?" is asking where a page is, not what a date is. Kept only
  in a `when ...` context.
* **`schedule` alone** -- "How do I schedule a campus tour?" is a verb there.
* **`still`** -- "Can SJSU alumni still use the Career Center?" is about
  eligibility, not about this week.

`calendar` is excluded for the same reason as `deadline`. Writing those four into
the pattern list unexamined would have pushed a quarter of the FAQ set onto the
slow path permanently -- which is how a knowledge base ends up built and unused.
"""
from __future__ import annotations

import re

# Unconditional signals: if any of these appear, the answer has a shelf life.
_PATTERNS = (
    # Anchored to the speaker's present. "this semester", "this fall".
    #
    # The optional modifier is not decoration. "this year" was caught and **"this
    # academic year" was not**, because one word sat between the two the pattern
    # required to be adjacent -- a false negative found against
    # bench/questions.jsonl's `alumni-long-2`, "When are the career fairs this
    # academic year". That question is labelled `long`, not `fresh`, so no
    # assertion in test_freshness.py was looking at it. A stored answer would have
    # served last year's career fair dates with a citation attached, which is
    # precisely the failure this module exists to prevent.
    re.compile(
        r"\bthis\s+(?:academic\s+|school\s+|calendar\s+|fiscal\s+|past\s+|coming\s+)?"
        r"(semester|term|week|weekend|month|year|quarter|"
        r"fall|spring|summer|winter)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(today|tonight|tomorrow|right now|currently|at the moment|as of now|"
        r"this coming)\b",
        re.IGNORECASE,
    ),
    # "the next event", "upcoming workshops", "what's happening".
    re.compile(r"\b(next|upcoming|happening)\b", re.IGNORECASE),
    # A concrete year dates the answer, and a corpus cannot know it has passed.
    re.compile(r"\b20\d{2}\b"),
    # Opening hours change by term, and during finals they change by week.
    re.compile(r"\b(open late|opening hours|hours of operation|still open|now open)\b",
               re.IGNORECASE),
    # Events have dates by definition.
    re.compile(r"\b(concerts?|game day|ticket sales)\b", re.IGNORECASE),
)

# Contextual: a date word only counts when the question is asking *when*.
# "When is the application deadline" expires; "where are the deadlines listed"
# does not.
_WHEN_ASKING = re.compile(
    r"\bwhen\b[^?]*\b(deadline|due|last day|cut.?off|close[sd]?|open[sd]?|start|end)\b",
    re.IGNORECASE,
)
_DATE_ASKING = re.compile(
    r"\b(what|which)\s+(date|day|time)\b",
    re.IGNORECASE,
)


def is_time_sensitive(question: str) -> bool:
    """True when a stored answer could be out of date in a way that misleads."""
    if not question:
        return False
    text = question.strip()
    if any(pattern.search(text) for pattern in _PATTERNS):
        return True
    return bool(_WHEN_ASKING.search(text) or _DATE_ASKING.search(text))


def explain(question: str) -> list[str]:
    """Which patterns fired. For `kb.eval_retrieval` and for debugging a surprise."""
    hits: list[str] = []
    for pattern in _PATTERNS:
        match = pattern.search(question or "")
        if match:
            hits.append(match.group(0).lower())
    for name, pattern in (("when-asking", _WHEN_ASKING), ("date-asking", _DATE_ASKING)):
        if pattern.search(question or ""):
            hits.append(name)
    return hits
