"""Token budgeting for the chat request.

The pipeline previously had no notion of a total request size. `web_search`
capped retrieved context at a fixed character count and everything else --
system policy, memory, conversation history, completion budget -- was simply
added on top. On Groq's free plan the token allowance (8,000 tokens/minute) is
shared by the whole organisation, not per user, so a single "detailed" request
carrying a full context window could consume the entire per-minute budget.

This module enforces one ceiling across every part of the prompt, and trims in a
fixed priority order when the ceiling is exceeded:

    1. retrieved context  -- largest, and the answer degrades gracefully
    2. conversation history -- oldest turns first; the latest turn is never dropped
    3. memory context     -- smallest, and dropping it is the most user-visible

Counting is approximate by design. The models actually in use (Llama 3.3 via
Groq, and gpt-oss if enabled) do not use tiktoken's encodings, so an exact count
is not available without pulling in each model's tokenizer. `tiktoken` is used
when it is installed and its vocabulary is already cached locally; otherwise a
deliberately conservative character ratio is used, which over-estimates token
counts and therefore trims earlier rather than later.
"""

import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# English prose runs about 4 characters per token. A smaller divisor
# over-estimates the count, which is the safe direction for a budget.
CHARS_PER_TOKEN = 3.5

# Per-message overhead of the chat format (role, delimiters).
TOKENS_PER_MESSAGE = 4

# Retrieved context below this size is not worth keeping: the citations would be
# incomplete and the model does better with no context than a fragment of one.
MIN_USEFUL_RAG_TOKENS = 200

_encoder = None
_encoder_loaded = False


def _get_encoder():
    """Return a tiktoken encoder, or None. Never raises, never hits the network.

    tiktoken downloads its vocabulary on first use. That would put a network
    fetch on the request path, so we only use an encoding that is already cached
    on disk.
    """
    global _encoder, _encoder_loaded
    if _encoder_loaded:
        return _encoder

    _encoder_loaded = True
    try:
        import tiktoken  # noqa: PLC0415

        os.environ.setdefault("TIKTOKEN_CACHE_DIR", os.path.expanduser("~/.cache/tiktoken"))
        _encoder = tiktoken.get_encoding("o200k_base")
        logger.info("token_budget using tiktoken o200k_base")
    except Exception:
        _encoder = None
        logger.info("token_budget using character estimate (tiktoken unavailable)")

    return _encoder


def count_tokens(text: str | None) -> int:
    """Approximate token count for a string."""
    if not text:
        return 0

    encoder = _get_encoder()
    if encoder is not None:
        try:
            return len(encoder.encode(text))
        except Exception:
            pass

    return int(len(text) / CHARS_PER_TOKEN) + 1


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Cut text down to roughly max_tokens, at a word boundary."""
    if max_tokens <= 0:
        return ""
    if count_tokens(text) <= max_tokens:
        return text

    # Binary search on characters rather than trusting the ratio, so this is
    # correct whichever counter is in use.
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if count_tokens(text[:mid]) <= max_tokens:
            low = mid
        else:
            high = mid - 1

    cut = text[:low].rsplit(" ", 1)[0]
    return (cut or text[:low]) + "..."


@dataclass(frozen=True)
class BudgetLimits:
    max_prompt_tokens: int
    max_completion_tokens: int


@dataclass
class BudgetReport:
    prompt_tokens: int = 0
    rag_tokens_dropped: int = 0
    rag_dropped_entirely: bool = False
    messages_dropped: int = 0
    memory_dropped: bool = False

    def as_log_fields(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "rag_tokens_dropped": self.rag_tokens_dropped,
            "rag_dropped_entirely": self.rag_dropped_entirely,
            "messages_dropped": self.messages_dropped,
            "memory_dropped": self.memory_dropped,
        }

    @property
    def trimmed(self) -> bool:
        return bool(
            self.rag_tokens_dropped
            or self.messages_dropped
            or self.memory_dropped
            or self.rag_dropped_entirely
        )


@dataclass
class FittedPrompt:
    system_prompt: str
    messages: list[dict] = field(default_factory=list)
    report: BudgetReport = field(default_factory=BudgetReport)


def limits_from_env() -> BudgetLimits:
    return BudgetLimits(
        max_prompt_tokens=int(os.getenv("MAX_PROMPT_TOKENS", "4000")),
        max_completion_tokens=int(os.getenv("MAX_COMPLETION_TOKENS", "1500")),
    )


def _assemble(policy: str, memory: str | None, rag: str | None) -> str:
    """Compose the system prompt, most stable content first.

    Ordering matters beyond tidiness: providers cache on a prompt prefix, and
    the previous order put per-turn memory ahead of the static policy, so the
    prefix changed on every single turn and could never be reused.
    """
    parts = [policy]
    if memory:
        parts.append(memory)
    if rag:
        parts.append(rag)
    return "\n\n".join(p for p in parts if p)


def fit_prompt(
    policy_prompt: str,
    memory_prompt: str | None,
    rag_prompt: str | None,
    messages: list[dict],
    limits: BudgetLimits | None = None,
) -> FittedPrompt:
    """Trim the request to fit max_prompt_tokens. Never drops the latest turn."""
    limits = limits or limits_from_env()
    report = BudgetReport()

    memory = memory_prompt or None
    rag = rag_prompt or None
    kept = list(messages)

    def total(memory_, rag_, msgs) -> int:
        system = _assemble(policy_prompt, memory_, rag_)
        running = count_tokens(system) + TOKENS_PER_MESSAGE
        for m in msgs:
            running += count_tokens(m.get("content")) + TOKENS_PER_MESSAGE
        return running

    budget = limits.max_prompt_tokens

    # 1. Retrieved context.
    if rag and total(memory, rag, kept) > budget:
        original = count_tokens(rag)
        overflow = total(memory, rag, kept) - budget
        allowed = original - overflow
        if allowed < MIN_USEFUL_RAG_TOKENS:
            report.rag_tokens_dropped = original
            report.rag_dropped_entirely = True
            rag = None
        else:
            rag = _truncate_to_tokens(rag, allowed)
            report.rag_tokens_dropped = original - count_tokens(rag)

    # 2. Conversation history, oldest first. The final message is the question
    #    being asked, so it always survives even if it alone exceeds the budget.
    while len(kept) > 1 and total(memory, rag, kept) > budget:
        kept.pop(0)
        report.messages_dropped += 1

    # 3. Memory context.
    if memory and total(memory, rag, kept) > budget:
        memory = None
        report.memory_dropped = True

    report.prompt_tokens = total(memory, rag, kept)

    if report.trimmed:
        logger.info("prompt trimmed to fit token budget", extra=report.as_log_fields())

    return FittedPrompt(
        system_prompt=_assemble(policy_prompt, memory, rag),
        messages=kept,
        report=report,
    )


def clamp_completion_tokens(requested: int, limits: BudgetLimits | None = None) -> int:
    """Cap the completion budget.

    For reasoning models the reasoning tokens are billed and counted against
    this same allowance even though they are never shown, so the ceiling has to
    cover both.
    """
    limits = limits or limits_from_env()
    return max(1, min(requested, limits.max_completion_tokens))
