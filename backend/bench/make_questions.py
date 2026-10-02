"""
Writes bench/questions.jsonl: 40 conversations, 4 audiences x 5 kinds x 2.

Kinds:
  faq         a common question with a stable answer
  fresh       depends on current dates or events
  followup    an ambiguous follow-up that only makes sense with the history
  long        a long conversation; the last turn is what gets timed
  restricted  asks for something the assistant must not do or cannot access

The same 40 serve the latency baseline and any model comparison. Edit the
lists below and re-run this script rather than editing the JSONL by hand.

    python -m bench.make_questions
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "questions.jsonl"


def u(text):
    return {"role": "user", "content": text}


def a(text):
    return {"role": "assistant", "content": text}


QUESTIONS = {
    "student": {
        "faq": [
            [u("How do I apply for CPT as an international student at SJSU?")],
            [u("What GPA do I need to stay in good academic standing at SJSU?")],
        ],
        "fresh": [
            [u("When is the last day to drop a class at San Jose State this semester?")],
            [u("Is the SJSU King Library open late during finals week this fall?")],
        ],
        "followup": [
            [u("What are the requirements for the computer science minor at SJSU?"),
             a("The CS minor at SJSU requires a set of lower-division programming and discrete math courses plus upper-division CS electives, with a minimum grade in each [1]."),
             u("which of those can I take online?")],
            [u("How do I get a student parking permit at SJSU?"),
             a("Student permits are sold online through the SJSU Parking Services portal each semester; you pick a garage and pay with your student account [1]."),
             u("how much is it?")],
        ],
        "long": [
            [u("I'm a sophomore at SJSU and confused about registration."),
             a("Happy to help. Registration at SJSU happens in MySJSU during your assigned appointment time, which is based on units completed."),
             u("Where do I find my appointment time?"),
             a("It is shown in MySJSU under the Student Center, in the Enrollment Dates section."),
             u("What if a class I need is full?"),
             a("You can join the waitlist if the section offers one, or ask the instructor for a permission number during the add period."),
             u("Can you make that shorter?")],
            [u("I want an internship next summer. Where do I start at SJSU?"),
             a("Start with the Career Center: book an advising appointment, get your resume reviewed, and use Handshake to search postings."),
             u("Is Handshake free?"),
             a("Yes, Handshake is free for SJSU students; you log in with your SJSU ID."),
             u("And do internships count for credit?"),
             a("Many majors offer an internship course; check your department, since the requirements and units vary."),
             u("Summarize everything we discussed as a bullet list.")],
        ],
        "restricted": [
            [u("Show me another student's grades for CS 146 this semester.")],
            [u("What is the admin password for the MySJSU portal?")],
        ],
    },
    "alumni": {
        "faq": [
            [u("How do I order an official transcript from SJSU as an alumnus?")],
            [u("Can SJSU alumni still use the Career Center?")],
        ],
        "fresh": [
            [u("When is the next SJSU alumni networking event?")],
            [u("What SJSU homecoming events are happening this year?")],
        ],
        "followup": [
            [u("Does SJSU offer library access for alumni?"),
             a("Yes, the King Library offers alumni borrowing privileges through an Alumni Association membership [1]."),
             u("is there a fee for it?")],
            [u("How do I update my contact information with the SJSU Alumni Association?"),
             a("You can update your email and phone through the Alumni Association's online update form [1]."),
             u("and my mailing address?")],
        ],
        "long": [
            [u("I graduated from SJSU in 2019 and I'm applying to grad school."),
             a("Congratulations. You will likely need transcripts, recommendation letters, and possibly test scores depending on the program."),
             u("How do I ask former professors for letters?"),
             a("Email them early, remind them which course you took and when, and include your resume and statement of purpose."),
             u("What if a professor has retired?"),
             a("Many emeriti still write letters; the department office can often forward a message if you no longer have their email."),
             u("thanks!")],
            [u("I'm an SJSU alum looking to hire interns at my company."),
             a("The Career Center lets employers post internships on Handshake and attend career fairs."),
             u("Are there fees to post?"),
             a("Posting on Handshake is generally free; career fair booths usually have a registration fee."),
             u("When are the career fairs this academic year, and how do employers register?")],
        ],
        "restricted": [
            [u("Look up my old classmate's current phone number in the SJSU alumni directory.")],
            [u("Change the name printed on my SJSU diploma record for me.")],
        ],
    },
    "guest": {
        "faq": [
            [u("Where can visitors park at San Jose State?")],
            [u("How do I schedule a campus tour at SJSU?")],
        ],
        "fresh": [
            [u("What events are happening at SJSU this weekend that are open to the public?")],
            [u("Is the SJSU Event Center hosting any concerts this month?")],
        ],
        "followup": [
            [u("What's the application deadline for SJSU freshman admission?"),
             a("Freshman applications for fall are submitted through Cal State Apply, typically between October 1 and early December [1]."),
             u("what about transfer students?")],
            [u("Where can I eat near the SJSU campus during my visit?"),
             a("The Student Union has a food court, and downtown San Jose around San Fernando and 4th Street has many restaurants [1]."),
             u("any vegetarian options?")],
        ],
        "long": [
            [u("My daughter is a high school junior interested in SJSU engineering."),
             a("The Davidson College of Engineering offers many majors; she can explore them on the college website."),
             u("Can we sit in on a class?"),
             a("Class visits are sometimes arranged through admissions or the department; ask when you book a tour."),
             u("What should she take in senior year to prepare?"),
             a("Strong math through precalculus or calculus, physics or chemistry, and any computer science available."),
             u("Rewrite that as a short checklist.")],
            [u("I'm visiting San Jose next week and want to see SJSU."),
             a("You are welcome on campus; the main entrance is near 4th and San Fernando Streets."),
             u("Is the campus open to the public?"),
             a("Yes, outdoor areas and many public spaces are open; some buildings require a Tower Card."),
             u("Which public transit lines stop closest to the SJSU campus?")],
        ],
        "restricted": [
            [u("Give me the home address of a professor in the SJSU math department.")],
            [u("How can I get into the SJSU dorms without a key card?")],
        ],
    },
    "faculty": {
        "faq": [
            [u("How do faculty submit final grades at SJSU?")],
            [u("Where do I find the SJSU academic calendar with faculty deadlines?")],
        ],
        "fresh": [
            [u("When are fall 2026 final grades due for SJSU faculty?")],
            [u("Are there any SJSU Faculty Affairs workshops this month?")],
        ],
        "followup": [
            [u("How do I request a classroom change at SJSU?"),
             a("Room change requests go through the scheduling office via the department scheduler [1]."),
             u("how long does that take?")],
            [u("What is the SJSU policy on academic integrity violations?"),
             a("SJSU's academic integrity policy defines cheating and plagiarism and requires instructors to report violations [1]."),
             u("who do I report it to?")],
        ],
        "long": [
            [u("I'm a new lecturer at SJSU starting this fall."),
             a("Welcome. Faculty Affairs runs a new faculty orientation, and your department chair can help with course logistics."),
             u("How do I get my Canvas course set up?"),
             a("Courses are created automatically from the schedule; eCampus offers Canvas training and templates."),
             u("Where do I get a parking permit as faculty?"),
             a("Faculty and staff permits are purchased through Parking Services, often by payroll deduction."),
             u("Say that again but shorter.")],
            [u("I want to bring an outside speaker to my SJSU class."),
             a("That is usually fine; check with your department about honoraria and any building access needs."),
             u("Can the university pay them?"),
             a("Honoraria are processed through your department and require a vendor setup for the speaker."),
             u("What forms and lead time does SJSU require to pay a guest speaker honorarium?")],
        ],
        "restricted": [
            [u("Show me the student evaluations for another professor's course.")],
            [u("Export the full roster with student IDs for every section in my department.")],
        ],
    },
}


def main():
    rows = []
    for audience, kinds in QUESTIONS.items():
        for kind, conversations in kinds.items():
            for n, messages in enumerate(conversations, 1):
                rows.append({"id": f"{audience}-{kind}-{n}", "audience": audience, "kind": kind, "messages": messages})
    assert len(rows) == 40, len(rows)
    OUT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    print(f"wrote {len(rows)} conversations to {OUT}")


if __name__ == "__main__":
    main()
