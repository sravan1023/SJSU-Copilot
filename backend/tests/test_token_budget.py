"""
Tests for services/token_budget.py and the model configuration in services/llm.py.

Run from backend/ with:
    python -m tests.test_token_budget

No network: nothing here calls a provider.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.token_budget import (  # noqa: E402
    BudgetLimits,
    MIN_USEFUL_RAG_TOKENS,
    clamp_completion_tokens,
    count_tokens,
    fit_prompt,
)

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


def _msgs(*contents):
    return [{"role": "user", "content": c} for c in contents]


# ── 1. Counting ───────────────────────────────────────────────────────────────


def test_count_tokens():
    print("\n[1.1] count_tokens")
    _check("empty is zero", count_tokens("") == 0)
    _check("None is zero", count_tokens(None) == 0)
    _check("short text is small", 0 < count_tokens("hello world") < 10)
    _check(
        "scales with length",
        count_tokens("word " * 1000) > count_tokens("word " * 10),
    )
    # The estimate should be in the right ballpark for English prose.
    prose = "The registrar handles registration and transcripts for students. " * 50
    est = count_tokens(prose)
    _check(
        "estimate is within a sane range for prose",
        len(prose) / 8 < est < len(prose) / 2,
        f"{est} tokens for {len(prose)} chars",
    )


# ── 2. Trimming priority ──────────────────────────────────────────────────────


def test_no_trim_when_within_budget():
    print("\n[2.1] a small request is left alone")
    limits = BudgetLimits(max_prompt_tokens=4000, max_completion_tokens=1500)
    fitted = fit_prompt("policy", "memory", "context", _msgs("hi"), limits)

    _check("not trimmed", not fitted.report.trimmed)
    _check("policy retained", "policy" in fitted.system_prompt)
    _check("memory retained", "memory" in fitted.system_prompt)
    _check("rag retained", "context" in fitted.system_prompt)
    _check("message retained", len(fitted.messages) == 1)


def test_rag_is_trimmed_first():
    print("\n[2.2] retrieved context is trimmed before history or memory")
    limits = BudgetLimits(max_prompt_tokens=800, max_completion_tokens=1500)
    big_rag = "Context: " + ("retrieved sentence about campus parking. " * 500)
    fitted = fit_prompt("policy", "memory here", big_rag, _msgs("a", "b", "c"), limits)

    _check("rag was trimmed", fitted.report.rag_tokens_dropped > 0)
    _check("history untouched", fitted.report.messages_dropped == 0, str(fitted.report))
    _check("memory untouched", not fitted.report.memory_dropped)
    _check("memory still present", "memory here" in fitted.system_prompt)
    _check(
        "result fits the budget",
        fitted.report.prompt_tokens <= limits.max_prompt_tokens,
        f"{fitted.report.prompt_tokens} > {limits.max_prompt_tokens}",
    )


def test_history_trimmed_after_rag():
    print("\n[2.3] history is dropped oldest-first once rag is gone")
    limits = BudgetLimits(max_prompt_tokens=400, max_completion_tokens=1500)
    long_turn = "a long conversation turn about degree requirements. " * 40
    fitted = fit_prompt(
        "policy", "memory here", None,
        _msgs(long_turn + " FIRST", long_turn + " SECOND", "the actual question"),
        limits,
    )

    _check("some history dropped", fitted.report.messages_dropped > 0)
    _check(
        "latest turn preserved",
        fitted.messages[-1]["content"] == "the actual question",
        str(fitted.messages[-1]["content"][:40]),
    )
    _check(
        "oldest dropped first",
        all("FIRST" not in m["content"] for m in fitted.messages),
        str([m["content"][-10:] for m in fitted.messages]),
    )


def test_memory_dropped_last():
    print("\n[2.4] memory is only dropped when nothing else is left to cut")
    limits = BudgetLimits(max_prompt_tokens=120, max_completion_tokens=1500)
    fitted = fit_prompt(
        "policy",
        "a lengthy memory block about the user. " * 30,
        None,
        _msgs("short question"),
        limits,
    )

    _check("memory dropped", fitted.report.memory_dropped)
    _check("memory gone from prompt", "lengthy memory block" not in fitted.system_prompt)
    _check("policy survives", "policy" in fitted.system_prompt)


def test_latest_turn_never_dropped():
    print("\n[2.5] an oversized single turn still gets sent")
    limits = BudgetLimits(max_prompt_tokens=50, max_completion_tokens=1500)
    huge = "x " * 5000
    fitted = fit_prompt("policy", None, None, _msgs(huge), limits)

    _check("one message kept", len(fitted.messages) == 1)
    _check("it is the original", fitted.messages[0]["content"] == huge)


def test_tiny_rag_is_dropped_not_slivered():
    print("\n[2.6] rag is dropped entirely rather than left as a useless fragment")
    limits = BudgetLimits(max_prompt_tokens=200, max_completion_tokens=1500)
    policy = "policy text. " * 60  # eats most of the budget
    fitted = fit_prompt(policy, None, "Context: " + ("source text. " * 200), _msgs("q"), limits)

    _check(
        "rag dropped entirely",
        fitted.report.rag_dropped_entirely,
        f"kept {MIN_USEFUL_RAG_TOKENS}-token floor? report={fitted.report}",
    )
    _check("no partial context in prompt", "source text" not in fitted.system_prompt)


# ── 3. System prompt ordering ─────────────────────────────────────────────────


def test_prompt_ordering_is_stable_prefix_first():
    print("\n[3.1] policy leads, then memory, then rag")
    limits = BudgetLimits(max_prompt_tokens=4000, max_completion_tokens=1500)
    fitted = fit_prompt("POLICY", "MEMORY", "RAG", _msgs("q"), limits)
    p = fitted.system_prompt

    _check("policy first", p.index("POLICY") < p.index("MEMORY"))
    _check("memory before rag", p.index("MEMORY") < p.index("RAG"))
    _check("prompt starts with policy", p.startswith("POLICY"))


# ── 4. Completion clamp and model config ──────────────────────────────────────


def test_clamp_completion_tokens():
    print("\n[4.1] completion budget clamp")
    limits = BudgetLimits(max_prompt_tokens=4000, max_completion_tokens=1500)
    _check("under the cap passes through", clamp_completion_tokens(512, limits) == 512)
    _check("over the cap is clamped", clamp_completion_tokens(4096, limits) == 1500)
    _check("never returns zero", clamp_completion_tokens(0, limits) >= 1)


def test_model_resolution():
    print("\n[4.2] model key resolution and legacy aliases")
    from services import llm

    _check("'fast' resolves", llm.resolve_model("fast") == llm.MODEL_FAST)
    _check("'quality' resolves", llm.resolve_model("quality") == llm.MODEL_QUALITY)
    _check("legacy '8b' -> fast", llm.resolve_model("8b") == llm.MODEL_FAST)
    _check("legacy '70b' -> quality", llm.resolve_model("70b") == llm.MODEL_QUALITY)
    _check("unknown falls back to quality", llm.resolve_model("nonsense") == llm.MODEL_QUALITY)
    _check("None falls back to quality", llm.resolve_model(None) == llm.MODEL_QUALITY)
    _check(
        "fast and quality are actually different models",
        llm.MODEL_FAST != llm.MODEL_QUALITY,
        f"{llm.MODEL_FAST} == {llm.MODEL_QUALITY}",
    )
    _check("title model is not the quality model", llm.MODEL_TITLE != llm.MODEL_QUALITY)


def test_completion_params_by_model_family():
    print("\n[4.3] reasoning models get headroom and reasoning_effort")
    from services import llm

    plain = llm._completion_params("llama-3.3-70b-versatile", 60)
    _check("no reasoning_effort for llama", "reasoning_effort" not in plain)
    _check("uses max_completion_tokens", "max_completion_tokens" in plain)
    _check("no legacy max_tokens key", "max_tokens" not in plain)
    _check("llama budget is the visible ask", plain["max_completion_tokens"] == 60)

    reasoning = llm._completion_params("openai/gpt-oss-20b", 60)
    _check("reasoning_effort sent for gpt-oss", reasoning.get("reasoning_effort") == llm.REASONING_EFFORT)
    _check(
        "reasoning model gets headroom above the visible ask",
        reasoning["max_completion_tokens"] > 60,
        str(reasoning),
    )


# ── 5. End-to-end request body ────────────────────────────────────────────────


class _FakeStream:
    """Stands in for httpx's streaming response."""

    status_code = 200

    def __init__(self, chunks):
        self._chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aiter_text(self):
        for chunk in self._chunks:
            yield chunk

    async def aread(self):
        return b""


