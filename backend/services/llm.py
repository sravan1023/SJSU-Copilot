"""
LLM service — handles Groq API calls (streaming and non-streaming).
Ported from UI/src/services/llamaService.js
"""
import os
import json
import logging
import math
import re
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx

import audiences
import observability
import runtime

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _provider_client():
    """Yield the shared provider client, or a temporary one outside a lifespan.

    The fallback keeps this module usable from scripts and tests; under the app
    the connection pool is reused across every call.
    """
    shared = runtime.get_provider_client()
    if shared is not None:
        yield shared
        return
    async with httpx.AsyncClient() as client:
        yield client


# Failures that can happen before the provider has sent any response: a pooled
# keep-alive connection the server already closed, a reset, a refused connect.
# Nothing has reached the user yet, so one retry is invisible to them. A failure
# after headers (mid-stream) is not retried: that would repeat half an answer.
_RETRYABLE_BEFORE_RESPONSE = (httpx.NetworkError, httpx.ConnectTimeout, httpx.RemoteProtocolError)
PROVIDER_ATTEMPTS = 2


@asynccontextmanager
async def _provider_stream(client, **kwargs):
    """client.stream("POST", GROQ_API_URL, ...), retried once if it fails before a response."""
    for attempt in range(1, PROVIDER_ATTEMPTS + 1):
        stream = client.stream("POST", GROQ_API_URL, **kwargs)
        try:
            res = await stream.__aenter__()
        except _RETRYABLE_BEFORE_RESPONSE as exc:
            if attempt == PROVIDER_ATTEMPTS:
                raise
            observability.incr("provider_retries")
            logger.warning(
                "provider connection failed before a response; retrying",
                extra={"request_id": observability.current_request_id(), "error": type(exc).__name__},
            )
            continue
        try:
            yield res
        except BaseException:
            if not await stream.__aexit__(*sys.exc_info()):
                raise
        else:
            await stream.__aexit__(None, None, None)
        return

from services.citations import CitationStream, normalize_citations
from services.policy_compiler import compile_policy
from services.token_budget import (
    clamp_completion_tokens,
    fit_prompt,
    limits_from_env,
)
from services.validator import run_validators

GROQ_API_URL = os.getenv("GROQ_API_URL", "https://api.groq.com/openai/v1/chat/completions")

# Model selection is configuration, not code.
#
# The defaults are gpt-oss because Groq retired the Llama 3.x models this app
# used: as of 2026-09-21, llama-3.3-70b-versatile and llama-3.1-8b-instant both
# return 404 model_not_found. Before pointing these at anything else, confirm
# the account can reach it -- published availability is not account access:
#     curl -H "Authorization: Bearer $GROQ_API_KEY" \
#          https://api.groq.com/openai/v1/models
MODEL_QUALITY = os.getenv("MODEL_QUALITY", "openai/gpt-oss-120b")
MODEL_FAST = os.getenv("MODEL_FAST", "openai/gpt-oss-20b")
MODEL_TITLE = os.getenv("MODEL_TITLE", MODEL_FAST)
MODEL_REWRITE = os.getenv("MODEL_REWRITE", MODEL_FAST)

MODELS = {
    "fast": MODEL_FAST,
    "quality": MODEL_QUALITY,
}

# Clients (and stored behavior_feedback_log rows) still use the old keys.
LEGACY_MODEL_KEYS = {"8b": "fast", "70b": "quality"}

DEFAULT_MODEL_KEY = "quality"

# Reasoning models emit reasoning tokens that count against the completion
# budget but are never displayed. Only send the parameter to models that
# understand it.
REASONING_EFFORT = os.getenv("REASONING_EFFORT", "low")


def resolve_model(key: str | None) -> str:
    """Map a UI model key ('fast'/'quality', or legacy '8b'/'70b') to a model id."""
    normalized = LEGACY_MODEL_KEYS.get(key, key)
    return MODELS.get(normalized, MODELS[DEFAULT_MODEL_KEY])


def _is_reasoning_model(model_id: str) -> bool:
    return "gpt-oss" in model_id


