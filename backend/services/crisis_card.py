"""Crisis and basic-needs card, a `prefix` router for the structured-answer seam.

When a student's message suggests they may be in danger, or without food or a
place to sleep, SJSU's own contact details are shown before the normal answer.
The card is a fixed file (crisis_card_crisis.md / crisis_card_basic_needs.md)
emitted byte for byte: the model never writes or paraphrases a phone number.

Design choices, all deliberate:

- It is `prefix`, not `card`. The trigger over-matches on purpose, so a false
  positive costs a paragraph above a correct answer, and never blocks one.
- It reads `RouterInput.raw_text`, not the gated query. "thanks, I want to die"
  is a conversational turn that retrieval skips, and the card must not be.
- It is for guests as well: nothing in it is gated on who is asking.
- It records a count and a template label, and never any of the message.
- Off by default (`CRISIS_CARD_ENABLED`). X3 turns it on after the wording has
  been reviewed against the live pages.

Trigger phrases and the false-positive exclusions are listed beside each
pattern. When unsure, a phrase is included.
"""

import os
import re
from functools import lru_cache
from pathlib import Path

import observability

_DIR = Path(__file__).parent

# Source pages, shown as the card's sources.
SOURCES = {
    "crisis": [
        {
            "title": "SJSU Student Wellness Center: Emergency",
            "url": "https://www.sjsu.edu/wellness/access-services/emergency-crisis.php",
        },
        {"title": "SJSU Cares", "url": "https://www.sjsu.edu/sjsucares/"},
    ],
    "basic_needs": [
        {"title": "SJSU Cares", "url": "https://www.sjsu.edu/sjsucares/"},
        {
            "title": "Spartan Food Pantry",
            "url": "https://www.sjsu.edu/sjsucares/get-assistance/food-assistance/spartan-food-pantry.php",
        },
    ],
}

_TEMPLATE_FILES = {
    "crisis": "crisis_card_crisis.md",
    "basic_needs": "crisis_card_basic_needs.md",
}
_HEADER = re.compile(r"\A<!--.*?-->[ \t]*\n", re.DOTALL)


def enabled() -> bool:
    # Per call, so a restart with a new value is all it takes to switch it.
    return os.getenv("CRISIS_CARD_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")


@lru_cache(maxsize=None)
def template(kind: str) -> str:
    """The card text: the file minus its comment header and trailing newline."""
    # CRLF to LF: a Windows checkout with core.autocrlf rewrites the file's line
    # endings, and a stray carriage return must not reach the student's screen.
    raw = (_DIR / _TEMPLATE_FILES[kind]).read_bytes().decode("utf-8").replace("\r\n", "\n")
    return _HEADER.sub("", raw, count=1).rstrip("\r\n")


def _rx(*alternatives: str) -> re.Pattern:
    return re.compile("|".join(alternatives), re.IGNORECASE)


