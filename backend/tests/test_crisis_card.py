"""
The crisis and basic-needs card (services/crisis_card.py), driven through the
real chat route with only Groq's HTTP API mocked (respx).

Run from backend/ with:
    python -m pytest tests/test_crisis_card.py
"""
import asyncio
import json
import logging
import re
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx

import main
from routers import chat as chat_router
from services import crisis_card, llm, structured_answers

from .conftest import AUTH_HEADERS, GUEST_HEADERS

SERVICES = Path(__file__).resolve().parent.parent / "services"
CANARY = "SECRET-CANARY-7f3a9c"

RAG = "Context:\n[1] Retrieved - https://www.sjsu.edu/retrieved\nRETRIEVED-BODY"
RAG_SOURCES = [{"title": "Retrieved", "url": "https://www.sjsu.edu/retrieved"}]


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def text(self) -> str:
        # Message plus every extra field: a canary hidden in `extra=` counts.
        return "\n".join(
            r.getMessage() + " " + json.dumps(r.__dict__, default=str) for r in self.records
        )


def _groq_ok(*deltas):
    lines = [f"data: {json.dumps({'choices': [{'delta': {'content': d}}]})}\n\n" for d in deltas]
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=("".join(lines) + "data: [DONE]\n\n").encode(),
    )


def _chat(content, headers=AUTH_HEADERS, flag="true", groq=None):
    """POST /api/chat. Returns (frames, groq route, timing record, all log text)."""
    async def _rag(messages, audience=None, **_kw):
        return RAG, RAG_SOURCES

    everything, timings = _Capture(), _Capture()
    root, tlog = logging.getLogger(), logging.getLogger("timings")
    old_level = root.level
    root.addHandler(everything)
    root.setLevel(logging.DEBUG)
    tlog.addHandler(timings)

    async def go():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post(
                "/api/chat",
                json={"messages": [{"role": "user", "content": content}], "model": "quality"},
                headers=headers,
            )
            return [json.loads(l[6:]) for l in res.text.splitlines() if l.startswith("data: ")]

    try:
        with respx.mock(assert_all_called=False) as mock, \
             patch("routers.chat.build_rag_prompt", _rag), \
             patch.dict("os.environ", {"GROQ_API_KEY": "test-key", "CRISIS_CARD_ENABLED": flag}):
            route = mock.post(llm.GROQ_API_URL).mock(return_value=groq or _groq_ok("Model answer."))
            frames = asyncio.run(go())
        timing = next(r for r in timings.records if r.getMessage() == "request timings")
        return frames, route, timing, everything.text() + timings.text()
    finally:
        root.removeHandler(everything)
        root.setLevel(old_level)
        tlog.removeHandler(timings)


def _file_text(name: str) -> str:
    raw = (SERVICES / name).read_bytes().decode("utf-8").replace("\r\n", "\n")
    assert raw.startswith("<!--")
    return raw.split("-->\n", 1)[1].rstrip("\n")


CRISIS_FILE = "crisis_card_crisis.md"
NEEDS_FILE = "crisis_card_basic_needs.md"


# -- the trigger -----------------------------------------------------------------

CRISIS_PHRASES = [
    "I want to die",
    "i've been thinking about suicide",
    "I'm feeling suicidal",
    "thinking of killing myself",
    "i want to kill myself",
    "I've been cutting myself",
    "I keep hurting myself",
    "self harm",
    "self-harm resources",
    "I want to end my life",
    "I'm going to end it all",
    "i wish i was dead",
    "everyone would be better off without me",
    "there is no reason to live",
    "I don’t want to be here anymore",  # curly apostrophe, as phones type it
    "i can't go on",
    "i took too many pills",
    "I think I overdosed",
    "i don't feel safe at home",
    "I feel unsafe in my apartment",
    "my boyfriend is abusing me",
    "I was sexually assaulted",
    "I was raped last week",
    "I'm being stalked",
    "my dad is hitting me",
    "he threatened to kill me",
    "thanks, I want to die",
    "WANT TO DIE",
]

BASIC_NEEDS_PHRASES = [
    "I have no food",
    "I can't afford food this month",
    "i can't afford rent",
    "I'm homeless",
    "I got evicted yesterday",
    "I have nowhere to sleep tonight",
    "i'm sleeping in my car",
    "I haven't eaten in two days",
    "where is the food pantry",
    "how do I apply for CalFresh",
    "I'm dealing with food insecurity",
    "my landlord kicked out of my apartment",
]