def _completion_params(model_id: str, visible_tokens: int, reasoning_headroom: int = 512) -> dict:
    """Completion-budget parameters, named per the model family.

    `visible_tokens` is the answer length we actually want. Reasoning models
    spend tokens from the same allowance before producing any visible output, so
    they need headroom on top -- reusing the old bare limits (60 for a query
    rewrite, 30 for a title) would leave nothing for the answer itself.
    """
    budget = visible_tokens
    params = {}
    if _is_reasoning_model(model_id):
        budget += reasoning_headroom
        params["reasoning_effort"] = REASONING_EFFORT
    params["max_completion_tokens"] = clamp_completion_tokens(budget)
    return params


_DURATION_RE = re.compile(r"(?:\d+(?:\.\d+)?(?:ms|h|m|s))+")
_DURATION_PART_RE = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")
_DURATION_SCALE = {"h": 3600, "m": 60, "s": 1, "ms": 0.001}


def _parse_duration(value: str | None) -> float | None:
    """Parse a Groq reset header ('7.66s', '2m59.56s', '250ms') into seconds."""
    value = (value or "").strip()
    if not _DURATION_RE.fullmatch(value):
        return None
    return sum(float(n) * _DURATION_SCALE[unit] for n, unit in _DURATION_PART_RE.findall(value))


def _retry_after_seconds(headers: httpx.Headers) -> int | None:
    """Whole seconds to wait after a 429, or None if the provider didn't say."""
    try:
        seconds = float(headers.get("retry-after", ""))
    except ValueError:
        seconds = None
    if seconds is None:
        # Groq counts requests per day and tokens per minute. Unless the daily
        # requests are exhausted, the token window is what we are waiting on.
        if headers.get("x-ratelimit-remaining-requests") == "0":
            seconds = _parse_duration(headers.get("x-ratelimit-reset-requests"))
        else:
            seconds = _parse_duration(headers.get("x-ratelimit-reset-tokens"))
    if seconds is None or not math.isfinite(seconds) or seconds < 0:
        return None
    return max(1, math.ceil(seconds))


def build_query_rewrite_prompt(audience: str | None = None) -> str:
    """System prompt for the search-query rewrite, scoped to who is asking.

    The office names matter more than they look: they are the strongest signal
    in the rewritten query, and the right office differs entirely by audience.
    Sending an alum to the Registrar for a transcript is right; sending them to
    Advising for course planning is not, because they are not enrolled.
    """
    offices = audiences.offices(audience)
    office_hint = (
        f"Use SJSU office names where they help ({'; '.join(offices)}). "
        if offices
        else ""
    )
    return (
        "You rewrite questions into a single concise web search query (5-15 words) "
        "that finds authoritative pages on sjsu.edu and related SJSU resources. "
        "Rules: add 'SJSU' if missing; expand acronyms in context "
        "(CPT -> curricular practical training, OPT -> optional practical training, "
        "F-1, I-20, FAFSA, GE, etc.). "
        + office_hint
        + "Do NOT answer the question. Output ONLY the search query, no quotes, no preamble."
    )


def build_base_prompt(audience: str | None = None) -> str:
    """The assistant's identity, with the audience's clause appended.

    This is the **stable prefix** of the system prompt (policy_compiler puts
    `base_identity` first, for prefix caching). Making it audience-dependent
    means four prefixes rather than one, so cache hits fragment by audience.
    That is expected and cheap: Phase 3's own note on prefix caching says
    Groq's is automatic and not contractual, so nothing was budgeted against
    it.

    The core is neutral. It used to say "for San Jose State University
    students", which quietly made every answer assume enrolment -- the thing
    this phase exists to stop.
    """
    core = (
        "You are SJSU Copilot, a helpful AI assistant for the San Jose State University community. "
        "You help with questions about academics, campus life, degree requirements, registration, "
        "internships, and more. "
        "If you don't know something specific to SJSU, say so honestly rather than making things up."
    )
    clause = audiences.prompt_context(audience)
    return f"{core} {clause}".strip() if clause else core


# Kept as a module-level constant for callers that have no audience to pass
# (tests, scripts). Equivalent to build_base_prompt(None), which is the
# default audience.
BASE_SYSTEM_PROMPT = build_base_prompt()


def _get_api_key() -> str:
    key = os.getenv("GROQ_API_KEY")
    if not key:
        raise ValueError("GROQ_API_KEY is not set")
    return key