# Each alternative is word-bounded. Windows are bounded (`{0,n}`) and nothing
# is nested-quantified, so a long message cannot make matching slow.
_CRISIS = _rx(
    # Suicide, with the misspellings people type when distressed ("sucidal",
    # "suicde", "suicidel"). Excluded: "suicide squad" (the film), "suicide mission".
    r"\b(?:suicid|sucid|suicd|suisid|suiced?)(?:e|al|ally|el)\b(?!\s+(?:squad|mission|prevention\s+month))",
    r"\bunaliv(?:e|ed|ing)\b",
    # Hurting oneself. "kill me" is NOT here, so "this exam is killing me" and
    # "kill the process" stay clear; the reflexive "myself" is the signal.
    # "my self" with a space is how it gets typed on a phone.
    r"\b(?:kill|killing|hurt|hurting|harm|harming|cut|cutting|starve|starving|punish|punishing"
    r"|hang|hanging|hung|hanged|shoot|shooting|shot|drown|drowning|drowned|unalive|unaliving"
    r"|off|offing|stab|stabbing|burn|burning|poison|poisoning)\s+(?:myself|my\s+self)\b",
    # Not after a digit: "3 kms from campus" is a distance.
    r"(?<!\d)(?<!\d\s)\bkms\b",
    r"\bself[- ]?(?:harm|injur\w*|mutilat\w*)",
    # Wanting to die. "dying to know" cannot match: `die` is word-bounded and
    # "dying" is a different word. "going to die" is left out on purpose: it is
    # almost always hyperbole ("I'm going to die if I fail").
    r"\b(?:want|wanted|wants|wanna|ready|planning|trying|thinking\s+(?:of|about))\s+(?:to\s+)?(?:die|be\s+dead|end\s+my\s+life|end(?:ing)?\s+it(?:\s+all)?)\b",
    # "going to end it tonight". "going to die" stays out (hyperbole), but "end it"
    # after an intent phrase is not an idiom, so it is allowed here.
    r"\b(?:going\s+to|gonna|about\s+to|have\s+to|need\s+to)\s+end(?:ing)?\s+it(?:\s+all)?\b",
    r"\b(?:end|ending|take|taking)\s+(?:my|my\s+own)\s+(?:own\s+)?life\b",
    r"\bend(?:ing)?\s+it\s+all\b",
    r"\bwish\s+(?:i|that\s+i)\s+(?:was|were|could|would)\s+(?:dead|die|never\s+born|never\s+wake\s+up|not\s+here|not\s+alive)\b",
    r"\bwish\s+(?:i|that\s+i)\s+(?:wasn'?t|weren'?t|was\s+not|were\s+not)\s+(?:alive|here|born|around)\b",
    r"\bwish\s+(?:i|that\s+i)(?:'d|\s+had|\s+would)?\s+never\s+(?:been\s+born|existed)\b",
    r"\bbetter\s+off\s+(?:dead|without\s+me)\b",
    r"\b(?:no|not\s+any)\s+(?:reason|point)\s+(?:to|in)\s+(?:live|living|go\s+on|going\s+on|being\s+alive)\b",
    r"\bpoint\s+(?:of|in|to)\s+(?:living|going\s+on|being\s+alive)\b",
    r"\b(?:not|isn'?t|ain'?t|aint)\s+worth\s+living\b",
    r"\bdo(?:n'?t|\s+not)\s+(?:want\s+to|wanna)\s+(?:be\s+here|live|be\s+alive|exist|wake\s+up)\b",
    r"\b(?:no\s*one|nobody)\s+would\s+(?:miss|care\s+if)\b",
    # "can't take it anymore" is ambiguous (exams) but also how people say it
    # when it is serious; the card is cheap, so it is included.
    r"\bcan'?t\s+go\s+on\b",
    r"\bcan'?t\s+(?:take|do)\s+(?:this|it)\s+anymore\b",
    # Overdose. "od" alone is too ambiguous; only its verb forms.
    r"\boverdos(?:e|ed|es|ing)\b",
    r"\bod'?(?:ed|ing)\b",
    r"\btook\s+too\s+many\s+(?:pills|tablets|meds|sleeping)",
    # Unsafe. Excluded: "in danger of" (failing, probation).
    r"\bdo(?:n'?t|\s+not)\s+feel\s+safe\b",
    r"\bnot\s+safe\s+(?:at|in|with)\s+(?:home|my|the)\b",
    r"\b(?:i'?m|i\s+am|i\s+feel|feel|feeling)\s+(?:very\s+|really\s+)?unsafe\b",
    r"\bin\s+(?:immediate\s+|serious\s+|real\s+)?danger\b(?!\s+of\b)",
    r"\b(?:afraid|scared|fear|fearing)\s+for\s+my\s+(?:life|safety)\b",
    r"\bthreaten\w*\s+(?:to\s+)?(?:kill|hurt)\b",
    # Abuse and assault. Bare "abuse" and its forms fire ("my partner abuses me",
    # "I'm a victim of abuse"), except right after substance/drug/alcohol and in
    # "abuse detection" (a CS topic).
    r"(?<!substance\s)(?<!substance-)(?<!drug\s)(?<!drug-)(?<!alcohol\s)(?<!alcohol-)"
    r"\babus(?:e[sd]?|ive|ing|er)\b(?!\s+detection)",
    r"\bdomestic\s+violence\b|\bintimate\s+partner\s+violence\b|\bsexual\s+violence\b",
    # "assault rifle/weapon" is excluded.
    r"\bassault(?:ed|s)?\b(?!\s+(?:rifle|weapon)s?\b)",
    r"\brap(?:e|ed|ist|ing)\b",
    r"\bmolest\w*",
    r"\bsexual(?:ly)?\s+harass\w*",
    r"\bstalk(?:ed|ing|er)\b",
    r"\btrafficked\b",
    # Being hit. "it hit me" and "beats me" are idioms, so only the verb forms
    # that need a person doing it.
    r"\b(?:hitting|beating|choking|strangling)\s+me\b",
    r"\b(?:he|she|they)\s+(?:hits|hit|beats|beat|choked|strangled)\s+me\b",
    # "my dad beats me": any one word before the verb, except the idioms
    # "that beats me" / "it hits me" / "this kicks me".
    r"\b(?!(?:that|it|this|what|which|who)\s)\w+\s+(?:hits|beats|chokes|kicks)\s+me\b",
)

