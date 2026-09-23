"""
Tests for per-request stage timings (observability.py) and where they are
wired in: the chat route, retrieval, and the client timing sink.

Run from backend/ with:
    python -m pytest tests/test_observability.py

No network: search, crawl and the provider are stubbed.
"""
import asyncio
import contextvars
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from .conftest import AUTH_HEADERS

import main
import observability
import runtime
from logging_config import JsonFormatter
from services import web_search
from tests.test_chat_stream import _fake_stream, _live_server


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


@contextmanager
def _captured(logger_name=""):
    """Collect log records from one logger (default: everything, via root)."""
    handler = _Capture()
    logger = logging.getLogger(logger_name)
    previous_level = logger.level
    logger.addHandler(handler)
    if logger_name:
        logger.setLevel(logging.INFO)
    try:
        yield handler.records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


def _timing_lines(records):
    return [r for r in records if r.name == "timings" and r.getMessage() == "request timings"]


def _post(client, content="hi", **kwargs):
    # Merge, not set: two callers below pass their own headers.
    kwargs["headers"] = {**AUTH_HEADERS, **kwargs.get("headers", {})}
    res = client.post("/api/chat", json={"messages": [{"role": "user", "content": content}]}, **kwargs)
    frames = [json.loads(line[6:]) for line in res.text.splitlines() if line.startswith("data: ")]
    return res, frames


# ── 1. The module ─────────────────────────────────────────────────────────────


def test_without_a_trace_everything_is_a_noop():
    ctx = contextvars.Context()

    def run():
        with observability.stage("x"):
            pass
        observability.mark("m")
        observability.record("k", 1)
        observability.incr("n")
        return observability.flush(), observability.current_request_id()

    assert ctx.run(run) == (None, None)


def test_flush_reports_stages_marks_and_counters():
    def run():
        observability.begin("req-1")
        with observability.stage("work"):
            time.sleep(0.01)
        with observability.stage("work"):  # repeats are summed
            time.sleep(0.01)
        observability.mark("halfway")
        observability.mark("halfway")  # first occurrence wins
        observability.record("sources", 3)
        observability.incr("hits")
        observability.incr("hits", 2)
        with _captured("timings") as records:
            payload = observability.flush(outcome="ok")
        return payload, records

    payload, records = contextvars.Context().run(run)
    assert payload["request_id"] == "req-1"
    assert payload["stages"]["work"] >= 20
    assert payload["stages"]["auth_verify"] == 0.0
    assert list(payload["marks"]) == ["halfway"]
    assert payload["counters"] == {"sources": 3, "hits": 3}
    assert payload["outcome"] == "ok"
    assert payload["total_ms"] >= payload["stages"]["work"]
    assert len(records) == 1 and records[0].request_id == "req-1"


def test_flush_stops_tracking():
    def run():
        observability.begin("req-2")
        observability.flush()
        return observability.flush(), observability.current_request_id()

    assert contextvars.Context().run(run) == (None, None)


def test_trace_reaches_tasks_and_pool_threads():
    async def main_():
        observability.begin("req-3")

        async def child():
            with observability.stage("in_task"):
                await asyncio.sleep(0)
            return observability.current_request_id()

        in_task = await asyncio.ensure_future(child())
        with patch.object(runtime, "_executor", ThreadPoolExecutor(max_workers=1)):
            in_thread = await runtime.run_blocking(observability.current_request_id)
        return in_task, in_thread, observability.flush()

    in_task, in_thread, payload = asyncio.run(main_())
    assert in_task == "req-3"
    assert in_thread == "req-3", "run_blocking must carry context into the pool"
    assert "in_task" in payload["stages"]


def test_flush_with_an_explicit_trace_works_outside_the_context():
    trace = contextvars.Context().run(observability.begin, "req-4")
    # e.g. an abandoned generator finalised somewhere else
    payload = contextvars.Context().run(observability.flush, trace, outcome="cancelled")
    assert payload["request_id"] == "req-4" and payload["outcome"] == "cancelled"


# ── 2. The chat route ─────────────────────────────────────────────────────────


