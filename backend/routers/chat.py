import asyncio
import json
import logging
import re
import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

import observability
from services.llm import stream_chat, generate_title
from services.web_search import build_rag_prompt
from services.conversation_state import (
    analyze_conversation_state,
    generate_default_behavior,
    merge_behavior,
    adapt_behavior,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

# How often to emit an SSE comment while retrieval is running, so proxies and
# load balancers don't treat a slow search as an idle connection.
KEEPALIVE_SECONDS = 10

# Request bounds. These endpoints were previously unbounded: an unauthenticated
# caller could post an arbitrarily large body and burn provider tokens. The
# frontend sends at most the last 20 turns (UI/src/App.jsx builds a 20-message
# window), so these are comfortable headroom rather than a tight fit.
#
# These bound what a caller may send. What actually reaches the model is fitted
# separately by services/token_budget.py (system prompt + memory + history +
# retrieved context under one token ceiling).
MAX_MESSAGES = 30
MAX_MESSAGE_CHARS = 8_000
MAX_TOTAL_CHARS = 48_000
MAX_MEMORY_PROMPT_CHARS = 4_000
MAX_TITLE_MESSAGE_CHARS = 4_000


class Message(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str = Field(max_length=MAX_MESSAGE_CHARS)


def _check_total_chars(messages: list[Message]) -> list[Message]:
    total = sum(len(m.content) for m in messages)
    if total > MAX_TOTAL_CHARS:
        raise ValueError(
            f"conversation too large: {total} characters, limit {MAX_TOTAL_CHARS}"
        )
    return messages


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1, max_length=MAX_MESSAGES)
    model: str = Field(default="8b", max_length=64)
    behavior: dict | None = None
    memory_prompt: str | None = Field(default=None, max_length=MAX_MEMORY_PROMPT_CHARS)

    _check_messages = field_validator("messages")(_check_total_chars)


class AutoBehaviorRequest(BaseModel):
    messages: list[Message] = Field(min_length=1, max_length=MAX_MESSAGES)

    _check_messages = field_validator("messages")(_check_total_chars)


class TitleRequest(BaseModel):
    message: str = Field(max_length=MAX_TITLE_MESSAGE_CHARS)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


# A client may supply its own id (the benchmark does, to join its timings to the
# server's). It ends up in every log line for the request, so only accept a
# short plain token; anything else gets a fresh id.
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _request_id(request: Request) -> str:
    supplied = request.headers.get("x-request-id") or ""
    return supplied if _REQUEST_ID_RE.fullmatch(supplied) else uuid.uuid4().hex


async def _retrieve_with_keepalive(messages: list[dict], request_id: str):
    """Run retrieval, emitting SSE comments while it works.

    Yields keepalive frames, then finally a ("result", (rag_prompt, sources))
    tuple. Retrieval is a task so a client disconnect can cancel it rather than
    leaving the search and crawl running.
    """
    task = asyncio.create_task(build_rag_prompt(messages))
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=KEEPALIVE_SECONDS)
            if done:
                break
            yield ": ping\n\n"
        yield ("result", task.result())
    finally:
        if not task.done():
            task.cancel()


@router.post("/chat")
async def chat(req: ChatRequest, request: Request):
    messages = [m.model_dump() for m in req.messages]
    request_id = _request_id(request)

    async def event_stream():
        # Everything that used to happen before StreamingResponse was
        # constructed now happens here. The first yield flushes response headers
        # immediately, so the browser stops waiting on retrieval to see a byte.
        #
        # Timing lives here rather than in middleware: middleware around a
        # StreamingResponse finishes when the response object is returned, not
        # when the stream drains, so it would record ~0 ms for every chat.
        trace = observability.begin(request_id)
        observability.record("history_messages", len(messages))
        outcome = "ok"
        try:
            yield _sse({"status": "received", "request_id": request_id})

            with observability.stage("behavior_compute"):
                # 1. Analyze conversation state
                state = analyze_conversation_state(messages)

                # 2. Auto-detect baseline behavior
                auto_behavior = generate_default_behavior(state)

                # 3. Merge manual overrides (if any) on top of auto
                behavior = merge_behavior(auto_behavior, req.behavior)

                # 4. Adapt to conversation context
                behavior = adapt_behavior(behavior, state)

            yield _sse({"status": "searching"})

            rag_prompt, sources = None, []
            with observability.stage("rag.total"):
                async for item in _retrieve_with_keepalive(messages, request_id):
                    if isinstance(item, tuple):
                        rag_prompt, sources = item[1]
                    else:
                        yield item

            # Retrieval can take seconds; don't pay for generation if the user
            # already navigated away or hit stop.
            if await request.is_disconnected():
                outcome = "disconnected"
                logger.info(
                    "client disconnected before generation",
                    extra={"request_id": request_id},
                )
                return

            yield _sse({"status": "generating"})
            observability.mark("generating")

            async for frame in stream_chat(
                messages=messages,
                model=req.model,
                behavior=behavior,
                memory_prompt=req.memory_prompt,
                rag_prompt=rag_prompt,
                sources=sources,
                request_id=request_id,
            ):
                if frame.startswith('data: {"error"'):
                    outcome = "upstream_error"
                yield frame

        except asyncio.CancelledError:
            # Client went away. Let it propagate so the upstream call unwinds.
            outcome = "cancelled"
            logger.info("chat stream cancelled", extra={"request_id": request_id})
            raise
        except GeneratorExit:
            # Client went away while the stream was paused at a yield; the
            # server closes the generator instead of cancelling an await.
            outcome = "cancelled"
            raise
        except Exception:
            # Previously an exception here produced a bare 500 with no body the
            # client understood, or truncated the stream with no terminal frame.
            outcome = "error"
            logger.exception("chat stream failed", extra={"request_id": request_id})
            yield _sse({"error": "Something went wrong generating this answer."})
        finally:
            observability.flush(trace, outcome=outcome, model=req.model)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Request-Id": request_id,
        },
    )


@router.post("/auto-behavior")
async def auto_behavior(req: AutoBehaviorRequest):
    """Return what the backend auto-detected for this conversation."""
    messages = [m.model_dump() for m in req.messages]
    state = analyze_conversation_state(messages)
    behavior = generate_default_behavior(state)
    return {
        "behavior": behavior,
        "state": state,
    }


@router.post("/generate-title")
async def title(req: TitleRequest):
    result = await generate_title(req.message)
    return {"title": result}
