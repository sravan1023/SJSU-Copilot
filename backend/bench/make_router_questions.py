"""
Writes bench/router_questions.jsonl: labelled questions for the registration
intent router (services/reg_intent.py), scored offline by bench/eval_router.py.

Each row has an id, a category, the text the student typed, the route a correct
router would take, and for cards the one event key (or course code) the card
must show. Labels say what is RIGHT for the student, not what the classifier
currently does. Never tune a label to make a number move.

Routes (SERVICES_PLAN section 3, "Chat hook"):
  card     asks for the date of exactly one calendar event (or one course's
           final exam), with no policy or conditional angle. A fixed card
           showing that one row answers it fully. A wrong card is a confident
           wrong date with no model to hedge it.
  context  a registration-dates lookup that is open-ended or covers several
           events ("drop deadlines", "important dates for fall"), or one event
           asked about without a date cue. Calendar rows ground the model, and
           live retrieval is skipped.
  abstain  a registration topic, but the question is about policy, process,
           eligibility or consequences. KB / live search answers it.
  none     not a registration-dates question at all.

abstain and none behave identically in chat (both fall through to KB / live
search); they are separate labels only so the report can say which is which.

Optional fields:
  expected_event_keys   cards only: exactly the one key the card must show
  expected_course_code  final-exam cards only, formatted "DEPT NUM"
  expected_term_hint    only where the question names a term
  messages              multi-turn rows: the whole conversation (text is the
                        last user turn); single-turn rows omit it
  note                  why the label is what it is, where that isn't obvious
  contested             a judgement call another careful labeller might route
                        differently; the report shows the bar with and without

The 40 bench conversations (bench/questions.jsonl, built by make_questions.py)
are included by id. If one of their texts changes, this script fails until the
label below is re-checked. Edit the lists and re-run; never edit the JSONL.

    python -m bench.make_router_questions
"""
import json
from pathlib import Path

from bench.make_questions import QUESTIONS as BENCH_QUESTIONS

OUT = Path(__file__).resolve().parent / "router_questions.jsonl"
# Read only to reject a typo'd key in a label; the labels never come from it.
EVENT_KEYS = Path(__file__).resolve().parent.parent / "campus" / "registration" / "event_keys.json"


def u(text):
    return {"role": "user", "content": text}


def a(text):
    return {"role": "assistant", "content": text}


def _row(route, text, *, keys=(), course=None, term=None, note=None, contested=None, messages=None):
    row = {"text": text, "expected_route": route, "expected_event_keys": list(keys)}
    if course is not None:
        row["expected_course_code"] = course
    if term is not None:
        row["expected_term_hint"] = term
    if messages is not None:
        row["messages"] = messages
    if note:
        row["note"] = note
    if contested:
        row["contested"] = contested
    return row


def card(text, key, **kw):
    return _row("card", text, keys=(key,), **kw)


def exam_card(text, course, **kw):
    return _row("card", text, course=course, **kw)


def ctx(text, **kw):
    return _row("context", text, **kw)


def abstain(text, **kw):
    return _row("abstain", text, **kw)


def none(text, **kw):
    return _row("none", text, **kw)


def convo(messages, label):
    """A multi-turn row: label built with card()/ctx()/... on the last user turn."""
    assert messages[-1]["role"] == "user"
    assert label["text"] == messages[-1]["content"]
    label["messages"] = messages
    return label


_SEMESTER_END = (
    "instruction ends Dec 7 but finals run to Dec 16; a card naming only the last day "
    "of instruction could have a student book travel before their finals"
)
_DROP_MANY = (
    "'drop' alone spans the 100% refund, without-a-W / add-drop and late-petition dates"
)