# Basic needs. Excluded: bare "no food" ("no food allowed in the library"),
# bare "hungry" and "free food" (campus-event questions), "snap" (not the
# benefit). First-person phrasing is what separates need from curiosity.
_BASIC_NEEDS = _rx(
    r"\b(?:i|we)\s+(?:have|has|got|had)\s+(?:no|not\s+enough)\s+food\b",
    r"\b(?:i|we)\s+(?:don'?t|do\s+not)\s+have\s+(?:any|enough)\s+food\b",
    r"\b(?:i|we)\s+(?:have|has|got|had)\s+(?:no|not\s+enough)\s+money\s+(?:for|to\s+buy)\s+(?:food|groceries|meals?|rent)\b",
    r"\b(?:living|live|lives)\s+in\s+(?:my|a|our)\s+car\b",
    r"\bnothing\s+to\s+eat\b",
    r"\bhaven'?t\s+eaten\b",
    r"\b(?:can'?t|cannot|can\s+not|unable\s+to|couldn'?t)\s+afford\s+(?:food|groceries|meals?|to\s+eat|rent|housing|to\s+pay\s+rent)\b",
    r"\b(?:can'?t|cannot)\s+(?:pay|make)\s+(?:my\s+)?rent\b",
    r"\b(?:go|going|went)\s+hungry\b",
    r"\bskipp?(?:ing|ed)\s+meals\b",
    r"\bfood\s+insecur\w*",
    r"\bfood\s+pantry\b",
    r"\bcalfresh\b|\bfood\s+stamps\b",
    r"\bhomeless(?:ness)?\b|\bunhoused\b",
    r"\bevict(?:ed|ion|ing)\b",
    r"\bnowhere\s+to\s+(?:sleep|stay|live|go)\b",
    r"\bno\s+(?:place|where)\s+to\s+(?:live|stay|sleep)\b",
    r"\bsleeping\s+(?:in\s+my\s+car|on\s+the\s+streets?|outside|on\s+couches)\b",
    r"\bcouch[- ]?surf\w*",
    r"\bkicked\s+out\s+of\s+(?:my\s+)?(?:house|home|apartment|place|dorm)\b",
    r"\bhousing\s+insecur\w*",
    r"\b(?:lose|losing|lost)\s+(?:my\s+)?housing\b",
)


def _normalize(text: str) -> str:
    # Curly apostrophes are what phones type; the patterns use the straight one.
    return " ".join(text.replace("\u2019", "'").replace("\u2018", "'").split())


def classify(text: str) -> str | None:
    """'crisis', 'basic_needs' or None. Crisis wins when both match."""
    t = _normalize(text or "")
    if not t:
        return None
    if _CRISIS.search(t):
        return "crisis"
    if _BASIC_NEEDS.search(t):
        return "basic_needs"
    return None


async def route(q):
    """Router for structured_answers.ROUTERS. Never sees or logs the text beyond classify."""
    if not enabled():
        return None
    kind = classify(q.raw_text)
    if kind is None:
        return None
    # Count and label only: this is the one place that knows a student may be in
    # crisis, and it must not leave a trace of what they wrote.
    observability.incr("crisis_card")
    observability.record("crisis_card_template", kind)
    # Imported here: structured_answers registers this router at import time.
    from services.structured_answers import Answer

    return Answer(
        mode="prefix",
        markdown=template(kind),
        card={"kind": kind},
        sources=[dict(s) for s in SOURCES[kind]],
        route="crisis_card",
    )
