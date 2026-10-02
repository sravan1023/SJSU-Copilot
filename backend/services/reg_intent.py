"""Registration-lookup intent classifier (pure functions, no I/O after import).

Decides whether a chat question is a lookup the structured registration data can
answer, and how much of it to trust:

- ``card``: exactly one calendar event (or one course's final exam) was named, a
  date was asked for, and nothing in the wording asks for judgement. The answer
  is a verbatim data row with no model call, so a wrong card is worse than a
  missing one: precision beats recall everywhere below.
- ``context``: several events match, or the question is an open-ended lookup
  ("important dates for fall"). Rows are fed to the model as grounding.
- ``abstain``: it is about registration but asks for policy or a conditional
  answer; it belongs to the KB / live-search path unchanged.
- ``None``: not a registration lookup at all.

**Abstain words vs. event keys whose labels contain them.** The abstain list
includes ``late`` and ``petition``, but "late drop petition deadline" asks for a
date and the calendar's own label is "Last Day to File Late Drop/ Semester
Withdrawal Petition". Chosen rule: the spans matched by event-key patterns are
blanked out *before* the abstain words are looked for. So the words inside the
event's own name are free, while the same words anywhere else still abstain:
"late drop petition deadline" is a card; "can I file a late drop petition" (can
i), "late drop petition if I'm sick" (if) and "what is the deadline after I
miss..." stay abstains. Blanking only the exact matched span is deliberate: a
broader exemption would let "late" in "I was late to class" through.

**Specific beats generic.** Group phrasings ("drop deadline", "holidays") only
contribute when no specific key matched, and a key's declared ``supersedes``
list removes its sub-phrase keys ("late add post census" must not also be the
census date). Everything else that matches more than one key is context.

**Measured, then fixed (R7, 2026-10-01).** The first version scored card
precision 0.70 with 5 policy questions carded, on the 167 labelled questions in
`bench/router_questions.jsonl` (`bench/router-precision.md`). The fixes below are
from that measurement and from review:

- Text a pattern's `.{0,N}` gap swallowed is the user's own wording, so it is
  still checked for abstain words ("drop a class *after* the refund deadline").
- Consequence and scope words abstain: affect, fee, passed, "what now", ...
- Facilities and immigration ("library hours", "H-1B registration") are never
  calendar lookups: None.
- A final-exam lookup needs the word "final" and not "final day/deadline/date/
  grades"; "exam 2", midterms and quizzes are not finals. A final exam *and* a
  named event is two questions: context.
- No card when the question asks about several rows or not for a date at all
  ("tell me about", plural "deadlines", "start and end", "before", "upcoming").
- "Drop with a W" is the late-drop process, not the without-a-W deadline.
- "Semester end" is the `term_end` group (instruction, finals, make-up day),
  never the last day of instruction alone.

The question text a router gives this classifier must be the *current* turn.
When the chat hook prepends history (a follow-up), R8 must cap the route at
context: the measured miss "...start? and when do they end?" carded the start.
"""
import json
import re
from dataclasses import dataclass

from campus.registration import event_keys as _event_keys


@dataclass(frozen=True)
class RegIntent:
    route: str  # "card" | "context" | "abstain"
    intent: str  # "deadline" | "final_exam" | "term_dates"
    event_keys: tuple = ()
    course_code: str | None = None
    term_hint: str | None = None


_GAP = re.compile(r"\.\{0,\d+\}")


def _name_gaps(pattern: str) -> str:
    """Wrap each `.{0,N}` gap in a named group (gap0, gap1, ...), so the text a
    gap swallowed can be checked for abstain words. Matching is unchanged."""
    counter = iter(range(1000))
    return _GAP.sub(lambda m: f"(?P<gap{next(counter)}>{m.group(0)})", pattern)


def _load():
    # The same file, and the same EVENT_KEYS_PATH override, as the refresh's
    # label mapping, so the classifier and the stored rows can't drift apart.
    raw = json.loads(_event_keys.config_path().read_text(encoding="utf-8"))
    keys = {
        key: (
            spec["intent"],
            [re.compile(_name_gaps(p), re.I) for p in spec["question_patterns"]],
            frozenset(spec.get("supersedes", ())),
        )
        for key, spec in raw["keys"].items()
    }
    groups = {
        name: (spec["keys"], [re.compile(p, re.I) for p in spec["question_patterns"]])
        for name, spec in raw["groups"].items()
    }
    return keys, groups


_KEYS, _GROUPS = _load()