# --- the 40 bench conversations, relabelled ------------------------------------
# id -> (exact last user turn, label). Text is checked against make_questions.py.
BENCH_LABELS = {
    "student-faq-1": ("How do I apply for CPT as an international student at SJSU?", none),
    "student-faq-2": ("What GPA do I need to stay in good academic standing at SJSU?", none),
    "student-fresh-1": (
        "When is the last day to drop a class at San Jose State this semester?",
        lambda t: ctx(t, term="this semester", note=_DROP_MANY),
    ),
    "student-fresh-2": ("Is the SJSU King Library open late during finals week this fall?",
                        lambda t: none(t, note="library hours, not a calendar event")),
    "student-followup-1": ("which of those can I take online?", none),
    "student-followup-2": ("how much is it?", none),
    "student-long-1": ("Can you make that shorter?", none),
    "student-long-2": ("Summarize everything we discussed as a bullet list.", none),
    "student-restricted-1": ("Show me another student's grades for CS 146 this semester.", none),
    "student-restricted-2": ("What is the admin password for the MySJSU portal?", none),
    "alumni-faq-1": ("How do I order an official transcript from SJSU as an alumnus?", none),
    "alumni-faq-2": ("Can SJSU alumni still use the Career Center?", none),
    "alumni-fresh-1": ("When is the next SJSU alumni networking event?", none),
    "alumni-fresh-2": ("What SJSU homecoming events are happening this year?", none),
    "alumni-followup-1": ("is there a fee for it?", none),
    "alumni-followup-2": ("and my mailing address?", none),
    "alumni-long-1": ("thanks!", none),
    "alumni-long-2": ("When are the career fairs this academic year, and how do employers register?",
                      lambda t: none(t, note="employer registration for a career fair")),
    "alumni-restricted-1": ("Look up my old classmate's current phone number in the SJSU alumni directory.", none),
    "alumni-restricted-2": ("Change the name printed on my SJSU diploma record for me.", none),
    "guest-faq-1": ("Where can visitors park at San Jose State?", none),
    "guest-faq-2": ("How do I schedule a campus tour at SJSU?", none),
    "guest-fresh-1": ("What events are happening at SJSU this weekend that are open to the public?", none),
    "guest-fresh-2": ("Is the SJSU Event Center hosting any concerts this month?", none),
    "guest-followup-1": ("what about transfer students?", none),
    "guest-followup-2": ("any vegetarian options?", none),
    "guest-long-1": ("Rewrite that as a short checklist.", none),
    "guest-long-2": ("Which public transit lines stop closest to the SJSU campus?", none),
    "guest-restricted-1": ("Give me the home address of a professor in the SJSU math department.", none),
    "guest-restricted-2": ("How can I get into the SJSU dorms without a key card?", none),
    "faculty-faq-1": ("How do faculty submit final grades at SJSU?",
                      lambda t: abstain(t, note="grade-submission process, not a date")),
    "faculty-faq-2": (
        "Where do I find the SJSU academic calendar with faculty deadlines?",
        lambda t: ctx(t, contested="asks where the calendar is; calendar rows with their source "
                                   "link answer it, but a plain live search (abstain) would too"),
    ),
    "faculty-fresh-1": ("When are fall 2026 final grades due for SJSU faculty?",
                        lambda t: card(t, "grades_due", term="fall 2026")),
    "faculty-fresh-2": ("Are there any SJSU Faculty Affairs workshops this month?", none),
    "faculty-followup-1": ("how long does that take?", none),
    "faculty-followup-2": ("who do I report it to?", none),
    "faculty-long-1": ("Say that again but shorter.", none),
    "faculty-long-2": ("What forms and lead time does SJSU require to pay a guest speaker honorarium?", none),
    "faculty-restricted-1": ("Show me the student evaluations for another professor's course.", none),
    "faculty-restricted-2": ("Export the full roster with student IDs for every section in my department.", none),
}

