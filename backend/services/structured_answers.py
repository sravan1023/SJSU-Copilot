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
"""

import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal

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


async def answer(
    messages: list[dict], audience: str | None, principal_kind: str = "guest"
) -> Answer | None:
    if not ROUTERS:
        return None
    q = build_input(messages, audience, principal_kind)
    for route in list(ROUTERS):
        try:
            result = await route(q)
        except Exception:
            # One broken router must not hide the ones after it, nor fail the
            # turn. Log the type only: messages can echo user text.
            logger.warning(
                "structured router failed",
                extra={"router": getattr(route, "__name__", "?")},
                exc_info=False,
            )
            continue
        if result is not None:
            return result
    return None
