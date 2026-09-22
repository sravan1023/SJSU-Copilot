"""
End-to-end /api/chat through the real router and the real stream_chat, with
only Groq's HTTP API mocked (respx) and retrieval stubbed. Everything between
the client and the provider -- request fitting, SSE parsing, citation
normalisation, validator wiring, error framing, timings -- runs for real.

Run from backend/ with:
    python -m pytest tests/test_chat_e2e.py
"""
import asyncio
import json
import logging
from unittest.mock import patch

import httpx
import respx

import main
from services import llm

SOURCES = [{"title": "Drop calendar", "url": "https://www.sjsu.edu/ue/drops/calendar.php"}]
RAG = "Context:\n[1] Drop calendar - https://www.sjsu.edu/ue/drops/calendar.php\nLast day to drop: Sep 15."


async def _rag(messages):
    return RAG, SOURCES


def _groq_sse(*deltas):
    lines = [f"data: {json.dumps({'choices': [{'delta': {'content': d}}]})}\n\n" for d in deltas]
    return ("".join(lines) + "data: [DONE]\n\n").encode()


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def _chat(body, groq_response):
    """POST /api/chat in-process; return (frames, provider request, timing line)."""
    capture = _Capture()
    logging.getLogger("timings").addHandler(capture)

    async def go():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/chat", json=body)
            return [json.loads(line[6:]) for line in res.text.splitlines() if line.startswith("data: ")]

    try:
        with respx.mock(assert_all_called=False) as mock, \
             patch("routers.chat.build_rag_prompt", _rag), \
             patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}):
            route = mock.post(llm.GROQ_API_URL).mock(return_value=groq_response)
            frames = asyncio.run(go())
        timing = next(r for r in capture.records if r.getMessage() == "request timings")
        return frames, route, timing
    finally:
        logging.getLogger("timings").removeHandler(capture)


def test_answer_streams_with_normalised_citations_and_timings():
    groq = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=_groq_sse("The last day is Sep 15 ", "【", "1†L3", "-L4】", "."),
    )
    body = {"messages": [{"role": "user", "content": "last day to drop?"}], "model": "quality"}
    frames, route, timing = _chat(body, groq)

    statuses = [f["status"] for f in frames if "status" in f]
    assert statuses == ["received", "searching", "generating"]

    tokens = "".join(f["token"] for f in frames if "token" in f)
    done = frames[-1]
    assert tokens == "The last day is Sep 15 [1]." == done["full_response"]
    assert done["sources"] == SOURCES and done["request_id"] == timing.request_id

    sent = json.loads(route.calls.last.request.content)
    assert route.calls.last.request.headers["authorization"] == "Bearer test-key"
    assert sent["model"] == llm.MODEL_QUALITY and sent["stream"] is True
    assert sent["messages"][0]["role"] == "system" and RAG in sent["messages"][0]["content"]
    assert "max_tokens" not in sent

    assert timing.outcome == "ok"
    assert timing.counters["http_status"] == 200
    assert timing.counters["model_id"] == llm.MODEL_QUALITY
    assert timing.counters["citations"] == 1
    assert timing.counters["output_chars"] == len(done["full_response"])
    marks = timing.marks
    assert marks["generating"] <= marks["llm.request_sent"] <= marks["llm.first_token"] <= marks["llm.last_token"]


def test_rate_limit_reaches_the_client_as_a_retry_time():
    groq = httpx.Response(
        429,
        headers={"retry-after": "12"},
        json={"error": {"message": "Rate limit reached for tokens per minute"}},
    )
    frames, _, timing = _chat({"messages": [{"role": "user", "content": "hi"}]}, groq)

    assert frames[-1] == {"error": {"code": "rate_limited", "retry_after": 12}}
    assert not any("token" in f or f.get("done") for f in frames)
    assert timing.outcome == "upstream_error"
    assert timing.counters["http_status"] == 429
