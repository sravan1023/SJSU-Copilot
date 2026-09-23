"""
Tests for the /api/chat SSE stream (routers/chat.py).

Run from backend/ with:
    python -m tests.test_chat_stream

Covers the stream restructure: response headers and the first status
frames must reach the client before retrieval finishes, rather than after it.
Also covers request bounds and terminal error framing.

Groq and the web RAG pipeline are both stubbed — nothing here touches the
network.
"""
import asyncio
import json
import socket
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from .conftest import AUTH_HEADERS  # noqa: E402
import uvicorn  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402


PASS = 0
FAIL = 0
FAILURES = []

# How long the stubbed retrieval blocks for. Long enough that "before" and
# "after" are unambiguous without making the suite slow.
RAG_DELAY = 1.0


def _check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        FAILURES.append((label, detail))
        print(f"  FAIL {label}  {detail}")


async def _slow_rag(messages, audience=None):
    await asyncio.sleep(RAG_DELAY)
    return "CONTEXT", [{"title": "SJSU", "url": "https://sjsu.edu"}]


async def _fake_stream(**kwargs):
    yield f"data: {json.dumps({'token': 'Hello'})}\n\n"
    yield f"data: {json.dumps({'token': ' world'})}\n\n"
    done = {
        "done": True,
        "full_response": "Hello world",
        "validators_run": [],
        "validators_passed": True,
        "repairs_applied": [],
        "sources": kwargs.get("sources") or [],
    }
    if kwargs.get("request_id"):
        done["request_id"] = kwargs["request_id"]
    yield f"data: {json.dumps(done)}\n\n"


def _collect(client, body):
    """POST and return (elapsed_seconds, parsed_frame_or_raw) for each SSE line.

    TestClient buffers the response body, so the elapsed values here are only
    good for ordering. Timing assertions use _live_server below.
    """
    frames = []
    start = time.monotonic()
    with client.stream("POST", "/api/chat", json=body, headers=AUTH_HEADERS) as res:
        header_time = time.monotonic() - start
        for line in res.iter_lines():
            if not line:
                continue
            elapsed = time.monotonic() - start
            if line.startswith("data: "):
                frames.append((elapsed, json.loads(line[6:])))
            else:
                frames.append((elapsed, line))
    return header_time, frames


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _ThreadedServer(uvicorn.Server):
    def install_signal_handlers(self):
        pass  # not the main thread


@contextmanager
def _live_server(app):
    """Run the app under a real uvicorn server on a free port.

    Needed because TestClient buffers the whole response, which would make a
    streaming regression invisible. Patches applied in the calling thread are
    visible here — uvicorn runs in this same process.
    """
    port = _free_port()
    server = _ThreadedServer(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    if not server.started:
        raise RuntimeError("uvicorn did not start")

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=15)


def _collect_live(base_url, body):
    """Same as _collect, but over a real socket so timings are meaningful."""
    frames = []
    start = time.monotonic()
    with httpx.Client(timeout=30) as client:
        with client.stream("POST", f"{base_url}/api/chat", json=body, headers=AUTH_HEADERS) as res:
            header_time = time.monotonic() - start
            for line in res.iter_lines():
                if not line:
                    continue
                elapsed = time.monotonic() - start
                if line.startswith("data: "):
                    frames.append((elapsed, json.loads(line[6:])))
                else:
                    frames.append((elapsed, line))
    return header_time, frames


# ── 1. Retrieval no longer blocks the first byte ──────────────────────────────


def test_status_frames_precede_retrieval():
    print("\n[1.1] status frames arrive before retrieval completes (real server)")
    body = {"messages": [{"role": "user", "content": "when is add/drop deadline"}]}

    with patch("routers.chat.build_rag_prompt", _slow_rag), \
         patch("routers.chat.stream_chat", _fake_stream):
        with _live_server(main.app) as base_url:
            header_time, frames = _collect_live(base_url, body)

    keys = [f[1].get("status") for f in frames if isinstance(f[1], dict)]
    _check("'received' emitted first", keys[:1] == ["received"], f"got {keys[:3]}")
    _check("'searching' emitted second", keys[:2] == ["received", "searching"], f"got {keys[:3]}")

    searching = next(t for t, f in frames if isinstance(f, dict) and f.get("status") == "searching")
    generating = next(t for t, f in frames if isinstance(f, dict) and f.get("status") == "generating")

    _check(
        "'searching' lands before retrieval finishes",
        searching < RAG_DELAY,
        f"searching at {searching:.3f}s, retrieval takes {RAG_DELAY}s",
    )
    _check(
        "'generating' lands after retrieval finishes",
        generating >= RAG_DELAY,
        f"generating at {generating:.3f}s",
    )
    _check(
        "response headers flush before retrieval finishes",
        header_time < RAG_DELAY,
        f"headers at {header_time:.3f}s",
    )

    first_token = next(t for t, f in frames if isinstance(f, dict) and "token" in f)
    _check(
        "first token still follows retrieval",
        first_token >= RAG_DELAY,
        f"first token at {first_token:.3f}s",
    )


def test_keepalive_during_retrieval():
    print("\n[1.2] keepalive comments are emitted while retrieval runs")
    body = {"messages": [{"role": "user", "content": "hi"}]}

    # 0.3s against a 1.0s retrieval, so several pings land without slowing the suite.
    with patch("routers.chat.build_rag_prompt", _slow_rag), \
         patch("routers.chat.stream_chat", _fake_stream), \
         patch("routers.chat.KEEPALIVE_SECONDS", 0.3):
        with _live_server(main.app) as base_url:
            _, frames = _collect_live(base_url, body)

    pings = [(t, f) for t, f in frames if isinstance(f, str)]
    _check("at least two keepalives emitted", len(pings) >= 2, f"got {len(pings)}")
    _check("keepalives are SSE comments", all(f.startswith(":") for _, f in pings), str(pings[:2]))
    _check(
        "keepalives stop once retrieval finishes",
        all(t < RAG_DELAY + 0.2 for t, _ in pings),
        str([round(t, 3) for t, _ in pings]),
    )
    _check(
        "stream still completes normally",
        any(isinstance(f, dict) and f.get("done") for _, f in frames),
    )


