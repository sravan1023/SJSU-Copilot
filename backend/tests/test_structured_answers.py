"""
The structured-answer seam (services/structured_answers.py + routers/chat.py).

Driven through the real router and the real stream_chat with only Groq's HTTP
API mocked (respx), like test_chat_e2e.py. Retrieval is replaced by a counting
stub so "retrieval was skipped" is an assertion on a call count, not on output.

Run from backend/ with:
    python -m pytest tests/test_structured_answers.py
"""
import asyncio
import json
import logging
from unittest.mock import patch

import httpx
import respx

import main
from routers import chat as chat_router
from services import llm, structured_answers
from services.structured_answers import Answer

from .conftest import AUTH_HEADERS

RAG_SOURCES = [{"title": "Retrieved", "url": "https://www.sjsu.edu/retrieved"}]
RAG = "Context:\n[1] Retrieved - https://www.sjsu.edu/retrieved\nRETRIEVED-BODY"
CARD_MD = "**Crisis line** call 988."


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def _groq_ok(*deltas):
    lines = [f"data: {json.dumps({'choices': [{'delta': {'content': d}}]})}\n\n" for d in deltas]
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=("".join(lines) + "data: [DONE]\n\n").encode(),
    )


def _chat(routers, content="when is the add deadline", deltas=("Model answer.",)):
    """POST /api/chat; return (frames, groq route, rag call count, timing record)."""
    rag_calls = []

    async def _rag(messages, audience=None, **_kw):
        rag_calls.append(1)
        return RAG, RAG_SOURCES

    capture = _Capture()
    logging.getLogger("timings").addHandler(capture)

    async def go():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post(
                "/api/chat",
                json={"messages": [{"role": "user", "content": content}], "model": "quality"},
                headers=AUTH_HEADERS,
            )
            return [json.loads(l[6:]) for l in res.text.splitlines() if l.startswith("data: ")]

    try:
        with respx.mock(assert_all_called=False) as mock, \
             patch.object(structured_answers, "ROUTERS", routers), \
             patch("routers.chat.build_rag_prompt", _rag), \
             patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}):
            route = mock.post(llm.GROQ_API_URL).mock(return_value=_groq_ok(*deltas))
            frames = asyncio.run(go())
        timing = next(r for r in capture.records if r.getMessage() == "request timings")
        return frames, route, len(rag_calls), timing
    finally:
        logging.getLogger("timings").removeHandler(capture)


def _router(result, calls=None):
    async def route(q):
        if calls is not None:
            calls.append(q)
        return result

    return route


def test_card_is_the_whole_answer_and_makes_no_provider_request():
    card = {"kind": "deadline", "term": "Fall 2026"}
    ans = Answer(mode="card", markdown=CARD_MD, card=card, route="deadline",
                 sources=[{"title": "Cal", "url": "https://www.sjsu.edu/cal"}])
    frames, route, rag_calls, timing = _chat([_router(ans)])

    assert route.call_count == 0  # no Groq request at all
    assert rag_calls == 0
    assert [f["status"] for f in frames if "status" in f] == ["received"]
    assert [f["token"] for f in frames if "token" in f] == [CARD_MD]
    done = frames[-1]
    assert done["done"] is True and done["full_response"] == CARD_MD
    assert done["answer_mode"] == "structured" and done["card"] == card
    assert done["request_id"] == timing.request_id
    assert done["sources"] == ans.sources
    assert timing.outcome == "ok"
    assert timing.counters["answer_route"] == "deadline"
    assert timing.counters["answer_mode"] == "card"
    assert "structured.route" in timing.stages


def test_context_skips_retrieval_and_grounds_the_model_on_the_router_block():
    ans = Answer(mode="context", rag_prompt="Context:\n[1] ROUTER-ROWS", route="dates",
                 sources=[{"title": "Dates", "url": "https://www.sjsu.edu/dates"}])
    frames, route, rag_calls, timing = _chat([_router(ans)])

    assert rag_calls == 0
    assert route.call_count == 1
    system = json.loads(route.calls.last.request.content)["messages"][0]["content"]
    assert "ROUTER-ROWS" in system and "RETRIEVED-BODY" not in system
    done = frames[-1]
    assert done["full_response"] == "Model answer." and done["sources"] == ans.sources
    assert "answer_mode" not in done
    assert timing.counters["answer_mode"] == "context"
    assert "rag.total" not in timing.stages


