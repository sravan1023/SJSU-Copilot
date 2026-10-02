import asyncio
import json
import logging
import re
import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

import observability
import audiences
import ratelimit
from auth import Principal
from ratelimit import rate_limited
from services import structured_answers
from services import student_context as student_ctx
from services.llm import stream_chat, generate_title
from services.token_budget import count_tokens
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

# Routers read prepared data, not the network, so they get far less than
# retrieval does. A router that cannot answer in a second is skipped: the turn
# is better served by search than by waiting.
STRUCTURED_TIMEOUT_SECONDS = 1.0

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
    # Which experience to render the answer for.
    #
    # **The server must not trust this for anything authorization-shaped.** It
    # selects prompt text and which domains retrieval prefers, and nothing
    # else -- a caller claiming to be faculty gets faculty-flavoured wording,
    # not faculty-only information, because no information is gated on it.
    # Authority comes from admin_grants and nowhere else.
    #
    # Unknown values fall back to the default rather than 422: audiences.get()
    # is total, and rejecting a request because a client sent a stale audience
    # id would break the chat over a cosmetic field.
    audience: str | None = Field(default=None, max_length=32)
    # The student's own profile facts, for the generation prompt only. Bounds are
    # on the model (an oversize or unknown-field payload is a 422). A guest's, or
    # any when DEGREE_CONTEXT_ENABLED is off, is dropped below rather than
    # rejected: a client that sends it has done nothing wrong.
    student_context: student_ctx.StudentContext | None = None

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