def _resolve_params(behavior: dict | None) -> dict:
    """Map behavior settings to LLM generation parameters."""
    temperature = 0.7
    max_tokens = 2048

    if not behavior:
        return {"temperature": temperature, "max_tokens": max_tokens}

    length = behavior.get("response_length")
    if length == "concise":
        max_tokens = 512
        temperature = 0.5
    elif length == "detailed":
        max_tokens = 4096

    stack = behavior.get("priority_stack") or []

    creativity_idx = stack.index("creativity") if "creativity" in stack else -1
    if 0 <= creativity_idx < 3:
        temperature += 0.15

    accuracy_idx = stack.index("accuracy") if "accuracy" in stack else -1
    if 0 <= accuracy_idx < 2:
        temperature -= 0.1

    speed_idx = stack.index("speed") if "speed" in stack else -1
    if 0 <= speed_idx < 2:
        max_tokens = int(max_tokens * 0.6)

    return {
        "temperature": max(0.3, min(1.0, temperature)),
        "max_tokens": max_tokens,
    }


async def _repair_response(repair_prompt: str, model_id: str) -> str | None:
    """Non-streaming repair call for Level 1 validator rewrites."""
    api_key = _get_api_key()
    async with _provider_client() as client:
        res = await client.post(
            GROQ_API_URL,
            timeout=30,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            json={
                "model": model_id,
                "messages": [
                    {"role": "system", "content": "You are a response editor. Follow the instruction exactly and return only the rewritten text."},
                    {"role": "user", "content": repair_prompt},
                ],
                "stream": False,
                "temperature": 0.3,
                **_completion_params(model_id, 512),
            },
        )
        if res.status_code != 200:
            return None
        data = res.json()
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        return normalize_citations(content) or None


async def stream_chat(
    messages: list[dict],
    model: str = "8b",
    behavior: dict | None = None,
    memory_prompt: str | None = None,
    rag_prompt: str | None = None,
    sources: list[dict] | None = None,
    request_id: str | None = None,
    audience: str | None = None,
) -> AsyncGenerator[str, None]:
    """
    Stream chat completion from Groq. Yields SSE-formatted lines:
      data: {"token": "..."}       — for each text chunk
      data: {"done": true, ...}    — final message with validator metadata

    Uses Groq for completion with behavior policy, optional memory, and optional RAG context.
    """
    api_key = _get_api_key()
    model_id = resolve_model(model)
    params = _resolve_params(behavior)

    # Fit the whole request -- policy, memory, retrieved context, history --
    # inside one token ceiling before sending it.
    policy_prompt = compile_policy(build_base_prompt(audience), behavior)["prompt"]
    fitted = fit_prompt(
        policy_prompt=policy_prompt,
        memory_prompt=memory_prompt,
        rag_prompt=rag_prompt,
        messages=messages,
        limits=limits_from_env(),
    )
    if fitted.report.trimmed:
        logger.info(
            "request trimmed to token budget",
            extra={"request_id": request_id, **fitted.report.as_log_fields()},
        )

    max_tokens = params.pop("max_tokens")
    body = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": fitted.system_prompt},
            *fitted.messages,
        ],
        "stream": True,
        **params,
        **_completion_params(model_id, max_tokens),
    }

    observability.record("model_id", model_id)
    observability.record("prompt_chars", sum(len(m["content"]) for m in body["messages"]))
    observability.record("prompt_trimmed", 1 if fitted.report.trimmed else 0)

    full_response = ""

    async with _provider_client() as client:
        observability.mark("llm.request_sent")
        async with _provider_stream(
            client,
            timeout=120,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            json=body,
        ) as res:
            observability.mark("llm.headers")
            observability.record("http_status", res.status_code)
            if res.status_code != 200:
                error_body = (await res.aread()).decode(errors="replace")
                if res.status_code == 429:
                    retry_after = _retry_after_seconds(res.headers)
                    logger.warning(
                        "Groq rate limited: %s", error_body,
                        extra={"request_id": request_id, "retry_after": retry_after},
                    )
                    error = {"code": "rate_limited", "retry_after": retry_after}
                else:
                    logger.error(
                        "Groq API error %s: %s", res.status_code, error_body,
                        extra={"request_id": request_id},
                    )
                    error = f"Upstream model error ({res.status_code})"
                yield f"data: {json.dumps({'error': error})}\n\n"
                return

            citations = CitationStream()
            buffer = ""
            async for chunk in res.aiter_text():
                buffer += chunk
                lines = buffer.split("\n")
                buffer = lines.pop()

                for line in lines:
                    trimmed = line.strip()
                    if not trimmed or not trimmed.startswith("data: "):
                        continue
                    data = trimmed[6:]
                    if data == "[DONE]":
                        continue
                    try:
                        parsed = json.loads(data)
                        content = (parsed.get("choices") or [{}])[0].get("delta", {}).get("content")
                        content = citations.feed(content) if content else ""
                        if content:
                            if not full_response:
                                observability.mark("llm.first_token")
                            full_response += content
                            yield f"data: {json.dumps({'token': content})}\n\n"
                    except json.JSONDecodeError:
                        continue

            tail = citations.flush()
            if tail:
                full_response += tail
                yield f"data: {json.dumps({'token': tail})}\n\n"
            observability.mark("llm.last_token")

    # -- Post-generation validation ---
    validator_meta = {
        "validators_run": [],
        "validators_passed": True,
        "repairs_applied": [],
    }
    observability.record("repair_applied", 0)

    if behavior and full_response:
        with observability.stage("validate"):
            result = run_validators(full_response, behavior)
        validator_meta["validators_run"] = [v["rule"] for v in result["violations"]]
        validator_meta["validators_passed"] = result["action"] is None

        if result["action"] == "rewrite" and result["repair_prompt"]:
            try:
                with observability.stage("repair"):
                    repaired = await _repair_response(result["repair_prompt"], model_id)
                if repaired:
                    observability.record("repair_applied", 1)
                    full_response = repaired
                    validator_meta["repairs_applied"] = ["rewrite"]
                    yield f"data: {json.dumps({'replace': repaired})}\n\n"
            except Exception:
                pass
        elif result["action"] == "warn" and result["warning_text"]:
            full_response += result["warning_text"]
            validator_meta["repairs_applied"] = [
                v["rule"] for v in result["violations"] if v.get("severity") == "medium"
            ]
            yield f"data: {json.dumps({'replace': full_response})}\n\n"

    observability.record("output_chars", len(full_response))
    if sources:
        # Per-answer citation count, so citation rate can be compared across
        # models.
        citations_found = len(re.findall(r"\[\d+\]", full_response))
        observability.record("citations", citations_found)
        if full_response and not citations_found:
            logger.warning(
                "RAG sources provided but no [N] citations found in response",
                extra={"request_id": request_id},
            )

    # Final done event
    done_payload = {"done": True, "full_response": full_response, **validator_meta}
    if sources:
        done_payload["sources"] = sources
    if request_id:
        done_payload["request_id"] = request_id
    yield f"data: {json.dumps(done_payload)}\n\n"


