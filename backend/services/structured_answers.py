"""Seam for answers that come from structured data rather than from retrieval.

Routers registered in ROUTERS get the first look at a chat turn, before the
knowledge base or live search runs. A router either abstains (None) or returns
an Answer in one of three modes:

    card     the markdown is the whole answer. No retrieval, no provider call.
    context  the router supplies the grounding block and sources; retrieval is
             skipped but the model still writes the answer.
    prefix   the markdown is emitted first, then the normal pipeline runs
             unchanged. Used where the trigger deliberately over-matches (the
             crisis card), so a false positive costs a paragraph of text and
             never blocks a legitimate answer.

ROUTERS is empty until a router is added; with it empty, `answer` returns None
and the chat stream behaves exactly as before.

Contracts the seam enforces, so one bad router cannot fail or corrupt a turn:

- A result must be an Answer with a known mode and the payload its mode needs
  (card and prefix: non-empty markdown; context: non-empty rag_prompt). Anything
  else is dropped as if the router had abstained, and `structured_fallthrough`
  is recorded as "invalid".
- Only `prefix` may act on a turn the RAG gates skipped (RouterInput.query is
  None): a prefix adds text and still lets the model answer, whereas a card or
  context result on "make that shorter" would replace the answer to a follow-up
  with unrelated data. Those results are dropped, recorded as "gated".
- Routers get their own deep copy of the messages, so none can change what
  retrieval and the provider later receive.
- prefix-mode `sources` are appended to the done frame's sources, after the
  retrieval ones, so the model's [N] citation numbers keep pointing at the
  sources they were written against. Duplicate URLs are dropped.
"""

import copy
import dataclasses
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal

import observability
from services.web_search import prepare_rag_query

logger = logging.getLogger(__name__)

Mode = Literal["card", "context", "prefix"]
MODES = ("card", "context", "prefix")


@dataclass(frozen=True)
class RouterInput:
    # The text prepare_rag_query would search on, or None when its conversational
    # and meta gates say this turn needs no retrieval ("thanks", "make it shorter").
    # Data routers should classify this, so those gates still apply to them.
    query: str | None
    # The last user message, ungated. The crisis router needs this: a gate that
    # hides "thanks, I want to die" from retrieval must not hide it from the card.
    raw_text: str
    audience: str | None
    principal_kind: str
    messages: list[dict] = field(default_factory=list)


@dataclass
class Answer:
    mode: Mode
    markdown: str = ""
    # Structured payload for the done frame (the client ignores unknown keys).
    card: dict | None = None
    # context mode only: the grounding block handed to the generation path.
    rag_prompt: str | None = None
    sources: list[dict] = field(default_factory=list)
    # Short label for observability; never user text.
    route: str = ""


Router = Callable[[RouterInput], Awaitable[Answer | None]]

# Ordered; the first non-None answer wins.
ROUTERS: list[Router] = []


def build_input(
    messages: list[dict], audience: str | None, principal_kind: str
) -> RouterInput:
    raw = next(
        (
            (m.get("content") or "").strip()
            for m in reversed(messages)
            if m.get("role") == "user"
        ),
        "",
    )
    return RouterInput(
        query=prepare_rag_query(messages),
        raw_text=raw,
        audience=audience,
        principal_kind=principal_kind,
        messages=messages,
    )


def invalid_reason(result, q: RouterInput) -> str | None:
    """Why a router result can't be used, or None when it can. Labels, never text."""
    if not isinstance(result, Answer) or result.mode not in MODES:
        return "invalid"
    if result.mode in ("card", "prefix"):
        if not isinstance(result.markdown, str) or not result.markdown.strip():
            return "invalid"
    if result.mode == "context":
        if not isinstance(result.rag_prompt, str) or not result.rag_prompt.strip():
            return "invalid"
    if not isinstance(result.sources, list):
        return "invalid"
    if result.card is not None and not isinstance(result.card, dict):
        return "invalid"
    if q.query is None and result.mode != "prefix":
        return "gated"
    return None


async def answer(
    messages: list[dict], audience: str | None, principal_kind: str = "guest"
) -> Answer | None:
    if not ROUTERS:
        return None
    q = build_input(messages, audience, principal_kind)
    for route in list(ROUTERS):
        name = getattr(route, "__name__", "?")
        try:
            # A fresh copy per router: one router's edits must not reach the next.
            result = await route(
                dataclasses.replace(q, messages=copy.deepcopy(q.messages))
            )
        except Exception:
            # One broken router must not hide the ones after it, nor fail the
            # turn. Log the type only: messages can echo user text.
            logger.warning(
                "structured router failed",
                extra={"router": name},
                exc_info=False,
            )
            continue
        if result is None:
            continue
        reason = invalid_reason(result, q)
        if reason is not None:
            observability.record("structured_fallthrough", reason)
            logger.warning(
                "structured router result rejected",
                extra={"router": name, "reason": reason},
            )
            continue
        return result
    return None