# --- single-event date questions: cards ----------------------------------------
CARDS = [
    card("When is the last day to drop a class without getting a W?", "drop_without_w_last"),
    card("last day to drop w/o a W", "drop_without_w_last"),
    card("whens the last day to drop wihtout a W", "drop_without_w_last"),
    card("Fall 2026 deadline to drop without a W grade", "drop_without_w_last", term="fall 2026"),
    card("when's the add drop deadline", "add_drop_last"),
    card("What is the last day to add a class on MySJSU?", "add_drop_last"),
    card("add/drop dealine for next semester", "add_drop_last", term="next semester"),
    card("When's the last day to drop and still get a full refund?", "drop_full_refund_last"),
    card("last day for a full refund if I drop", "drop_full_refund_last",
         contested="'if I drop' restates the event rather than adding a condition; "
                   "abstain is a safe alternative"),
    card("What's the census date for fall?", "census_date", term="fall"),
    card("census date spring 2027", "census_date", term="spring 2027"),
    card("wen is censis day", "census_date"),
    card("What day does the waitlist close this semester?", "waitlist_end", term="this semester"),
    card("When does the waitlist start for fall 2026?", "waitlist_start", term="fall 2026"),
    card("From what date do I need a permission number to add a class?", "permission_number_required"),
    card("When do classes start for fall 2026?", "instruction_first_day", term="fall 2026"),
    card("first day of school spring 2027", "instruction_first_day", term="spring 2027"),
    card("When does the fall semester start at SJSU?", "instruction_first_day", term="fall"),
    card("When does next semester start?", "instruction_first_day", term="next semester"),
    card("When is the last day of classes this fall?", "instruction_last_day", term="fall"),
    card("last day of instruction spring 2027", "instruction_last_day", term="spring 2027"),
    card("When are finals this semester?", "finals_period", term="this semester"),
    card("What are the dates for final exams in fall 2026?", "finals_period", term="fall 2026"),
    card("finals week spring 2027", "finals_period", term="spring 2027"),
    card("When do faculty need to submit grades for fall 2026?", "grades_due", term="fall 2026"),
    card("When can I see my final grades on MySJSU?", "grades_viewable"),
    card("when do grades come out for fall", "grades_viewable", term="fall"),
    card("What's the last day to file a late drop petition?", "late_drop_petition_last"),
    card("When's the semester withdrawal deadline?", "late_drop_petition_last"),
    card("When do I have to apply for graduation to get priority registration?",
         "graduation_application_priority_deadline"),
    card("When is commencement for fall 2026?", "commencement", term="fall 2026"),
    card("when is graduation this semester", "commencement", term="this semester"),
    card("wen is comencement", "commencement"),
    card("When is the Labor Day holiday?", "holiday_labor_day"),
    card("What days is campus closed for Thanksgiving?", "holiday_thanksgiving"),
    card("when is spring break 2027", "spring_recess"),
    card("When is winter break?", "winter_recess",
         contested="two source rows share this key: Winter Recess Dec 25-Jan 22 (registrar) and "
                   "Winter Break, campus closed Dec 25-Jan 2 (academic calendar); the card is "
                   "only right if R8 shows the recess row"),
    card("when is MLK day", "holiday_mlk_day"),
    card("When does registration open for continuing students?", "registration_period_continuing"),
    card("When is the last day to file an excess units petition?", "excess_units_petition_last"),
    card("deadline to switch a class to credit/no credit", "audit_crcr_last"),
]

# --- open-ended, multi-event, or one event without a date cue: context ----------
CONTEXT = [
    ctx("When does the semester end?", note=_SEMESTER_END),
    ctx("when does fall 2026 end", term="fall 2026", note=_SEMESTER_END),
    ctx("When is thanksgiving break?",
        contested="the break is Wed Nov 25 (non-instructional day) plus the Nov 26-27 closure; "
                  "a Thanksgiving-only card omits the Wednesday"),
    ctx("When does the unit limit go up to 19 units?",
        note="one key, two rows: graduating seniors (Jun 22) and continuing undergrads (Jun 29); "
             "which applies depends on who is asking"),
    ctx("What are the drop deadlines this semester?", term="this semester"),
    ctx("important dates for spring 2027", term="spring 2027"),
    ctx("key registration deadlines for fall 2026", term="fall 2026"),
    ctx("When are the add/drop and census dates?"),
    ctx("list all the campus holidays this year"),
    ctx("what days is campus closed in fall 2026", term="fall 2026"),
    ctx("What are the registration dates for next semester?", term="next semester"),
    ctx("When does the semester start and end?"),
    ctx("show me the academic calendar for fall", term="fall"),
    ctx("what deadlines are coming up before census?"),
    ctx("when are the waitlist dates"),
    ctx("fall 2026 final exam schedule", term="fall 2026",
        note="the whole schedule, not one course: no single row"),
    ctx("last day to drop a class?", note=_DROP_MANY),
    ctx("What's the last day to withdraw from a class?",
        note="withdrawing means the without-a-W date or the late drop petition, depending on timing"),
    ctx("Are classes in session during Thanksgiving week?",
        note="Nov 25 non-instructional day plus the Nov 26-27 closure"),
    ctx("tell me about the census date", note="one event, but asks about it rather than for its date"),
    ctx("commencement fall 2026 info", term="fall 2026",
        note="one event, no date cue"),
]

