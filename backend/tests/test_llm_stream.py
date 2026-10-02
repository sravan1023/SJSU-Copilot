"""
Tests for the gpt-oss switch in services/llm.py: citation normalisation,
429 handling, and the default model ids.

Run from backend/ with:
    python -m tests.test_llm_stream

No network: the provider is replaced with a fake streaming client.
"""
import asyncio
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

import httpx  # noqa: E402

from services import llm  # noqa: E402
from services.citations import CitationStream, normalize_citations  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []


def _check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        FAILURES.append((label, detail))
        print(f"  FAIL {label}  {detail}")


# ── Fake provider ─────────────────────────────────────────────────────────────


class _FakeStream:
    def __init__(self, status_code=200, chunks=(), headers=None, body=b""):
        self.status_code = status_code
        self.headers = httpx.Headers(headers or {})
        self._chunks = list(chunks)
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aiter_text(self):
        for chunk in self._chunks:
            yield chunk

    async def aread(self):
        return self._body


class _FakeClient:
    next_stream = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url, **kwargs):
        return _FakeClient.next_stream


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def _sse_deltas(deltas):
    lines = [
        f"data: {json.dumps({'choices': [{'delta': {'content': d}}]})}\n" for d in deltas
    ]
    return lines + ["data: [DONE]\n"]


SOURCES = [{"title": "Drops", "url": "https://www.sjsu.edu/drops"}]
RAG = "Context:\n[1] Drops - https://www.sjsu.edu/drops\nThe drop deadline is Sep 15."


def _drive(stream, sources=SOURCES):
    """Run stream_chat against a fake provider; return (frames, llm log records)."""
    handler = _ListHandler()
    llm_logger = logging.getLogger("services.llm")
    llm_logger.addHandler(handler)

    async def go():
        frames = []
        _FakeClient.next_stream = stream
        with patch.object(llm.httpx, "AsyncClient", _FakeClient), \
             patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}):
            async for frame in llm.stream_chat(
                messages=[{"role": "user", "content": "when is the drop deadline"}],
                model="fast",
                rag_prompt=RAG if sources else None,
                sources=sources,
                request_id="req-test",
            ):
                frames.append(json.loads(frame[len("data: "):]))
        return frames

    try:
        return asyncio.run(go()), handler.records
    finally:
        llm_logger.removeHandler(handler)


# ── 1. Citation normalisation ─────────────────────────────────────────────────


def test_normalize_citations():
    print("\n[1.1] gpt-oss citation markers become [N]")
    cases = [
        ("line-range form", "see the page【2†L31-L33】.", "see the page[2]."),
        ("source-name form", "per ISSS【4†source】", "per ISSS[4]"),
        ("bare number", "details【5】", "details[5]"),
        ("adjacent markers", "both【1†L1-L3】【3†L9】", "both[1][3]"),
        ("inner whitespace", "x【 2 †L1】", "x[2]"),
        ("existing [N] untouched", "already cited [1].", "already cited [1]."),
        ("non-citation brackets untouched", "【注意】 read this", "【注意】 read this"),
        ("marker with no number untouched", "odd【†L1】", "odd【†L1】"),
        ("empty string", "", ""),
    ]
    for label, given, expected in cases:
        got = normalize_citations(given)
        _check(label, got == expected, f"{given!r} -> {got!r}, expected {expected!r}")


def test_citation_stream_split_points():
    print("\n[1.2] a marker split across deltas is normalised at every split point")
    text = "Drop by Sep 15【2†L31-L33】 then petition【3†source】."
    expected = normalize_citations(text)

    two_way_ok, leaked = True, []
    for i in range(len(text) + 1):
        stream = CitationStream()
        pieces = [stream.feed(text[:i]), stream.feed(text[i:]), stream.flush()]
        if "".join(pieces) != expected:
            two_way_ok = False
        leaked += [p for p in pieces if "【" in p or "】" in p]
    _check("every two-way split reassembles correctly", two_way_ok)
    _check("no emitted piece ever contains a raw marker", not leaked, repr(leaked[:3]))

    stream = CitationStream()
    pieces = [stream.feed(ch) for ch in text] + [stream.flush()]
    _check("char-by-char stream reassembles correctly", "".join(pieces) == expected, repr("".join(pieces)))


