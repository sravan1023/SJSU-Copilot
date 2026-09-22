"""
A provider connection that fails before any response is retried once; a failure
after tokens have streamed is not.

Found in the mock benchmark at 50 concurrent turns: a pooled keep-alive
connection the server had already closed raised httpx.ReadError before the
response headers, and the user got "Something went wrong" for a request that
could simply have been sent again.

Run from backend/ with:
    python -m pytest tests/test_provider_retry.py
"""
import asyncio
import json
import os
from unittest.mock import patch

import httpx
import pytest

import observability
from services import llm


def _sse(*deltas):
    return [f"data: {json.dumps({'choices': [{'delta': {'content': d}}]})}\n" for d in deltas] + ["data: [DONE]\n"]


class _Stream:
    """One attempt: raises on open, raises mid-stream, or streams chunks."""

    def __init__(self, fail_on_open=None, chunks=(), fail_mid_stream=None):
        self.fail_on_open = fail_on_open
        self.chunks = list(chunks)
        self.fail_mid_stream = fail_mid_stream
        self.status_code = 200
        self.headers = httpx.Headers({})

    async def __aenter__(self):
        if self.fail_on_open:
            raise self.fail_on_open
        return self

    async def __aexit__(self, *exc):
        return False

    async def aiter_text(self):
        for chunk in self.chunks:
            yield chunk
        if self.fail_mid_stream:
            raise self.fail_mid_stream

    async def aread(self):
        return b""


class _Client:
    """Hands out the scripted attempts in order and counts them."""

    attempts: list = []
    opened = 0

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url, **kwargs):
        _Client.opened += 1
        return _Client.attempts.pop(0)


def _run(*attempts):
    _Client.attempts = list(attempts)
    _Client.opened = 0

    async def go():
        trace = observability.begin("retry-test")
        frames = []
        try:
            with patch.object(llm.httpx, "AsyncClient", _Client), \
                 patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}):
                async for frame in llm.stream_chat(messages=[{"role": "user", "content": "hi"}], model="fast"):
                    frames.append(json.loads(frame[len("data: "):]))
            return frames, None, trace
        except Exception as exc:  # the caller decides whether that was expected
            return frames, exc, trace

    return asyncio.run(go())


def test_a_drop_before_the_response_is_retried_once():
    frames, exc, trace = _run(
        _Stream(fail_on_open=httpx.ReadError("connection reset")),
        _Stream(chunks=_sse("Hello", " again")),
    )
    assert exc is None
    assert _Client.opened == 2
    assert "".join(f["token"] for f in frames if "token" in f) == "Hello again"
    assert frames[-1]["done"] is True
    assert trace.counters["provider_retries"] == 1


def test_two_drops_in_a_row_still_fail():
    frames, exc, _ = _run(
        _Stream(fail_on_open=httpx.RemoteProtocolError("server disconnected")),
        _Stream(fail_on_open=httpx.ConnectError("refused")),
    )
    assert isinstance(exc, httpx.ConnectError)
    assert _Client.opened == 2
    assert frames == []


def test_a_drop_after_tokens_have_streamed_is_not_retried():
    frames, exc, trace = _run(
        _Stream(chunks=_sse("Half an "), fail_mid_stream=httpx.ReadError("reset mid-stream")),
        _Stream(chunks=_sse("should never be used")),
    )
    assert isinstance(exc, httpx.ReadError)
    assert _Client.opened == 1, "a mid-stream failure must not replay the request"
    assert [f["token"] for f in frames if "token" in f] == ["Half an "]
    assert "provider_retries" not in trace.counters


@pytest.mark.parametrize("error", [httpx.ReadTimeout("slow"), httpx.PoolTimeout("pool")])
def test_timeouts_waiting_for_a_response_are_not_retried(error):
    # A 120 s read timeout already cost the user two minutes; don't double it.
    _, exc, _ = _run(_Stream(fail_on_open=error), _Stream(chunks=_sse("unused")))
    assert exc is error
    assert _Client.opened == 1
