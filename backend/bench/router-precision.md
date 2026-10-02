# Registration router precision (R7)

## Re-measured after the fixes (2026-10-01, later the same day)

**On this set: PASS, but not yet evidence for turning cards on.** Card precision **34/34
= 1.000** (95% Wilson lower bound 0.898), **0 policy questions carded**, 0 fall-through
questions routed to context. Recall 0.642 (34 of 53 expected cards; the misses are safe:
context or today's search path).

| | before fixes | after fixes |
|---|---|---|
| carded | 47 | 34 |
| card precision | 0.702 | 1.000 |
| policy questions carded | 5 | 0 |
| fall-through questions sent to context | 5 | 0 |
| card recall | 0.623 | 0.642 |

- Same 167 questions (`7dbc284ceb3a…`), input `hook`; classifier `services/reg_intent.py`
  `be32effbe5c9…`, `event_keys.json` `1018ca77a06e…`, on commit `f370179`.
- The fixes, all in `services/reg_intent.py` and `event_keys.json`: gap text checked for
  abstain words (F1); consequence words abstain (F3); facilities and immigration return
  None (F5); a final exam needs "final" and not "final day/deadline" (F6, review);
  BUS1-style and capitalised ME subject codes (F7); no card for multi-row or no-date
  wording (F8); "semester end" is a `term_end` group (F9); "thanksgiving break" is a
  `thanksgiving_break` group; "drop with a W" abstains (review); a final exam plus another
  event is context (review); and `classify(..., followup=True)` caps a follow-up at
  context (the "hook cap"; R8 must pass it when the hook prepended history).
- **Why this is not yet the go-ahead (B7/G9):** the fixes were designed from these misses,
  so this set now over-states precision. With ~34 cards, 0.98 at 95% confidence needs
  about 188 consecutive correct cards; 34/34 has a lower bound of only 0.898. Before
  `REG_CHAT_ENABLED` is turned on, re-measure on 40+ fresh questions written blind.
- Not changed: card recall misses ("next semester start", "grades come out", typos),
  which fall through safely.

The original measurement follows.

---

**Verdict (original): FAIL.** Card precision is **0.702** (33 of 47 cards correct) and **5 policy
questions became cards**. The bar is precision ≥ 0.98 *and* zero policy cards (decision 2),
so R8 and B6 ship context-only. Context-only is not safe as the classifier stands either:
see "What context-only costs".

- Date: 2026-10-01. Offline: no database, no network, no tokens.
- Commit `02c8241`. The classifier files are untracked at that commit, so their hashes
  identify the version measured: `services/reg_intent.py` sha256 `a173e5f6896c…`,
  `campus/registration/event_keys.json` `900ce74117c0…`.
- Questions: `bench/router_questions.jsonl`, 167 rows, sha256 `7dbc284ceb3a…`.
- Input: what the chat hook will see, `prepare_rag_query(messages)`. For single-turn rows
  that is the question itself. Short follow-ups get the previous user turn prepended.
  `--input raw` changes the outcome for only 3 rows, all of them follow-ups.

```
.venv\Scripts\python -m bench.make_router_questions
.venv\Scripts\python -m bench.eval_router --json bench/results/router-eval.json --check
```

## The set

| category | card | context | abstain | none | total |
|---|---|---|---|---|---|
| bench (the 40 `questions.jsonl` last turns, relabelled) | 1 | 2 | 1 | 36 | 40 |
| card (one event, several phrasings, casual and typo'd) | 41 | 0 | 0 | 0 | 41 |
| context (open-ended, multi-event, one event with no date cue) | 0 | 21 | 0 | 0 | 21 |
| policy (traps that name an event) | 0 | 0 | 25 | 0 | 25 |
| final (course-code final exams) | 8 | 1 | 5 | 0 | 14 |
| lookalike (F-1, H-1B, I-20, W-2, DS-160, CS club, exam 2) | 0 | 0 | 0 | 10 | 10 |
| nearmiss (degree audit, library, dining, Rec Center…) | 0 | 0 | 0 | 11 | 11 |
| followup (multi-turn; the hook sees the prepended text) | 3 | 0 | 1 | 1 | 5 |
| **total** | **53** | **24** | **32** | **58** | **167** |

How the labels were set:

- Each label says what is right for the student, from the route rules in the
  generator's docstring.
- All labels were written before the classifier first ran on the set. None changed
  after scoring.
- 34 rows name a term and carry an expected term hint.
- 4 labels are judgement calls, marked `contested`: `bench-faculty-faq-2`, `card-09`,
  `card-37` (winter break) and `context-03` (Thanksgiving break). Every number is also
  given with those four removed.
- The plan said about 120 questions. The extra 47 are card phrasings and traps,
  because the bar depends on them.

## Headline

| | all labels | contested excluded |
|---|---|---|
| cards issued | 47 | 44 |
| cards correct (right event key / course code) | 33 | 31 |
| **card precision** | **0.702** | 0.705 |
| precision, 95% Wilson lower bound | 0.560 | 0.558 |
| precision, strict (term hint must match too) | 0.702 | 0.705 |
| card recall (of 53 / 51 expected cards) | 0.623 | 0.608 |
| **policy questions carded** | **5** | 5 |
| fall-through questions (abstain or none) routed to context, which skips retrieval | 5 / 90 | 5 / 90 |
| the same if cards ship as context-only | 12 / 90 | 12 / 90 |

**Term hints:** 26 of the 34 rows that name a term got the right hint. All 26 rows
the router routed had the right hint; the other 8 got no result at all.

## Confusion matrix (rows: expected, columns: actual)

| expected \ actual | card | context | abstain | none | total |
|---|---|---|---|---|---|
| **card** | 35 | 8 | 0 | 10 | 53 |
| **context** | 5 | 16 | 0 | 3 | 24 |
| **abstain** | 5 | 3 | 20 | 4 | 32 |
| **none** | 2 | 2 | 2 | 52 | 58 |
| total | 47 | 29 | 22 | 69 | 167 |

The card/card cell is 35, but only 33 of those are correct. `final-05` and `followup-03`
are cards for the wrong target.

## Every miss, by severity

### Wrong cards: 14 (each one is a confident wrong answer, with no model to hedge it)

| id | question | expected | got | cause |
|---|---|---|---|---|
| policy-13 | Is there a fee to drop a class after the refund deadline? | abstain | card `drop_full_refund_last` | **span blanking:** `drop.{0,40}refund` swallows "after", so it never reaches the abstain check |
| policy-22 | when i drop a class late do i still get a refund | abstain | card `drop_full_refund_last` | **span blanking** swallows "late". "when I…" is a conditional, but it counts as a date cue |
| policy-14 | my add/drop deadline passed, what now | abstain | card `add_drop_last` | **abstain vocabulary gap:** "passed", "what now" |
| policy-16 | Does dropping before the census date affect my financial aid? | abstain | card `census_date` | **abstain vocabulary gap:** "affect". The date cue is "date" inside the event's own name |
| policy-20 | Does the waitlist end date apply to online classes too? | abstain | card `waitlist_end` | **abstain vocabulary gap:** "apply to". The date cue comes from the event name ("end date") |
| lookalike-01 | When is the CS 146 exam 2? | none | card final_exam CS 146 | **bare "exam"** plus a course code counts as a final exam |
| nearmiss-05 | Is the Rec Center open on Labor Day? | none | card `holiday_labor_day` | **facility-hours question:** "open" counts as a date cue |
| context-01 | When does the semester end? | context | card `instruction_last_day` | **"semester end" maps to the last day of instruction** (Dec 7), but finals run to Dec 16 |
| context-12 | When does the semester start and end? | context | card `instruction_first_day` | **the "…and end" half matches no key**, so the question looks single-event |
| context-14 | what deadlines are coming up before census? | context | card `census_date` | **plural "deadlines"** and "before" ask for several rows |
| context-20 | tell me about the census date | context | card `census_date` | **no date is asked for:** "date" is part of the event's name |
| context-03 *(contested)* | When is thanksgiving break? | context | card `holiday_thanksgiving` | **the break includes the Nov 25 non-instructional day** |
| final-05 | BUS1 21 final exam time | card BUS1 21 | card final_exam **BUS 1** | **course regex** drops the subject's trailing digit (SJSU has BUS1–BUS5) |
| followup-03 | and when do they end? *(the hook saw "When do classes start for fall 2026? and when do they end?")* | card `instruction_last_day` | card `instruction_first_day` | **the prepended history** supplies the key, so the card answers the previous question |

### Context where the question should fall through: 5 (live retrieval is skipped, so the model answers from calendar rows only)

| id | question | expected | got | cause |
|---|---|---|---|---|
| policy-08 | Is it better to drop before census or take a W? | abstain | context `census_date` | **abstain vocabulary gap:** "better" |
| policy-12 | Will graduating late affect my priority registration? | abstain | context `graduation_application_priority_deadline` | **span blanking** swallows "late"; "affect" is not an abstain word |
| final-13 | where is my CS 46B final | abstain | context final_exam CS 46B | **a "where" question**: the exam schedule has times, not rooms |
| lookalike-07 | When is the H-1B cap registration period? | none | context `registration_periods` group | **immigration "registration"** is not SJSU's |
| nearmiss-03 | What are the dining hall hours during spring break? | none | context `spring_recess` | **facility hours** |

### Card expected, got context: 8 (safe: calendar rows still ground the model)

- **card-01, card-02, card-03** ("…without getting a W", "w/o a W", "wihtout a W"): the
  without-a-W pattern needs "without a W" verbatim, so the drop-deadlines group answers.
- **card-29** ("semester withdrawal deadline"): the withdrawal group matches;
  `semester withdrawal petition` needs the word "petition".
- **card-39** ("registration open for continuing students"): the key pattern needs
  "registration period/dates".
- **card-07** ("add/drop dealine…") and **card-24** ("finals week spring 2027"): there is
  no date cue.
- **followup-02** ("and the refund one?"): the prepended history adds a second key.

### Lookup expected, fell through: 13 (safe: the same as today's behaviour)

- **Typos:** card-12 ("wen is censis day") and card-33 ("wen is comencement").
- **Phrasings no pattern covers:**
  - card-18 "the fall semester start" and card-19 "next semester start": the pattern needs
    "the semester".
  - card-23 "dates for final exams".
  - card-25 "faculty need to submit grades".
  - card-27 "grades come out".
  - card-32 "when is graduation".
- **Context lookups with no pattern:** context-02 "when does fall 2026 end", context-04
  "unit limit go up to 19", and context-18 "last day to withdraw from a class".
- **final-06** "ME 20 final exam": `me` is in the non-department stoplist.
- **followup-01** "what about spring 2027?": it starts with "what", so it isn't treated as
  a follow-up and the history isn't prepended.

### Abstain and none swapped: 6 (no behavioural difference; both reach KB or live search)

bench-student-fresh-2, bench-faculty-faq-1, policy-05, policy-25, final-14, nearmiss-06.

## What context-only costs

Context rows replace live retrieval. If every current card becomes context, **12 of the
90 questions that should fall through lose their KB or live-search grounding**: the 5
above, plus the 7 that are wrongly carded today. The model would then answer "Does
dropping before the census date affect my financial aid?" from a single calendar row.
So context-only still needs the abstain and stop-list fixes below, or context rows that
supplement retrieval instead of replacing it.

## Suggested fixes, prototyped offline

The suggested fixes are listed below. "Alone" names the baseline harmful misses that the
fix resolves on its own.

- **F1. Blank only the literal words a pattern matched.** Text inside a `.{0,N}` gap is
  the user's own wording, and should still be checked for abstain words.
  - Implementation: wrap each gap in a named group, and un-blank that group.
  - Alone: policy-12, policy-13, policy-22.
- **F2.** Treat `when (i|we|you)` as conditional, which makes it abstain.
  - Alone: policy-22.
- **F3. Extend the abstain words:** `affect`, `impact`, `apply to`, `count(s)`, `fee(s)`,
  `cost`, `charge`, `penalty`, `passed`, `what now`, `better`, `worth`, `where`, `who`.
  - Alone: policy-08, -12, -13, -14, -16, -20 and final-13.
- **F4.** A leading yes/no auxiliary (`does`, `is`, `will`, …) means no card.
  - Alone: none. It breaks the R6 case "Is campus closed on Labor Day?", so it is
    **not recommended**.
- **F5. Stop-list facilities and immigration, returning None.** The terms are library,
  rec center, SRAC, gym, dining, student union, bookstore, health center, office hours,
  club, hours, H-1B, F-1, J-1, OPT, CPT, I-20, DS-160, visa, USCIS, SEVIS and W-2.
  - Alone: nearmiss-03, nearmiss-05, lookalike-07.
- **F6. A final exam needs the word "final" or "finals".** Bare "exam", "exam 2",
  "midterm" and "quiz" don't count.
  - Alone: lookalike-01.
- **F7. Course codes.**
  - The department may end in a digit when a space follows:
    `([a-z]{2,4}(?:\d(?=\s))?)\s?(\d{1,3}[a-z]{0,2})`. "cs146" still parses as CS 146.
  - Uppercase `ME` is a department.
  - Better still: check the code against the schedule's subject list.
  - Alone: final-05. It also recovers final-06.
- **F8. Words that mean "more than one row" or "not asking for a date" rule out a
  card.** They are checked on the text left after event spans are blanked:
  `tell me about`, `info`, `details`, plural `deadlines` or `dates`, `start and end`,
  `and end`, `before`, `until`, `coming up`, `upcoming`, `between`.
  - Alone: context-12, context-14, context-20.
- **F9. `event_keys.json` change.**
  - Remove `the semester`, `the term` and `school` from `instruction_last_day`'s "end"
    patterns.
  - Add a `term_end` group: `instruction_last_day`, `finals_period`, `make_up_day`.
  - Alone: context-01. It also recovers context-02.
- **Hook cap (R8). When `prepare_rag_query` prepended history, the route is at most
  context.**
  - Alone: followup-03.
- **(not prototyped)** A `thanksgiving (break|week)` group that adds
  `non_instructional_day`.
  - Would fix: context-03 (contested).

Results with the fixes applied:

- **F1, F3, F5, F6, F7, F8, F9 plus the hook cap:**
  - precision **34/35 = 0.971**, recall 34/53 = 0.642;
  - **0 policy questions carded**;
  - **0 fall-through questions routed to context**;
  - all R6 tests still pass.
  - The one remaining wrong card is the contested Thanksgiving row. With the contested
    rows excluded, precision is 33/33 = 1.000.
- **Without the hook cap:** 34/36 = 0.944.
- **Ablation, removing one fix at a time from the full set:**
  - without F3: 1 policy card and 4 fall-through questions routed to context;
  - without F8: precision 0.895;
  - without F7, F6 or F9: about 0.94;
  - without F5: 3 fall-through questions routed to context;
  - without F1 or F2: no change on this set. F3's vocabulary happens to cover the same
    rows. F1 is still the structural fix, because a word list will miss the next
    phrasing.

**Caveats:**

- These fixes were designed from the misses on this set, so the post-fix numbers are
  optimistic. To claim the bar, re-measure on a fresh batch of 40 or more questions,
  written by someone who has not seen the fixed classifier.
- At this sample size, 0.98 means zero wrong cards. With about 35 cards, one error
  gives 0.97. Even 33/33 has a 95% lower bound of only 0.896.
- Showing 0.98 at 95% confidence needs about 188 consecutive correct cards. Grow the set
  before G9 reads this number as more than "no known failure".

## Measured, judged, assumed

- **Measured:**
  - every route, key, course code and term hint above (deterministic, offline);
  - the prototype numbers. They come from a scratch copy of the classifier;
    `reg_intent.py` and `event_keys.json` were not edited.
- **Judged:** the 167 labels (one labeller), and the 4 contested ones in particular.
  `nearmiss-05` (Rec Center on Labor Day) is the most debatable label that isn't marked
  contested. Even counted as correct, the bar still fails.
- **Assumed:**
  - that R8 will classify `prepare_rag_query`'s output, as SERVICES_PLAN §3 says;
  - that abstain and none behave identically;
  - that context replaces live retrieval rather than adding to it.