def test_citation_stream_holding():
    print("\n[1.3] only the unclosed tail is held back")
    stream = CitationStream()
    _check("text before an open marker is released at once", stream.feed("Hello 【2") == "Hello ")
    _check("the rest arrives once the marker closes", stream.feed("†L1】 bye") == "[2] bye")

    stream = CitationStream()
    _check("unclosed tail held until the stream ends", stream.feed("tail 【not a cite") == "tail ")
    _check("flush releases the held tail unchanged", stream.flush() == "【not a cite")
    _check("nothing held after flush", stream.flush() == "")

    stream = CitationStream()
    long_text = "a 【" + "x" * 100
    _check("an open bracket that never closes is not held forever", stream.feed(long_text) == long_text)


# ── 2. stream_chat end to end ─────────────────────────────────────────────────


def test_stream_chat_normalises_split_marker():
    print("\n[2.1] stream_chat normalises a marker split across provider deltas")
    deltas = ["The last day is Sep 15 ", "【", "1", "†L31", "-L33", "】", ". Petition after."]
    frames, logs = _drive(_FakeStream(chunks=_sse_deltas(deltas)))

    tokens = "".join(f["token"] for f in frames if "token" in f)
    done = next((f for f in frames if f.get("done")), {})
    expected = "The last day is Sep 15 [1]. Petition after."

    _check("streamed tokens carry [1]", tokens == expected, repr(tokens))
    _check("done.full_response matches the streamed text", done.get("full_response") == expected, repr(done.get("full_response")))
    _check("no frame contains a raw marker", not any("【" in json.dumps(f, ensure_ascii=False) for f in frames))
    _check("sources still attached", done.get("sources") == SOURCES)
    _check(
        "missing-citation warning not raised",
        not any("no [N] citations" in r.getMessage() for r in logs),
        [r.getMessage() for r in logs],
    )


def test_stream_chat_unclosed_tail_is_kept():
    print("\n[2.2] an unclosed marker at the very end is released, not dropped")
    frames, _ = _drive(_FakeStream(chunks=_sse_deltas(["Answer text ", "【2†L1"])), sources=None)
    tokens = "".join(f["token"] for f in frames if "token" in f)
    done = next((f for f in frames if f.get("done")), {})
    _check("held tail is emitted as a token", tokens == "Answer text 【2†L1", repr(tokens))
    _check("and included in full_response", done.get("full_response") == "Answer text 【2†L1")


def test_missing_citation_warning_still_fires():
    print("\n[2.3] an answer with sources but genuinely no citations is still flagged")
    frames, logs = _drive(_FakeStream(chunks=_sse_deltas(["No citations here."])))
    _check(
        "warning logged",
        any("no [N] citations" in r.getMessage() for r in logs),
        [r.getMessage() for r in logs],
    )


# ── 3. Rate limiting ──────────────────────────────────────────────────────────


def _error_frames(stream):
    frames, logs = _drive(stream)
    return [f["error"] for f in frames if "error" in f], frames, logs


def test_rate_limited_frames():
    print("\n[3.1] a 429 becomes a rate_limited error with a retry time")
    body = b'{"error":{"message":"Rate limit reached ... Please try again in 7.66s."}}'
    cases = [
        ("retry-after header", {"retry-after": "7"}, 7),
        ("fractional retry-after rounds up", {"retry-after": "2.1"}, 3),
        ("token reset header", {"x-ratelimit-reset-tokens": "7.66s"}, 8),
        (
            "daily requests exhausted -> request reset",
            {
                "x-ratelimit-remaining-requests": "0",
                "x-ratelimit-reset-requests": "2m59.56s",
                "x-ratelimit-reset-tokens": "1s",
            },
            180,
        ),
        (
            "requests left -> token reset wins",
            {
                "x-ratelimit-remaining-requests": "12",
                "x-ratelimit-reset-requests": "2m59.56s",
                "x-ratelimit-reset-tokens": "1.2s",
            },
            2,
        ),
        (
            "HTTP-date retry-after falls through to the reset headers",
            {"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT", "x-ratelimit-reset-tokens": "4s"},
            4,
        ),
        ("no headers -> retry_after is null", {}, None),
    ]
    for label, headers, expected in cases:
        errors, frames, _ = _error_frames(_FakeStream(status_code=429, headers=headers, body=body))
        error = errors[0] if errors else None
        _check(
            label,
            error == {"code": "rate_limited", "retry_after": expected},
            repr(error),
        )

    errors, frames, logs = _error_frames(_FakeStream(status_code=429, headers={"retry-after": "7"}, body=body))
    _check("no tokens after a 429", not any("token" in f for f in frames))
    _check("no done frame claims success", not any(f.get("done") for f in frames))
    _check(
        "logged as a warning with the request id",
        any(r.levelno == logging.WARNING and getattr(r, "request_id", None) == "req-test" for r in logs),
    )