def test_done_frame_carries_sources_and_request_id():
    print("\n[1.2] done frame shape")
    client = TestClient(main.app)
    body = {"messages": [{"role": "user", "content": "parking on campus"}]}

    with patch("routers.chat.build_rag_prompt", _slow_rag), \
         patch("routers.chat.stream_chat", _fake_stream):
        _, frames = _collect(client, body)

    done = next(f for _, f in frames if isinstance(f, dict) and f.get("done"))
    _check("done carries full_response", done.get("full_response") == "Hello world")
    _check("done carries sources from retrieval", len(done.get("sources") or []) == 1, str(done.get("sources")))
    _check("done carries request_id", bool(done.get("request_id")), str(done.get("request_id")))


def test_request_id_echoed_in_header():
    print("\n[1.3] X-Request-Id echoes a client-supplied id")
    client = TestClient(main.app)

    async def _fast_rag(messages, audience=None):
        return None, []

    with patch("routers.chat.build_rag_prompt", _fast_rag), \
         patch("routers.chat.stream_chat", _fake_stream):
        res = client.post(
            "/api/chat",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers={**AUTH_HEADERS, "x-request-id": "client-supplied-id"},
        )

    _check(
        "header round-trips",
        res.headers.get("x-request-id") == "client-supplied-id",
        str(res.headers.get("x-request-id")),
    )


# ── 2. Failures produce a terminal error frame ────────────────────────────────


def test_retrieval_failure_yields_error_frame():
    print("\n[2.1] a crash during retrieval ends the stream with an error frame")
    client = TestClient(main.app)

    async def _boom(messages):
        raise RuntimeError("search exploded")

    with patch("routers.chat.build_rag_prompt", _boom), \
         patch("routers.chat.stream_chat", _fake_stream):
        _, frames = _collect(client, {"messages": [{"role": "user", "content": "hi"}]})

    errors = [f for _, f in frames if isinstance(f, dict) and "error" in f]
    _check("an error frame is emitted", len(errors) == 1, str(frames))
    _check(
        "no done frame claims success",
        not any(isinstance(f, dict) and f.get("done") for _, f in frames),
        str(frames),
    )


def test_generation_failure_yields_error_frame():
    print("\n[2.2] a crash during generation ends the stream with an error frame")
    client = TestClient(main.app)

    async def _fast_rag(messages, audience=None):
        return None, []

    async def _boom_stream(**kwargs):
        yield f"data: {json.dumps({'token': 'partial'})}\n\n"
        raise RuntimeError("provider exploded")

    with patch("routers.chat.build_rag_prompt", _fast_rag), \
         patch("routers.chat.stream_chat", _boom_stream):
        _, frames = _collect(client, {"messages": [{"role": "user", "content": "hi"}]})

    errors = [f for _, f in frames if isinstance(f, dict) and "error" in f]
    _check("partial token was delivered", any(isinstance(f, dict) and "token" in f for _, f in frames))
    _check("error frame terminates the stream", len(errors) == 1, str(frames))


# ── 3. Request bounds ─────────────────────────────────────────────────────────


def test_request_bounds():
    print("\n[3.1] oversized and malformed requests are rejected before the handler")
    client = TestClient(main.app)
    from routers.chat import MAX_MESSAGES, MAX_MESSAGE_CHARS, MAX_TOTAL_CHARS

    cases = [
        ("too many messages", {"messages": [{"role": "user", "content": "x"}] * (MAX_MESSAGES + 1)}),
        ("message too long", {"messages": [{"role": "user", "content": "x" * (MAX_MESSAGE_CHARS + 1)}]}),
        ("total too large", {"messages": [{"role": "user", "content": "x" * 7000}] * 10}),
        ("empty message list", {"messages": []}),
        ("invalid role", {"messages": [{"role": "root", "content": "x"}]}),
        ("memory_prompt too long", {"messages": [{"role": "user", "content": "hi"}], "memory_prompt": "x" * 5000}),
    ]

    # If a bound leaked through to the handler these would call the real
    # pipeline, so patch it to something that would be obvious in the output.
    async def _should_not_run(messages, audience=None):
        raise AssertionError("handler ran for a request that should have been rejected")

    with patch("routers.chat.build_rag_prompt", _should_not_run):
        for label, body in cases:
            res = client.post("/api/chat", json=body, headers=AUTH_HEADERS)
            _check(f"{label} -> 422", res.status_code == 422, f"got {res.status_code}")

    _check("MAX_TOTAL_CHARS is below the model's practical limit", MAX_TOTAL_CHARS <= 48_000)


def run():
    test_status_frames_precede_retrieval()
    test_keepalive_during_retrieval()
    test_done_frame_carries_sources_and_request_id()
    test_request_id_echoed_in_header()
    test_retrieval_failure_yields_error_frame()
    test_generation_failure_yields_error_frame()
    test_request_bounds()

    print("\n" + "=" * 60)
    print(f"  Passed: {PASS}")
    print(f"  Failed: {FAIL}")
    print("=" * 60)
    if FAILURES:
        for label, detail in FAILURES:
            print(f"  - {label}: {detail}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(run())