# Policy / conditional wording from SERVICES_PLAN §3, plus words observed to turn
# a date lookup into a how-or-whether question. "required" is deliberately absent:
# it is part of the permission-number event's own name.
_ABSTAIN = re.compile(
    r"\b(if|unless|after|late|missed?|petition|exception|can i|should i|what happens|"
    r"how do|how does|how can|how to|how should|why|whether|process|procedure|steps?|"
    r"appeal|policy|policies|explain|mean|means|meaning|eligib\w*|allowed|rules?|"
    r"requirements?|conflicts?|resched\w*|early|excused)\b",
    re.I,
)
# Consequence, cost and scope words (R7). Checked on the whole text: no event's
# own name contains them.
_CONSEQUENCE = re.compile(
    r"\b(affect(s|ed|ing)?|impact\w*|appl(y|ies) to|counts?|fees?|cost|charged?|"
    r"penalty|penalties|passed|what now|better|worth|where|who)\b",
    re.I,
)
# "drop ... with a W" is the late-drop process. "without a W" does not match:
# 'with' must be followed by a space.
_WITH_A_W = re.compile(r"\bwith an? ['\"]?w['\"]?(?![a-z])", re.I)
# Facilities and immigration are never registration-calendar lookups (R7).
_NOT_REG = re.compile(
    r"\b(library|rec center|srac|gym|pool|dining( hall)?|student union|bookstore|"
    r"health center|office hours|club|hours|h-?1b|f-?1|j-?1|opt|cpt|i-?20|ds-?160|"
    r"visa|uscis|sevis|w-?2)\b",
    re.I,
)
# The question wants more than one row, or no date at all: never a card (R7).
# Checked after the events' own names are blanked.
_NO_CARD = re.compile(
    r"\b(tell me about|info|information|details|overview|deadlines|dates|"
    r"start (and|&|or) end|and end|open (and|&) close|before|until|coming up|"
    r"upcoming|between)\b",
    re.I,
)

# Without a date-asking cue ("when", "deadline", ...) a single-event match is
# demoted to context: "tell me about the census" is not a date lookup.
_DATE_CUE = re.compile(
    r"\b(when|what (day|date|time)|which (day|date)|deadlines?|last day|first day|"
    r"dates?|due|closed|off|starts?|begins?|ends?|opens?|schedule|time)\b",
    re.I,
)

# Registration vocabulary that is not itself an event key, so a policy question
# ("what happens if I miss the drop deadline") abstains rather than returning
# None. Both outcomes reach the KB; abstain is just the honest label for R7.
_REG_ANCHOR = re.compile(
    r"\b(drop(ping|ped)?|add/drop|withdraw\w*|census|waitlist\w*|registration|register(ing)?|"
    r"enroll\w*|permission number|late add|credit/no credit|audit|excess units?|"
    r"final exams?|finals)\b",
    re.I,
)
_DEADLINE_WORDS = re.compile(r"\b(deadlines?|last day|due)\b", re.I)

_OPEN_ENDED = re.compile(
    r"\b((important|key|all|upcoming|major) (dates|deadlines)|"
    r"(academic|registrar'?s?|registration|semester) calendar|"
    r"(dates|deadlines) (for|this|next|in) (the )?(fall|spring|summer|winter|semester|term)|"
    r"(fall|spring|summer|winter) (dates|deadlines|calendar))\b",
    re.I,
)

# Only "final(s)" makes a final-exam lookup, and not "final day/deadline/date/
# grades" ("final day to drop CS 146" is a drop question). "exam 2", midterms and
# quizzes are never finals (R7, review).
_EXAM_WORDS = re.compile(r"\bfinals?\b(?!\s+(days?|deadlines?|dates?|grades?)\b)", re.I)
_NOT_FINAL = re.compile(r"\b(midterms?|exam \d+|quiz(zes)?)\b", re.I)
_FINAL_EXAM_NO_CODE = re.compile(r"\bfinal exam(ination)?s? (schedule|times?)\b", re.I)

# Dept token: 2-4 letters, no hyphen on either side. That alone excludes the
# immigration look-alikes F-1, H-1B, I-20, W-2 (single letter and/or hyphen);
# the stoplist covers the ones that survive: "DS 160" (visa form DS-160 typed
# with a space) and common words followed by a number ("exam 2", "dec 9", "by 5pm").
#
# A subject may end in one digit when a space follows (SJSU has BUS1-BUS5:
# "BUS1 21"), so "cs146" still splits as CS 146 (R7).
_COURSE = re.compile(
    r"(?<![\w-])([a-z]{2,4}(?:\d(?=\s))?)\s?(\d{1,3}[a-z]{0,2})(?![\w-])", re.I
)
# Stoplisted words that are real subjects when typed in capitals: "ME 20" is
# Mechanical Engineering, "me 20" is not.
_UPPER_DEPTS = frozenset({"ME"})
_NOT_DEPT = frozenset(
    "ds in at on is of to or it be an as my no so we me us up by do if am are the and for "
    "exam exams final finals test quiz week day room page part unit units level group form "
    "chapter section grade year term class number problem version question mid hw "
    "jan feb mar apr may jun jul aug sep sept oct nov dec "
    "mon tue tues wed thu thur thurs fri sat sun".split()
)

_TERM_YEAR = re.compile(r"\b(fall|spring|summer|winter)\s+(20\d\d)\b", re.I)
_TERM_SEASON = re.compile(r"\b(fall|spring|summer|winter)\b", re.I)
_TERM_THIS = re.compile(r"\b(this|current) (semester|term)\b", re.I)
_TERM_NEXT = re.compile(r"\bnext (semester|term)\b", re.I)