def test_other_errors_unchanged():
    print("\n[3.2] non-429 errors keep the existing string contract")
    errors, frames, logs = _error_frames(_FakeStream(status_code=404, body=b'{"error":"model_not_found"}'))
    _check("404 -> 'Upstream model error (404)'", errors == ["Upstream model error (404)"], repr(errors))
    _check("logged as an error with the request id", any(
        r.levelno == logging.ERROR and getattr(r, "request_id", None) == "req-test" for r in logs
    ))


def test_duration_parsing():
    print("\n[3.3] Groq reset-header durations")
    cases = [
        ("7.66s", 7.66),
        ("2m59.56s", 179.56),
        ("1h2m3s", 3723.0),
        ("250ms", 0.25),
        ("5", None),
        ("abc", None),
        ("", None),
        (None, None),
    ]
    for value, expected in cases:
        got = llm._parse_duration(value)
        ok = (got is None and expected is None) or (
            got is not None and expected is not None and abs(got - expected) < 1e-9
        )
        _check(f"{value!r} -> {expected}", ok, repr(got))


# ── 4. Default model ids ──────────────────────────────────────────────────────


def _import_llm_in_subprocess(env_overrides):
    """Import services.llm in a clean interpreter, so module-level defaults are real."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("MODEL_")}
    env.update(env_overrides)
    code = (
        "import json; from services import llm; print(json.dumps({"
        "'quality': llm.MODEL_QUALITY, 'fast': llm.MODEL_FAST, "
        "'title': llm.MODEL_TITLE, 'rewrite': llm.MODEL_REWRITE, "
        "'quality_reasoning': llm._is_reasoning_model(llm.MODEL_QUALITY), "
        "'fast_reasoning': llm._is_reasoning_model(llm.MODEL_FAST)}))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=60
    )
    if out.returncode != 0:
        return {"error": out.stderr[-500:]}
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_default_models():
    print("\n[4.1] defaults are models the account can reach")
    got = _import_llm_in_subprocess({})
    _check("quality defaults to gpt-oss-120b", got.get("quality") == "openai/gpt-oss-120b", repr(got))
    _check("fast defaults to gpt-oss-20b", got.get("fast") == "openai/gpt-oss-20b", repr(got))
    _check("no retired llama id left as a default", "llama" not in json.dumps(got))
    _check("title and rewrite follow the fast model", got.get("title") == got.get("rewrite") == got.get("fast"))
    _check(
        "defaults get reasoning headroom",
        got.get("quality_reasoning") is True and got.get("fast_reasoning") is True,
        repr(got),
    )

    print("\n[4.2] an env override still wins (the rollback path)")
    got = _import_llm_in_subprocess({"MODEL_QUALITY": "qwen/qwen3.8-27b"})
    _check("MODEL_QUALITY override applied", got.get("quality") == "qwen/qwen3.8-27b", repr(got))
    _check("non-gpt-oss override gets no reasoning_effort", got.get("quality_reasoning") is False)


def run():
    test_normalize_citations()
    test_citation_stream_split_points()
    test_citation_stream_holding()
    test_stream_chat_normalises_split_marker()
    test_stream_chat_unclosed_tail_is_kept()
    test_missing_citation_warning_still_fires()
    test_rate_limited_frames()
    test_other_errors_unchanged()
    test_duration_parsing()
    test_default_models()

    print("\n" + "=" * 60)
    print(f"  Passed: {PASS}")
    print(f"  Failed: {FAIL}")
    print("=" * 60)
    for label, detail in FAILURES:
        print(f"  - {label}: {detail}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(run())