def test_prefix_streams_first_then_runs_the_normal_pipeline():
    ans = Answer(mode="prefix", markdown=CARD_MD, route="crisis", card={"kind": "crisis"})
    frames, route, rag_calls, timing = _chat([_router(ans)])

    assert rag_calls == 1 and route.call_count == 1
    tokens = [f["token"] for f in frames if "token" in f]
    assert tokens == [CARD_MD, "\n\n", "Model answer."]  # card verbatim, then the model
    assert [f["status"] for f in frames if "status" in f] == ["received", "searching", "generating"]
    # Retrieval's grounding still reaches the model.
    assert "RETRIEVED-BODY" in json.loads(route.calls.last.request.content)["messages"][0]["content"]
    done = frames[-1]
    assert done["full_response"] == CARD_MD + "\n\n" + "Model answer."
    assert done["answer_mode"] == "prefix" and done["card"] == {"kind": "crisis"}
    assert done["sources"] == RAG_SOURCES
    assert timing.counters["answer_mode"] == "prefix"


def test_prefix_survives_a_replace_frame():
    # A repaired answer arrives as a `replace` frame that swaps the client's
    # whole text, so it has to carry the prefix the client already rendered.
    ans = Answer(mode="prefix", markdown=CARD_MD)
    frame = 'data: ' + json.dumps({"replace": "repaired"}) + "\n\n"
    out = chat_router._prepend_to_full_text(frame, CARD_MD + "\n\n", ans)
    assert json.loads(out[6:]) == {"replace": CARD_MD + "\n\nrepaired"}
    token = 'data: {"token": "x"}\n\n'
    assert chat_router._prepend_to_full_text(token, "p", ans) == token


def test_router_exception_falls_through_to_the_normal_path():
    async def boom(q):
        raise RuntimeError("router exploded")

    frames, route, rag_calls, timing = _chat([boom])

    assert rag_calls == 1 and route.call_count == 1
    assert frames[-1]["full_response"] == "Model answer."
    assert not any("error" in f for f in frames)
    assert timing.outcome == "ok"
    assert "answer_mode" not in timing.counters


def test_router_timeout_falls_through_to_the_normal_path():
    async def slow(q):
        await asyncio.sleep(5)

    with patch.object(chat_router, "STRUCTURED_TIMEOUT_SECONDS", 0.05):
        frames, route, rag_calls, timing = _chat([slow])

    assert rag_calls == 1 and route.call_count == 1
    assert frames[-1]["full_response"] == "Model answer."
    assert timing.counters["structured_fallthrough"] == "timeout"
    assert timing.stages["structured.route"] < 1000  # cut at 50ms, not 5s


def test_first_non_none_router_wins_and_later_routers_are_not_called():
    first_calls, second_calls, third_calls = [], [], []
    second = Answer(mode="card", markdown="second", route="second")
    third = Answer(mode="card", markdown="third", route="third")
    routers = [
        _router(None, first_calls),
        _router(second, second_calls),
        _router(third, third_calls),
    ]
    frames, route, _, timing = _chat(routers)

    assert frames[-1]["full_response"] == "second"
    assert (len(first_calls), len(second_calls), len(third_calls)) == (1, 1, 0)
    assert timing.counters["answer_route"] == "second"


def test_a_failing_router_does_not_hide_the_next_one():
    async def boom(q):
        raise RuntimeError("x")

    ans = Answer(mode="card", markdown="from the second", route="second")
    frames, route, _, _ = _chat([boom, _router(ans)])
    assert frames[-1]["full_response"] == "from the second" and route.call_count == 0


def test_empty_routers_leave_the_stream_unchanged():
    frames, route, rag_calls, timing = _chat([])

    assert [f["status"] for f in frames if "status" in f] == ["received", "searching", "generating"]
    assert [f["token"] for f in frames if "token" in f] == ["Model answer."]
    done = frames[-1]
    assert set(done) == {
        "done", "full_response", "validators_run", "validators_passed",
        "repairs_applied", "sources", "request_id",
    }
    assert rag_calls == 1 and route.call_count == 1
    assert "answer_route" not in timing.counters
    assert "structured_fallthrough" not in timing.counters


def test_unknown_mode_is_ignored():
    frames, route, rag_calls, _ = _chat([_router(Answer(mode="bogus", markdown="x"))])
    assert rag_calls == 1 and frames[-1]["full_response"] == "Model answer."


def test_router_input_carries_gated_and_raw_text_and_principal():
    seen = []
    # "thanks" is gated out of retrieval; the router must still see it raw.
    ans = asyncio.run(_answer_with("thanks", seen))
    assert ans is None
    q = seen[0]
    assert q.query is None and q.raw_text == "thanks"
    assert q.audience == "student" and q.principal_kind == "user"

    seen.clear()
    asyncio.run(_answer_with("when is the add deadline", seen))
    assert seen[0].query and "add deadline" in seen[0].query


async def _answer_with(text, seen):
    with patch.object(structured_answers, "ROUTERS", [_router(None, seen)]):
        return await structured_answers.answer(
            [{"role": "assistant", "content": "hi"}, {"role": "user", "content": text}],
            "student",
            "user",
        )