def _term_hint(text: str) -> str | None:
    if m := _TERM_YEAR.search(text):
        return f"{m.group(1).lower()} {m.group(2)}"
    if m := _TERM_SEASON.search(text):
        return m.group(1).lower()
    if _TERM_THIS.search(text):
        return "this semester"
    if _TERM_NEXT.search(text):
        return "next semester"
    return None


def _course_codes(text: str) -> list[str]:
    codes: list[str] = []
    for m in _COURSE.finditer(text):
        dept = m.group(1)
        if dept.lower() in _NOT_DEPT and dept not in _UPPER_DEPTS:
            continue
        code = f"{dept.upper()} {m.group(2).upper()}"
        if code not in codes:
            codes.append(code)
    return codes


def _normalize(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", text).strip()


def _specific_matches(text: str) -> tuple[list[str], list[tuple[int, int]], list[str]]:
    """Event keys named in `text`, every matched span (for blanking), and the
    text each pattern's `.{0,N}` gaps swallowed (checked, never blanked)."""
    hits: list[tuple[str, int, int]] = []
    all_spans: list[tuple[int, int]] = []
    gap_texts: list[str] = []
    for key, (_intent, patterns, _sup) in _KEYS.items():
        spans = []
        for p in patterns:
            for m in p.finditer(text):
                spans.append(m.span())
                gap_texts.extend(v for k, v in m.groupdict().items() if k.startswith("gap") and v)
        all_spans.extend(spans)
        if spans:
            # The widest span stands for the key when deciding containment.
            hits.append((key, *max(spans, key=lambda s: s[1] - s[0])))
    # Not span containment: "drop without a W and the refund deadline" has the W
    # span inside the refund pattern's wide span, and dropping it would card a
    # question that named two events. Only declared sub-phrases are dropped.
    present = {key for key, _s, _e in hits}
    superseded = set().union(*(_KEYS[k][2] for k in present)) if present else set()
    kept = [key for key, _s, _e in hits if key not in superseded]
    return kept, all_spans, gap_texts


def classify(text: str, *, followup: bool = False) -> RegIntent | None:
    """`followup` is True when the chat hook prepended earlier turns to `text`.
    The question's referent then comes from history, which a card can't check:
    "...start? and when do they end?" names the start date. Capped at context."""
    if not text or not text.strip():
        return None
    text = _normalize(text)
    if _NOT_REG.search(text):
        return None
    hint = _term_hint(text)

    keys, spans, gap_texts = _specific_matches(text)
    codes = _course_codes(text)
    exam = bool(_EXAM_WORDS.search(text)) and not _NOT_FINAL.search(text)

    # What remains once the events' own names are blanked is what the abstain
    # words are checked against (see module docstring). A pattern's gap text is
    # the user's own wording and is checked too.
    remainder = list(text)
    for s, e in spans:
        remainder[s:e] = " " * (e - s)
    remainder = "".join(remainder)
    abstain = (
        bool(_ABSTAIN.search(remainder))
        or any(_ABSTAIN.search(g) for g in gap_texts)
        or bool(_CONSEQUENCE.search(text))
        or bool(_WITH_A_W.search(text))
    )

    final_exam = bool(codes) and exam
    group_keys: list[str] = []
    if not keys and not final_exam:
        for gkeys, patterns in _GROUPS.values():
            if any(p.search(text) for p in patterns):
                group_keys.extend(k for k in gkeys if k not in group_keys)
    open_ended = bool(_OPEN_ENDED.search(text))
    final_exam_ctx = bool(_FINAL_EXAM_NO_CODE.search(text)) and not keys

    if not (keys or group_keys or final_exam or open_ended or final_exam_ctx):
        # Registration vocabulary plus policy wording: label it abstain.
        if abstain and _REG_ANCHOR.search(text) and (_DEADLINE_WORDS.search(text) or exam):
            return RegIntent("abstain", "deadline", (), None, hint)
        return None

    if final_exam:
        intent, ev = "final_exam", ()
    elif keys:
        ev = tuple(keys)
        intent = "deadline" if any(_KEYS[k][0] == "deadline" for k in keys) else "term_dates"
    elif group_keys:
        ev = tuple(group_keys)
        intent = "deadline" if any(_KEYS[k][0] == "deadline" for k in group_keys) else "term_dates"
    elif final_exam_ctx:
        intent, ev = "final_exam", ()
    else:
        intent, ev = "term_dates", ()

    code = codes[0] if final_exam else None
    if abstain:
        return RegIntent("abstain", intent, ev, code, hint)

    # A final exam plus another named event is two questions ("CS 146 final and
    # the census date"); the generic finals period is not another question. Several
    # rows, or no date asked for, is never a card.
    other_keys = [k for k in keys if k != "finals_period"]
    single = (len(keys) == 1 and not final_exam) or (
        final_exam and len(codes) == 1 and not other_keys
    )
    if single and not followup and not _NO_CARD.search(remainder) and _DATE_CUE.search(text):
        return RegIntent("card", intent, ev, code, hint)
    return RegIntent("context", intent, ev, code, hint)