async def rewrite_query_for_sjsu(question: str, audience: str | None = None) -> str:
    """Rewrite a user question into an SJSU-anchored web search query.

    Falls back to the original question on any failure (no key, timeout, parse error,
    empty response). The caller should always use the returned string as the search query.
    """
    original = (question or "").strip()
    if not original:
        return original
    try:
        api_key = _get_api_key()
    except ValueError:
        return original
    try:
        async with _provider_client() as client:
            res = await client.post(
                GROQ_API_URL,
                timeout=5,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                json={
                    "model": MODEL_REWRITE,
                    "messages": [
                        {"role": "system", "content": build_query_rewrite_prompt(audience)},
                        {"role": "user", "content": original},
                    ],
                    "stream": False,
                    "temperature": 0.2,
                    **_completion_params(MODEL_REWRITE, 60),
                },
            )
            if res.status_code != 200:
                logger.warning("query rewrite returned %s; falling back", res.status_code)
                return original
            data = res.json()
            rewritten = (
                (data.get("choices") or [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
                .strip('"')
                .strip("'")
            )
            if not rewritten or len(rewritten) > 300:
                return original
            return rewritten
    except Exception:
        logger.exception("query rewrite failed; falling back to original")
        return original


async def generate_title(user_message: str) -> str | None:
    """Generate a short chat title from the first user message."""
    api_key = _get_api_key()
    async with _provider_client() as client:
        res = await client.post(
            GROQ_API_URL,
            timeout=15,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            json={
                "model": MODEL_TITLE,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Generate a short chat title (3-6 words, no quotes, no punctuation at the end) "
                            "that summarizes the user's message. Reply with ONLY the title, nothing else."
                        ),
                    },
                    {"role": "user", "content": user_message},
                ],
                "stream": False,
                "temperature": 0.4,
                **_completion_params(MODEL_TITLE, 30),
            },
        )
        if res.status_code != 200:
            return None
        data = res.json()
        title = (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        return title or None