# Each of these names an exclusion documented in services/crisis_card.py.
NEGATIVE_PHRASES = [
    "how do I kill the process on port 8000",
    "kill -9 the server",
    "I'm dying to know when registration opens",
    "this exam is killing me",
    "I'm dead tired after finals",
    "dying of laughter",
    "when is the add deadline",
    "what is the suicide squad cast",
    "I'm in danger of failing calculus",
    "that beats me, I have no idea",
    "it hit me that I should apply",
    "is there no food allowed in the library",
    "I'm hungry, where can I eat on campus",
    "where is the free food event",
    "substance abuse counseling hours",
    "abuse detection in my CS 160 project",
    "an assault rifle is not allowed on campus",
    "I'm going to die if I fail this class",
    "grape juice",
    "make that shorter",
    "",
]


@pytest.mark.parametrize("text", CRISIS_PHRASES)
def test_crisis_phrases_get_the_crisis_card(text):
    assert crisis_card.classify(text) == "crisis"


@pytest.mark.parametrize("text", BASIC_NEEDS_PHRASES)
def test_basic_needs_phrases_get_the_basic_needs_card(text):
    assert crisis_card.classify(text) == "basic_needs"


@pytest.mark.parametrize("text", NEGATIVE_PHRASES)
def test_non_crisis_phrases_get_no_card(text):
    assert crisis_card.classify(text) is None


def test_crisis_outranks_basic_needs_when_both_match():
    assert crisis_card.classify("I'm homeless and I want to die") == "crisis"


def test_the_trigger_is_not_slow_on_a_maximum_size_message():
    import time

    text = ("i can't afford " + "a " * 3000 + "kill ") * 2
    start = time.perf_counter()
    crisis_card.classify(text)
    assert time.perf_counter() - start < 0.5


# -- the flag --------------------------------------------------------------------

def test_with_the_flag_off_the_classifier_is_never_called_and_there_is_no_card():
    calls = []
    with patch.object(crisis_card, "classify", lambda t: calls.append(t) or "crisis"):
        frames, route, timing, _ = _chat("I want to die", flag="false")
    assert calls == []
    assert "answer_mode" not in frames[-1]
    assert [f["token"] for f in frames if "token" in f] == ["Model answer."]
    assert "crisis_card" not in timing.counters


def test_the_flag_defaults_to_off_when_unset():
    with patch.dict("os.environ", {}, clear=False) as env:
        env.pop("CRISIS_CARD_ENABLED", None)
        assert crisis_card.enabled() is False


def test_the_router_is_registered_first():
    assert structured_answers.ROUTERS[0] is crisis_card.route


# -- the card --------------------------------------------------------------------

def test_the_crisis_card_is_emitted_byte_for_byte_before_the_answer():
    frames, route, timing, _ = _chat("I want to die")
    expected = _file_text(CRISIS_FILE)

    tokens = [f["token"] for f in frames if "token" in f]
    assert tokens[0] == expected  # one frame, byte-equal to the file
    assert tokens == [expected, "\n\n", "Model answer."]
    done = frames[-1]
    assert done["full_response"] == expected + "\n\nModel answer."
    assert done["answer_mode"] == "prefix" and done["card"] == {"kind": "crisis"}
    # The normal pipeline still ran: retrieval grounding reached the model.
    assert route.call_count == 1
    assert "RETRIEVED-BODY" in json.loads(route.calls.last.request.content)["messages"][0]["content"]
    # Retrieval's [1] keeps its number; the card's pages follow it.
    assert done["sources"][0] == RAG_SOURCES[0]
    assert {"title": "SJSU Cares", "url": "https://www.sjsu.edu/sjsucares/"} in done["sources"]


def test_the_basic_needs_card_is_emitted_byte_for_byte():
    frames, _, _, _ = _chat("I'm homeless")
    expected = _file_text(NEEDS_FILE)
    assert [f["token"] for f in frames if "token" in f][0] == expected
    assert frames[-1]["card"] == {"kind": "basic_needs"}