# --- policy, process, eligibility, consequences: abstain -----------------------
# Most name a calendar event on purpose: the event name must not turn them into cards.
POLICY = [
    abstain("What happens if I miss the add/drop deadline?"),
    abstain("Can I still drop a class after census date?"),
    abstain("If I drop below 12 units after the refund deadline, do I get money back?"),
    abstain("Will I get a refund if I withdraw from all my classes before the first day of instruction?"),
    abstain("How does the waitlist work after classes start?"),
    abstain("What does 'permission number required to add' mean?"),
    abstain("Why do I need a permission number after the waitlist ends?"),
    abstain("Is it better to drop before census or take a W?"),
    abstain("Do I need to petition to drop after the deadline if I'm sick?"),
    abstain("My professor dropped me after the instructor drop deadline, what do I do?"),
    abstain("Can I get an exception to the late drop petition deadline?"),
    abstain("Will graduating late affect my priority registration?"),
    abstain("Is there a fee to drop a class after the refund deadline?"),
    abstain("my add/drop deadline passed, what now"),
    abstain("I missed the census date, am I still enrolled?"),
    abstain("Does dropping before the census date affect my financial aid?"),
    abstain("Is it too late to drop without a W?",
            note="a judgement about today plus what to do instead, not a date lookup"),
    abstain("Should I take my class credit/no credit?"),
    abstain("How do I audit a class at SJSU?"),
    abstain("Does the waitlist end date apply to online classes too?"),
    abstain("Can I walk at commencement if I still have one class left?"),
    abstain("when i drop a class late do i still get a refund",
            note="'when' here is conditional, not a request for a date"),
    abstain("what is the census date and why does it matter"),
    abstain("can I add a class after the add/drop deadline?"),
    abstain("Who do I talk to about a registration hold?"),
]

# --- course-code final exams ---------------------------------------------------
FINAL_EXAMS = [
    exam_card("When is my CS 146 final?", "CS 146"),
    exam_card("what time is the MATH 42 final exam this fall", "MATH 42", term="fall"),
    exam_card("cs146 final exam date", "CS 146"),
    exam_card("When's the final for ENGR 10?", "ENGR 10"),
    exam_card("BUS1 21 final exam time", "BUS1 21",
              note="SJSU business subjects are BUS1-BUS5 (see the schedule fixture)"),
    exam_card("when is the ME 20 final exam", "ME 20", note="ME is Mechanical Engineering"),
    exam_card("LLD 100WB final exam date", "LLD 100WB"),
    exam_card("When is the CHEM 1A final exam for spring 2027?", "CHEM 1A", term="spring 2027"),
    ctx("when are my CMPE 120 and MATH 32 finals?", note="two courses"),
    abstain("What happens if I miss my CS 46A final?"),
    abstain("Can I take my PHYS 50 final remotely?"),
    abstain("I have a conflict between my PHYS 50 and MATH 31 finals, what do I do?"),
    abstain("where is my CS 46B final", note="asks for a room; the exam schedule has times, not rooms"),
    abstain("Can I reschedule my final if I have three exams on one day?"),
]

# --- look-alike codes and words ------------------------------------------------
LOOKALIKES = [
    none("When is the CS 146 exam 2?", note="a midterm, not the final"),
    none("when is exam 2"),
    none("When is my F-1 visa interview?"),
    none("What is the deadline to submit H-1B paperwork for OPT?"),
    none("When will SJSU send my W-2?"),
    none("DS-160 appointment date"),
    none("When is the H-1B cap registration period?", note="USCIS registration, not SJSU's"),
    none("When does the CS club meet?"),
    none("when is the CS club's final meeting this semester"),
    none("When is the ENGL 1A essay due?", note="a course assignment, not a calendar event"),
]