async def _no_rag(messages, audience=None):
    return None, []


def test_one_timing_line_per_request_matching_the_header():
    client = TestClient(main.app)
    with _captured("timings") as records, \
         patch("routers.chat.build_rag_prompt", _no_rag), \
         patch("routers.chat.stream_chat", _fake_stream):
        res, frames = _post(client, headers={"x-request-id": "bench-abc_123"})

    lines = _timing_lines(records)
    assert len(lines) == 1
    line = lines[0]
    assert line.request_id == res.headers["x-request-id"] == "bench-abc_123"
    assert line.outcome == "ok"
    assert line.model == "8b"
    assert {"auth_verify", "behavior_compute", "rag.total"} <= set(line.stages)
    assert "generating" in line.marks
    assert line.counters["history_messages"] == 1


def test_unsafe_request_ids_are_replaced():
    client = TestClient(main.app)
    for supplied in ("has spaces", "x" * 65, "quote\"s", "../path;inject"):
        with patch("routers.chat.build_rag_prompt", _no_rag), \
             patch("routers.chat.stream_chat", _fake_stream):
            res, _ = _post(client, headers={"x-request-id": supplied})
        echoed = res.headers["x-request-id"]
        assert echoed != supplied
        assert len(echoed) == 32 and all(c in "0123456789abcdef" for c in echoed)


def test_error_and_upstream_error_outcomes():
    client = TestClient(main.app)

    async def _boom(messages):
        raise RuntimeError("search exploded")

    async def _upstream_error(**kwargs):
        yield f"data: {json.dumps({'error': 'Upstream model error (503)'})}\n\n"

    with _captured("timings") as records, patch("routers.chat.build_rag_prompt", _boom):
        _post(client)
    assert [r.outcome for r in _timing_lines(records)] == ["error"]

    with _captured("timings") as records, \
         patch("routers.chat.build_rag_prompt", _no_rag), \
         patch("routers.chat.stream_chat", _upstream_error):
        _post(client)
    assert [r.outcome for r in _timing_lines(records)] == ["upstream_error"]


def test_timing_line_is_written_when_the_client_disconnects():
    """Timing must survive a client that goes away mid-stream."""
    async def _slow_rag(messages, audience=None):
        await asyncio.sleep(3)
        return None, []

    with _captured("timings") as records, \
         patch("routers.chat.build_rag_prompt", _slow_rag), \
         patch("routers.chat.stream_chat", _fake_stream), \
         _live_server(main.app) as base:
        with httpx.Client(timeout=10) as http:
            with http.stream("POST", f"{base}/api/chat",
                             json={"messages": [{"role": "user", "content": "hi"}]},
                             headers={**AUTH_HEADERS, "x-request-id": "gone-early"}) as res:
                for line in res.iter_lines():
                    if '"searching"' in line:
                        break  # hang up while retrieval is still running
        deadline = time.monotonic() + 8
        while not _timing_lines(records) and time.monotonic() < deadline:
            time.sleep(0.05)

    lines = _timing_lines(records)
    assert len(lines) == 1
    assert lines[0].request_id == "gone-early"
    assert lines[0].outcome in ("cancelled", "disconnected")


# ── 3. Retrieval stages, counters, and no user text in logs ───────────────────

SECRET = "ZXQV7781"