def test_the_card_text_is_the_file_without_its_comment_header():
    for kind, name in (("crisis", CRISIS_FILE), ("basic_needs", NEEDS_FILE)):
        text = crisis_card.template(kind)
        assert text == _file_text(name)
        assert "<!--" not in text and "-->" not in text and "\r" not in text
        assert not text.endswith("\n")


def test_the_cards_carry_the_numbers_copied_from_sjsus_pages():
    crisis = crisis_card.template("crisis")
    for needle in ("911", "9-8-8", "408-924-5678", "877-565-8860", "408.924.1234"):
        assert needle in crisis, needle
    assert "408.924.1234" in crisis_card.template("basic_needs")


def test_every_template_file_records_its_source_and_fetch_date():
    for name in (CRISIS_FILE, NEEDS_FILE):
        header = (SERVICES / name).read_bytes().decode("utf-8").split("-->", 1)[0]
        assert re.search(r"https://www\.sjsu\.edu/\S+", header)
        assert re.search(r"fetched \d{4}-\d{2}-\d{2}", header)


def test_a_guest_gets_the_card():
    frames, _, timing, _ = _chat("I want to die", headers=GUEST_HEADERS)
    assert frames[-1]["done"] is True and frames[-1]["answer_mode"] == "prefix"
    assert [f["token"] for f in frames if "token" in f][0] == _file_text(CRISIS_FILE)
    assert timing.counters["principal"] == "guest"


def test_a_gated_conversational_turn_with_crisis_wording_still_gets_the_card():
    # The meta-request gate hides this turn from retrieval; the router reads
    # the raw text. (A bare "thanks" is gated too, but carries no crisis wording.)
    text = "make that shorter, I want to die"
    assert structured_answers.build_input(
        [{"role": "user", "content": text}], "student", "user"
    ).query is None
    frames, _, _, _ = _chat(text)
    assert [f["token"] for f in frames if "token" in f][0] == _file_text(CRISIS_FILE)


def test_a_benign_turn_with_the_flag_on_gets_no_card():
    frames, _, timing, _ = _chat("when is the add deadline")
    assert [f["token"] for f in frames if "token" in f] == ["Model answer."]
    assert "answer_mode" not in frames[-1] and "crisis_card" not in timing.counters


# -- privacy ---------------------------------------------------------------------

def test_observability_records_a_count_and_a_template_label_only():
    _, _, timing, _ = _chat(f"I want to die {CANARY}")
    assert timing.counters["crisis_card"] == 1
    assert timing.counters["crisis_card_template"] == "crisis"
    assert timing.counters["answer_route"] == "crisis_card"
    assert CANARY not in json.dumps(timing.counters, default=str)


def test_the_message_is_absent_from_every_log_line():
    frames, route, _, logs = _chat(f"I want to die {CANARY}")
    assert frames[-1]["done"] is True  # the card did fire
    assert CANARY not in logs
    # Control: the canary does reach the model, so the assertion above could fail.
    assert CANARY in route.calls.last.request.content.decode()


def test_the_message_is_absent_from_logs_on_the_failure_path_too():
    resp = httpx.Response(429, headers={"retry-after": "3"}, text="slow down")
    _, _, _, logs = _chat(f"I want to die {CANARY}", groq=resp)
    assert CANARY not in logs


# -- resilience (the seam hardening) ---------------------------------------------

def test_the_card_survives_a_429_from_the_provider():
    resp = httpx.Response(429, headers={"retry-after": "3"}, text="slow down")
    frames, route, timing, _ = _chat("I want to die", groq=resp)

    assert route.call_count == 1
    assert not any("error" in f for f in frames)
    done = frames[-1]
    assert done["done"] is True
    assert done["full_response"] == (
        _file_text(CRISIS_FILE) + "\n\n" + chat_router.PREFIX_FAILURE_LINE
    )
    assert done["card"] == {"kind": "crisis"}
    assert timing.outcome == "upstream_error"


def test_the_card_survives_an_exception_mid_stream():
    async def broken(**_kw):
        yield 'data: {"token": "Partial"}\n\n'
        raise RuntimeError("boom")

    with patch("routers.chat.stream_chat", broken):
        frames, _, _, _ = _chat("I want to die")
    assert not any("error" in f for f in frames)
    assert frames[-1]["done"] is True
    assert frames[-1]["full_response"].startswith(_file_text(CRISIS_FILE) + "\n\nPartial")