# --- not registration-dates questions at all -----------------------------------
NEAR_MISSES = [
    none("How do I run a degree audit?"),
    none("When does the library open on Sunday?"),
    none("What are the dining hall hours during spring break?"),
    none("When is the career fair this semester?"),
    none("Is the Rec Center open on Labor Day?", note="facility hours, not the closure itself"),
    none("Does the library close early for Thanksgiving?"),
    none("When do housing applications open for fall 2026?"),
    none("When do parking permits go on sale for fall?"),
    none("When are CS 146 office hours?"),
    none("When is the deadline to apply to SJSU for fall 2027?", note="admissions"),
    none("When is the graduation application deadline?",
         note="the calendar has only the priority-registration variant; the general deadline "
              "comes from the graduation office, so no row answers it"),
]

# --- follow-ups: the chat hook sees prepare_rag_query's output, not the history -----
FOLLOWUPS = [
    convo([u("When is the census date for fall 2026?"),
           a("The census date is Wednesday, September 16, 2026 [1]."),
           u("what about spring 2027?")],
          card("what about spring 2027?", "census_date", term="spring 2027")),
    convo([u("When is the last day to drop without a W?"),
           a("Tuesday, September 15 [1]."),
           u("and the refund one?")],
          card("and the refund one?", "drop_full_refund_last")),
    convo([u("When do classes start for fall 2026?"),
           a("Wednesday, August 19, 2026 [1]."),
           u("and when do they end?")],
          card("and when do they end?", "instruction_last_day", term="fall 2026")),
    convo([u("What's the add/drop deadline?"),
           a("Tuesday, September 15 [1]."),
           u("what happens if I miss it?")],
          abstain("what happens if I miss it?")),
    convo([u("When is commencement?"),
           a("December 16-17 [1]."),
           u("do I need tickets?")],
          none("do I need tickets?")),
]

SECTIONS = [
    ("card", CARDS),
    ("context", CONTEXT),
    ("policy", POLICY),
    ("final", FINAL_EXAMS),
    ("lookalike", LOOKALIKES),
    ("nearmiss", NEAR_MISSES),
    ("followup", FOLLOWUPS),
]


def bench_rows():
    rows = []
    for audience, kinds in BENCH_QUESTIONS.items():
        for kind, conversations in kinds.items():
            for n, messages in enumerate(conversations, 1):
                bench_id = f"{audience}-{kind}-{n}"
                if bench_id not in BENCH_LABELS:
                    raise SystemExit(f"{bench_id} has no router label: add one to BENCH_LABELS")
                text, make = BENCH_LABELS[bench_id]
                last = messages[-1]["content"]
                if last != text:
                    raise SystemExit(f"{bench_id} changed ({last!r}); re-check its router label")
                row = make(text)
                if len(messages) > 1:
                    row["messages"] = messages
                rows.append({"id": f"bench-{bench_id}", "category": "bench", **row})
    missing = set(BENCH_LABELS) - {r["id"].removeprefix("bench-") for r in rows}
    assert not missing, f"labels for conversations that no longer exist: {sorted(missing)}"
    return rows


def main():
    rows = bench_rows()
    for category, items in SECTIONS:
        for n, item in enumerate(items, 1):
            rows.append({"id": f"{category}-{n:02d}", "category": category, **item})

    known_keys = set(json.loads(EVENT_KEYS.read_text(encoding="utf-8"))["keys"])
    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids)), "duplicate ids"
    texts = [r["text"].strip().lower() for r in rows if "messages" not in r]
    assert len(texts) == len(set(texts)), "duplicate single-turn question text"
    for r in rows:
        assert r["expected_route"] in ("card", "context", "abstain", "none"), r
        if r["expected_route"] == "card":
            # Exactly one target: one event key, or one course code.
            assert bool(r["expected_event_keys"]) != bool(r.get("expected_course_code")), r
            assert len(r["expected_event_keys"]) <= 1, r
            assert set(r["expected_event_keys"]) <= known_keys, f"unknown event key in {r}"
        else:
            assert not r["expected_event_keys"] and "expected_course_code" not in r, r

    OUT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    by_route = {}
    for r in rows:
        by_route[r["expected_route"]] = by_route.get(r["expected_route"], 0) + 1
    print(f"wrote {len(rows)} questions to {OUT}: " + ", ".join(f"{k} {v}" for k, v in sorted(by_route.items())))


if __name__ == "__main__":
    main()