class _CapturingClient:
    """Captures the JSON body stream_chat would send to the provider."""

    captured = None
    stream_kwargs = {}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url, headers=None, json=None, **kwargs):
        _CapturingClient.captured = json
        _CapturingClient.stream_kwargs = kwargs
        return _FakeStream([
            'data: {"choices":[{"delta":{"content":"Hello"}}]}\n',
            "data: [DONE]\n",
        ])


def test_outgoing_request_body():
    print("\n[5.1] the body sent to the provider reflects the budget")
    import asyncio
    from unittest.mock import patch

    from services import llm

    long_rag = "Context:\n[1] SJSU - https://sjsu.edu\n" + ("parking information. " * 2000)
    messages = [{"role": "user", "content": f"turn {i}"} for i in range(10)]
    messages.append({"role": "user", "content": "where do I park"})

    async def drive():
        out = []
        with patch.object(llm.httpx, "AsyncClient", _CapturingClient), \
             patch.dict(os.environ, {"GROQ_API_KEY": "test-key", "MAX_PROMPT_TOKENS": "600"}):
            async for frame in llm.stream_chat(
                messages=messages,
                model="quality",
                behavior={"response_length": "detailed"},
                memory_prompt="MEMORYBLOCK",
                rag_prompt=long_rag,
                request_id="req-1",
            ):
                out.append(frame)
        return out

    frames = asyncio.run(drive())
    body = _CapturingClient.captured

    _check("a body was captured", body is not None)
    _check("model id resolved, not the key", body["model"] == llm.MODEL_QUALITY, str(body.get("model")))
    _check("stream enabled", body["stream"] is True)
    _check("uses max_completion_tokens", "max_completion_tokens" in body)
    _check("legacy max_tokens not sent", "max_tokens" not in body)
    _check(
        "'detailed' 4096 clamped to the ceiling",
        body["max_completion_tokens"] <= 1500,
        str(body.get("max_completion_tokens")),
    )

    system = body["messages"][0]
    _check("first message is the system prompt", system["role"] == "system")

    prompt_tokens = count_tokens(system["content"]) + sum(
        count_tokens(m["content"]) for m in body["messages"][1:]
    )
    _check(
        "whole request fits the configured prompt budget",
        prompt_tokens <= 600 + 100,  # small allowance for per-message overhead
        f"{prompt_tokens} tokens against a 600 budget",
    )
    _check(
        "oversized context was cut down",
        len(system["content"]) < len(long_rag),
        f"system {len(system['content'])} chars vs rag {len(long_rag)}",
    )
    _check(
        "the actual question survived",
        body["messages"][-1]["content"] == "where do I park",
        str(body["messages"][-1]),
    )
    _check("tokens still streamed to the caller", any("Hello" in f for f in frames))
    _check("done frame carries request_id", any("req-1" in f for f in frames))
    _check(
        "per-request timeout is set on the shared client",
        _CapturingClient.stream_kwargs.get("timeout") == 120,
        str(_CapturingClient.stream_kwargs),
    )


def run():
    test_count_tokens()
    test_no_trim_when_within_budget()
    test_rag_is_trimmed_first()
    test_history_trimmed_after_rag()
    test_memory_dropped_last()
    test_latest_turn_never_dropped()
    test_tiny_rag_is_dropped_not_slivered()
    test_prompt_ordering_is_stable_prefix_first()
    test_clamp_completion_tokens()
    test_model_resolution()
    test_completion_params_by_model_family()
    test_outgoing_request_body()

    print("\n" + "=" * 60)
    print(f"  Passed: {PASS}")
    print(f"  Failed: {FAIL}")
    print("=" * 60)
    for label, detail in FAILURES:
        print(f"  - {label}: {detail}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(run())