def test_retrieval_is_timed_and_logs_carry_no_user_text():
    client = TestClient(main.app)

    async def _rewrite(question, audience=None):
        return f"SJSU {SECRET} curricular practical training office"

    def _search(query):
        return [
            {"url": "https://www.sjsu.edu/isss/cpt", "title": "CPT", "snippet": "Apply for CPT."},
            {"url": "https://www.sjsu.edu/isss/opt", "title": "OPT", "snippet": "Apply for OPT."},
        ]

    async def _fetch(client_, url):
        return url, "<html><main>" + "CPT requires an internship course. " * 20 + "</main></html>"

    formatter = JsonFormatter()
    with _captured() as records, \
         patch.object(web_search, "rewrite_query_for_sjsu", _rewrite), \
         patch.object(web_search, "search_web", _search), \
         patch.object(web_search, "_safe_stream_get", _fetch), \
         patch("routers.chat.stream_chat", _fake_stream):
        _, frames = _post(client, content=f"How do I apply for CPT {SECRET} as an F-1 student?")

    assert frames[-1].get("done")
    line = _timing_lines(records)[0]
    for name in ("rag.prepare", "rag.rewrite", "rag.search.sjsu", "rag.search.general",
                 "rag.search.gather", "rag.crawl.total", "rag.extract", "rag.assemble"):
        assert name in line.stages, name
    assert line.counters["rag_skipped"] == 0
    assert line.counters["rewrite_skipped"] == 0
    assert line.counters["sources_found"] >= 1
    assert line.counters["sources_used"] == line.counters["sources_found"]
    assert line.counters["context_chars"] > 0

    output = "\n".join(formatter.format(r) for r in records)
    assert SECRET not in output, "a log line contains the user's question or its rewrite"


def test_a_failed_search_does_not_log_the_query():
    """Found in the first real run: search_web's failure log printed the query."""
    class _FailingDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def text(self, query, max_results):
            # search libraries put the request URL, query included, in errors
            raise RuntimeError(f"GET https://html.duckduckgo.com/html/?q={query} failed")

    formatter = JsonFormatter()
    with _captured() as records, patch.object(web_search, "DDGS", _FailingDDGS):
        assert web_search.search_web(f"where is {SECRET} parking") == []
    output = "\n".join(formatter.format(r) for r in records)
    assert records, "the failure should still be logged"
    assert SECRET not in output


def test_search_library_loggers_are_quiet():
    """primp logged every search-engine URL, question included, at INFO."""
    import logging_config

    logging_config.setup_logging()
    for name in logging_config.QUIET_LOGGERS:
        assert not logging.getLogger(name).isEnabledFor(logging.INFO), name
    assert "primp" in logging_config.QUIET_LOGGERS


def test_skipped_retrieval_is_counted():
    client = TestClient(main.app)
    with _captured("timings") as records, patch("routers.chat.stream_chat", _fake_stream):
        _post(client, content="thanks!")
    assert _timing_lines(records)[0].counters["rag_skipped"] == 1


# ── 4. Client timing sink ─────────────────────────────────────────────────────

GOOD_EVENT = {
    "kind": "send",
    "outcome": "ok",
    "request_id": "0123abcd",
    "model": "quality",
    "marks": {"ack": 2.1, "first_token": 5012.4, "total": 6100},
}


def test_telemetry_accepts_and_logs_a_batch():
    client = TestClient(main.app)
    with _captured("client_timings") as records:
        res = client.post("/api/telemetry", json={"events": [GOOD_EVENT, {**GOOD_EVENT, "kind": "edit"}]})
    assert res.status_code == 204
    assert [r.kind for r in records] == ["send", "edit"]
    assert records[0].marks["first_token"] == 5012.4


def test_telemetry_rejects_anything_but_bounded_numbers():
    client = TestClient(main.app)
    bad_events = [
        {**GOOD_EVENT, "kind": "anything"},
        {**GOOD_EVENT, "request_id": "has spaces"},
        {**GOOD_EVENT, "model": "Not A Model Key!"},
        {**GOOD_EVENT, "marks": {"what the user typed": 1}},
        {**GOOD_EVENT, "marks": {"ack": -5}},
        {**GOOD_EVENT, "marks": {f"m{i}": 1 for i in range(25)}},
        {**GOOD_EVENT, "text": "extra fields are ignored, not logged"},
    ]
    for event in bad_events[:-1]:
        res = client.post("/api/telemetry", json={"events": [event]})
        assert res.status_code == 422, event

    with _captured("client_timings") as records:
        res = client.post("/api/telemetry", json={"events": [bad_events[-1]]})
    assert res.status_code == 204
    assert not hasattr(records[0], "text")

    assert client.post("/api/telemetry", json={"events": []}).status_code == 422
    assert client.post("/api/telemetry", json={"events": [GOOD_EVENT] * 51}).status_code == 422