async def _structured_answer(
    messages: list[dict], audience: str, principal_kind: str, request_id: str
) -> structured_answers.Answer | None:
    """Ask the structured-answer routers, never failing the turn.

    Any exception, a timeout included, degrades to None, which is today's
    behaviour. CancelledError is a BaseException and so still propagates when
    the client goes away.
    """
    answer = None
    with observability.stage("structured.route"):
        try:
            answer = await asyncio.wait_for(
                structured_answers.answer(messages, audience, principal_kind),
                STRUCTURED_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            observability.record(
                "structured_fallthrough",
                "timeout" if isinstance(exc, TimeoutError) else "error",
            )
            logger.warning(
                "structured answer failed, falling through to retrieval",
                extra={"request_id": request_id, "error_type": type(exc).__name__},
            )
    if answer is not None and answer.mode not in structured_answers.MODES:
        logger.warning(
            "structured answer has an unknown mode, ignored",
            extra={"request_id": request_id},
        )
        return None
    if answer is not None:
        observability.record("answer_route", answer.route or "unnamed")
        observability.record("answer_mode", answer.mode)
    return answer


# Shown when the model could not finish after a prefix card was already emitted.
PREFIX_FAILURE_LINE = "I couldn't finish the rest of this answer — please try again."


def _merge_sources(model_sources: list[dict] | None, extra: list[dict]) -> list[dict]:
    """Retrieval sources first, so the model's [N] numbers stay valid."""
    merged = list(model_sources or [])
    seen = {s.get("url") for s in merged}
    for s in extra or []:
        if s.get("url") not in seen:
            merged.append(s)
            seen.add(s.get("url"))
    return merged


def _prefix_failure_frames(
    prefix: str,
    streamed: str,
    answer,
    request_id: str,
    retrieval_sources: list[dict] | None = None,
) -> list[str]:
    """Close a prefix-mode turn whose generation failed, keeping the card.

    The client throws on an `error` frame and the UI then overwrites the message
    and persists nothing, so a `replace` before the error would not save the
    card. A terminal `done` whose full_response is the card plus a short note
    is the only shape that keeps and stores it. `streamed` is whatever model
    text the client already rendered, so the saved text matches the screen.

    `retrieval_sources` go first, as in the normal done frame: the model may
    already have streamed "[1]", and that must keep pointing at the page it
    cited, not at the card's.
    """
    note = ("\n\n" if streamed else "") + PREFIX_FAILURE_LINE
    done = {
        "done": True,
        "full_response": prefix + streamed + note,
        "validators_run": [],
        "validators_passed": True,
        "repairs_applied": [],
        "answer_mode": "prefix",
        # The answer was cut short, so the "based on your profile" caveat has
        # nothing to attach to.
        "student_context_used": False,
        "request_id": request_id,
    }
    if answer.card is not None:
        done["card"] = answer.card
    merged = _merge_sources(retrieval_sources, answer.sources)
    if merged:
        done["sources"] = merged
    return [_sse({"token": note}), _sse(done)]


def _prepend_to_full_text(frame: str, prefix: str, answer) -> str:
    """Make the done and replace frames carry the prefix the client already saw.

    A `replace` frame swaps the client's whole rendered text for the model's
    repaired answer, and `done.full_response` is what gets persisted. Both are
    built by stream_chat from the model output alone, so without this the
    prefix would vanish on a repair and be missing from the saved message.
    """
    if not (frame.startswith('data: {"done"') or frame.startswith('data: {"replace"')):
        return frame
    try:
        payload = json.loads(frame[len("data: "):])
    except json.JSONDecodeError:
        return frame
    if "replace" in payload:
        payload["replace"] = prefix + payload["replace"]
    else:
        payload["full_response"] = prefix + payload.get("full_response", "")
        payload["answer_mode"] = "prefix"
        if answer.card is not None:
            payload["card"] = answer.card
        if answer.sources:
            payload["sources"] = _merge_sources(payload.get("sources"), answer.sources)
    return _sse(payload)


async def _retrieve_with_keepalive(
    messages: list[dict],
    request_id: str,
    audience: str | None = None,
    principal_kind: str = "guest",
):
    """Run retrieval, emitting SSE comments while it works.

    Yields keepalive frames, then finally a ("result", (rag_prompt, sources))
    tuple. Retrieval is a task so a client disconnect can cancel it rather than
    leaving the search and crawl running.
    """
    # principal_kind defaults to the least-privileged value everywhere it is
    # threaded, so a call site that forgets it under-shares rather than leaks.
    task = asyncio.create_task(
        build_rag_prompt(messages, audience, principal_kind=principal_kind)
    )
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
async def chat(
    req: ChatRequest,
    request: Request,
    principal: Principal = Depends(rate_limited),
):
    messages = [m.model_dump() for m in req.messages]
    request_id = _request_id(request)

    # Normalised once, here, so every consumer below sees a value the config
    # actually knows. audiences.get() is total -- an unrecognised id degrades
    # to the default rather than raising -- which is what lets this be a plain
    # optional string on the request instead of a validated enum that would
    # 422 a client holding a stale id.
    audience = audiences.get(req.audience)["id"]

    # Claimed before the response object is built and released when the stream
    # drains, not when this function returns -- see the finally below. A
    # streaming turn occupies the provider for its whole duration, which is
    # precisely the window the cap exists to bound.
    if not ratelimit.acquire_slot(principal):
        raise ratelimit.concurrency_error()

    async def event_stream():
        # Everything that used to happen before StreamingResponse was
        # constructed now happens here. The first yield flushes response headers
        # immediately, so the browser stops waiting on retrieval to see a byte.
        #
        # Timing lives here rather than in middleware: middleware around a
        # StreamingResponse finishes when the response object is returned, not
        # when the stream drains, so it would record ~0 ms for every chat.
        trace = observability.begin(request_id)
        # auth.py timed itself onto request.state, because a dependency runs
        # before this generator and so before the trace ContextVar is set.
        observability.add_stage("auth_verify", getattr(request.state, "auth_verify_ms", 0.0))
        observability.record("principal", principal.kind)
        observability.record("audience", audience)
        observability.record("history_messages", len(messages))
        outcome = "ok"
        # Set before anything can raise, so the failure paths can tell whether a
        # prefix card is already on the client's screen. `streamed` is the model
        # text sent after it.
        prefix_text, streamed, structured = "", "", None
        retrieval_sources: list[dict] = []
        student_context_prompt = None
        try:
            # Decided once, here, and kept out of `messages` and out of every call
            # retrieval makes: the digest may only ever reach the generation prompt.
            if req.student_context is None:
                observability.record("student_context", "none")
            elif principal.kind != "user":
                observability.record("student_context", "dropped_guest")
            elif not student_ctx.enabled():
                observability.record("student_context", "disabled")
            else:
                student_context_prompt = student_ctx.render(req.student_context) or None
                observability.record("student_context", student_ctx.label(req.student_context))
                observability.record("student_context_tokens", count_tokens(student_context_prompt))
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

            structured = await _structured_answer(
                messages, audience, principal.kind, request_id
            )

            if structured is not None and structured.mode == "card":
                # Zero provider tokens: the card is the whole answer, so
                # stream_chat is never reached and no Groq request is made.
                yield _sse({"token": structured.markdown})
                done = {
                    "done": True,
                    "full_response": structured.markdown,
                    "validators_run": [],
                    "validators_passed": True,
                    "repairs_applied": [],
                    "answer_mode": "structured",
                    "student_context_used": False,
                    "card": structured.card,
                    "request_id": request_id,
                }
                if structured.sources:
                    done["sources"] = structured.sources
                yield _sse(done)
                return

            yield _sse({"status": "searching"})

            if structured is not None and structured.mode == "prefix":
                # Verbatim, then a separator frame, so the emitted markdown
                # stays byte-equal to its source and the model's answer starts
                # on its own paragraph.
                prefix_text = structured.markdown + "\n\n"
                yield _sse({"token": structured.markdown})
                yield _sse({"token": "\n\n"})

            rag_prompt, sources = None, []
            if structured is not None and structured.mode == "context":
                rag_prompt, sources = structured.rag_prompt, structured.sources
            else:
                with observability.stage("rag.total"):
                    async for item in _retrieve_with_keepalive(
                        messages, request_id, audience, principal.kind
                    ):
                        if isinstance(item, tuple):
                            rag_prompt, sources = item[1]
                        else:
                            yield item
            retrieval_sources = sources

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
                audience=audience,
                student_context_prompt=student_context_prompt,
            ):
                if frame.startswith('data: {"error"'):
                    outcome = "upstream_error"
                    if prefix_text:
                        for out in _prefix_failure_frames(
                            prefix_text, streamed, structured, request_id, retrieval_sources
                        ):
                            yield out
                        return
                if prefix_text:
                    if frame.startswith('data: {"token"'):
                        try:
                            streamed += json.loads(frame[len("data: "):]).get("token", "")
                        except json.JSONDecodeError:
                            pass
                    frame = _prepend_to_full_text(frame, prefix_text, structured)
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
            if prefix_text:
                for out in _prefix_failure_frames(
                    prefix_text, streamed, structured, request_id, retrieval_sources
                ):
                    yield out
            else:
                yield _sse({"error": "Something went wrong generating this answer."})
        finally:
            # The stream is over here and only here; releasing at handler
            # return would under-count every streaming turn.
            ratelimit.release_slot(principal)
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
async def auto_behavior(
    req: AutoBehaviorRequest,
    principal: Principal = Depends(rate_limited),
):
    """Return what the backend auto-detected for this conversation."""
    messages = [m.model_dump() for m in req.messages]
    state = analyze_conversation_state(messages)
    behavior = generate_default_behavior(state)
    return {
        "behavior": behavior,
        "state": state,
    }


@router.post("/generate-title")
async def title(req: TitleRequest, principal: Principal = Depends(rate_limited)):
    result = await generate_title(req.message)
    return {"title": result}
