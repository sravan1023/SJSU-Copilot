"""
A stand-in for Groq's chat completions API, for mock-mode benchmarks.

Streams an SSE answer with a configurable time to first token and per-token
delay, answers non-streaming calls (the query rewrite) after a fixed delay, and
can return 429s at a configurable rate. Its speed is whatever you set: mock mode
measures the app's own overhead and its behaviour under concurrency, not the
provider's.
"""
import asyncio
import json
import random
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse


@dataclass
class MockConfig:
    ttft_ms: float = 450          # request received -> first token
    token_ms: float = 2           # between tokens (gpt-oss on Groq streams fast)
    tokens: int = 300             # tokens per answer
    nonstream_ms: float = 450     # query rewrite latency
    jitter: float = 0.3           # +/- fraction applied to every delay
    rate_limit_rate: float = 0.0  # probability of answering 429


CONFIG = MockConfig()

ANSWER_WORDS = (
    "To apply for curricular practical training at SJSU, confirm your F-1 status, "
    "secure a written internship offer, enrol in the internship course for your "
    "major, and submit the CPT request through the ISSS portal [1]. Processing "
    "usually takes several business days, so apply well before your start date [2]. "
).split(" ")

_in_flight = 0
_max_in_flight = 0
_requests = 0


def _delay(ms: float) -> float:
    return max(0.0, ms * random.uniform(1 - CONFIG.jitter, 1 + CONFIG.jitter)) / 1000


def _answer_tokens():
    words = []
    while len(words) < CONFIG.tokens:
        words.extend(ANSWER_WORDS)
    return [w + " " for w in words[: CONFIG.tokens]]


app = FastAPI(title="mock upstream")


@app.post("/openai/v1/chat/completions")
async def completions(request: Request):
    global _requests
    _requests += 1
    body = await request.json()

    if random.random() < CONFIG.rate_limit_rate:
        return JSONResponse(
            {"error": {"message": "Rate limit reached (mock)"}},
            status_code=429,
            headers={"retry-after": "7"},
        )

    if not body.get("stream"):
        await asyncio.sleep(_delay(CONFIG.nonstream_ms))
        return {"choices": [{"message": {"role": "assistant", "content": "SJSU curricular practical training ISSS"}}]}

    async def stream():
        global _in_flight, _max_in_flight
        _in_flight += 1
        _max_in_flight = max(_max_in_flight, _in_flight)
        try:
            await asyncio.sleep(_delay(CONFIG.ttft_ms))
            # Pace against a schedule instead of sleeping per token: timers on
            # Windows can't sleep for 2 ms (each sleep rounds up to ~15 ms), so a
            # per-token sleep would stretch a 0.6 s answer to 4-5 s. Tokens that
            # are due go out together, and the average rate matches token_ms.
            loop = asyncio.get_running_loop()
            started = loop.time()
            per_token = _delay(CONFIG.token_ms)
            for i, token in enumerate(_answer_tokens()):
                wait = started + i * per_token - loop.time()
                if wait > 0.01:
                    await asyncio.sleep(wait)
                chunk = {"choices": [{"delta": {"content": token}}]}
                yield f"data: {json.dumps(chunk)}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            _in_flight -= 1

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/stats")
async def stats():
    """How many streams the backend held open at once -- its provider concurrency."""
    return {"requests": _requests, "in_flight": _in_flight, "max_in_flight": _max_in_flight}
