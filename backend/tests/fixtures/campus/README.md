# Campus page fixtures

Captured 2026-10-01, one request per page from www.sjsu.edu, with the KB bot
user agent (`kb.robots.USER_AGENT`) after a robots.txt check. Pages are served
as of that date; none sent an ETag or Last-Modified header.

| File | Source URL | Notes |
|---|---|---|
| `schedule-fall-2026.trimmed.html` | https://www.sjsu.edu/classes/schedules/fall-2026.php | Real `<head>`, header and footer. The full page is 3.76 MB and 6,980 rows; this keeps 300 rows chosen to cover every Satisfies spelling, every mode and type, TBA times, multi-meeting (`<br>`) cells, a 0-seat section, Notes text, and the literal duplicate `PHYS 50 (Section 25)` row. |
| `registrar-calendar-fall-2026.html` | https://www.sjsu.edu/registrar/calendar/fall-2026.php | whole |
| `academic-calendar-2026-2027.html` | https://www.sjsu.edu/classes/calendar/2026-2027.php | whole |
| `final-exam-schedule-fall-2026.html` | https://www.sjsu.edu/classes/final-exam-schedule/fall-2026.php | whole |
| `bursar-payment-due-dates-fall.html` | https://www.sjsu.edu/bursar/fees-due-dates/payment-due-dates/fall.php | whole |

Privacy: instructor names in the schedule are placeholders. Each of the 224 distinct
real names was replaced by a stable `Instructor 001` ... `Instructor 224` (the same
name always maps to the same placeholder; `Staff` is kept as is). Row structure,
` / ` separators and repeats are unchanged. Every instructor `mailto:` href in the schedule is replaced with
`mailto:redacted@example.edu`. The anchor structure is kept so a parser test can
assert that no mailto is stored. Department contact mailtos in the page chrome of
the small pages are untouched.

Do not re-fetch to refresh these casually: www.sjsu.edu asks not to be overloaded.
