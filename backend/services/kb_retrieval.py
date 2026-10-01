"""Reading the knowledge base, on the chat critical path.

Everything here is shaped by one rule: **the live search path must be strictly
better off for this existing.** So `search_kb` never raises, never blocks past
its own timeout, and a knowledge base that is empty, slow, broken or
misconfigured produces exactly the behaviour the app had before it was written.
A KB outage must not be a chat outage.

**Why the backend reads with the service key.** `match_kb_hybrid` and
`search_kb_chunks` are granted to `service_role` alone -- 20260918000100 revoked
the rest, and 20260930000100 does not grant them back. That means the database
cannot see who is asking, so visibility is passed in as a parameter the backend
controls: `p_include_authenticated`, set from `principal.kind == "user"` and
defaulting to false. The RLS policies on `documents` enforce the same rule
independently for anything reading through PostgREST as a user, but they are not
what protects a guest here, because service_role bypasses RLS. Both exist; §22 of
verify_policies.sql tests both.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from datetime import date

import httpx

import observability
import runtime
from kb.embed import embed_query, to_pgvector

logger = logging.getLogger(__name__)

# **On by default since 2026-10-01**, after stage 4D measured that it helps: 9 of
# the 10 KB-eligible bench questions retrieve the page that answers them, p50
# 422ms against 2-6s for live search, assembled context 3.3-5.7k chars against
# ~8.8k, and the answers were read through and judged good.
#
# This flag is still the entire rollback, and it is still read per call, so
# `KB_RETRIEVAL_ENABLED=false` in the environment restores the pre-Phase-4
# behaviour exactly, with no deploy and no restart beyond picking up the env.
#
# One known failure survives the flip: a question about good academic standing
# gets a confident answer sourced from /admissions/impaction/, which is about
# admission GPA thresholds rather than the 2.0 continuation requirement. The fix
# is the catalog at crawl_depth 1, not a threshold.
def enabled() -> bool:
    return os.getenv("KB_RETRIEVAL_ENABLED", "true").strip().lower() in ("1", "true", "yes")


def _num(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("%s is not a number; using %s", name, default)
        return default


# Covers an embedding round trip plus the query. A local model would make this
# ~50ms; a hosted embedding provider is the cost of choosing zero dependencies.
def timeout_s() -> float:
    return _num("KB_TIMEOUT", 1.5)


# **6, not the 12 that shipped with 4C.** Measured against the live corpus on
# 2026-10-01 over the ten KB-eligible bench questions:
#
#   k=12  sufficient  9/10   context median 7,646 chars, max 9,996
#   k=6   sufficient 10/10   context median 4,351 chars, max 5,182
#   k=4   sufficient 10/10   context median 3,018 chars, max 3,498
#
# At 12 the context was hitting MAX_TOTAL_CHARS (10,000) and being truncated, so
# the knowledge base was spending the entire prompt budget -- about what live
# search costs, which quietly cancelled the token-ceiling argument for building it
# at all. Halving it costs nothing measurable: sufficiency went *up*, because the
# one miss at k=12 was a timeout rather than a ranking failure.
#
# 6 rather than 4 leaves recall headroom for questions outside the bench set;
# 4 also scored 10/10, so there is room to go lower if prompt size ever binds.
def max_chunks() -> int:
    return int(_num("KB_MAX_CHUNKS", 6))


# **Calibrated 2026-10-01 against the live 35-document corpus**, replacing the
# placeholders of 0.01 / 0.03 that shipped with 4C.
#
# RRF scores are not similarity scores -- they are sums of 1/(60 + rank_position),
# so what they encode is *agreement between the two arms*, and the measured
# distribution is close to bimodal:
#
#   ~0.0164  = 1/61. The item is rank 1 in ONE arm and absent from the other.
#              Because vector search always returns its k nearest neighbours, this
#              is what a question with no real answer in the corpus looks like.
#              Measured for "thanks!" and for "quantum chromodynamics lattice
#              gauge theory".
#   ~0.032   = 2/61. Both the keyword and the vector arm put the item at or near
#              rank 1, i.e. they agree. Measured for "where can visitors park"
#              (0.0323) and "How do I order an official transcript..." (0.0328).
#
# So KB_MIN_RANK sits between the two modes, and clearing it means "both arms
# found this". 0.03 for KB_STRONG_RANK is then "both arms put it first".
def min_rank() -> float:
    return _num("KB_MIN_RANK", 0.02)


def strong_rank() -> float:
    return _num("KB_STRONG_RANK", 0.03)


# Words websearch_to_tsquery treats as operators rather than as terms.
_TSQUERY_OPERATORS = {"or", "and", "not"}
_WORD_RE = re.compile(r"[A-Za-z0-9']+")


def fts_query(question: str) -> str:
    """Turn a natural-language question into an OR-joined websearch query.

    **`websearch_to_tsquery` ANDs every term**, which makes the keyword arm of the
    fusion useless for anything phrased as a question: measured against the live
    corpus on 2026-10-01, "How do I order an official transcript from SJSU as an
    alumnus?" matched **zero** chunks, because no single chunk contains every one
    of those words. So did every other bench question. Hybrid retrieval was
    silently vector-only, and the symptom was that almost every question scored
    exactly 1/(60+1) = 0.0164 -- the RRF score for ranking in one arm and not the
    other.

    Joining the terms with `or` fixes it without a migration, because
    websearch_to_tsquery understands the `or` keyword. `ts_rank_cd` then does the
    discriminating it was always meant to do: it weights how many terms matched
    and how close together they are, so a chunk matching four of five terms
    outranks one matching one. Measured after the change: the transcript question
    returns /registrar/transcripts/ at rank 1 (0.80), "faculty submit final
    grades" returns /registrar/faculty-staff/grading/ (1.80), and
    "quantum chromodynamics lattice gauge theory" returns **nothing** -- which is
    what makes the sufficiency gate able to tell a real hit from a near neighbour.

    Tokenising to words also sanitises the string. websearch_to_tsquery has its
    own operators -- a double quote starts a phrase, a leading `-` negates -- so
    passing a raw question through can silently invert its meaning: "add-drop
    deadline" would become `add & !drop & deadline`. Only word characters survive
    here, and the operator keywords are dropped so a question containing the word
    "or" cannot restructure the query.
    """
    seen: list[str] = []
    for word in _WORD_RE.findall(question.lower()):
        if word in _TSQUERY_OPERATORS or word in seen:
            continue
        seen.append(word)
    return " or ".join(seen)


@dataclass(frozen=True)
class KbChunk:
    document_id: str
    chunk_index: int
    heading: str | None
    content: str
    title: str
    url: str
    collection: str | None
    rank: float
    fetched_at: str | None
    last_verified_at: str | None
    valid_until: str | None

    @classmethod
    def from_row(cls, row: dict) -> "KbChunk":
        return cls(
            document_id=row.get("document_id") or "",
            chunk_index=int(row.get("chunk_index") or 0),
            heading=row.get("heading"),
            content=row.get("content") or "",
            title=row.get("title") or "",
            url=row.get("url") or "",
            collection=row.get("collection"),
            rank=float(row.get("rank") or 0.0),
            fetched_at=row.get("fetched_at"),
            last_verified_at=row.get("last_verified_at"),
            valid_until=row.get("valid_until"),
        )


def _headers() -> dict[str, str] | None:
    key = os.getenv("SUPABASE_SERVICE_KEY", "").strip()
    if not key:
        return None
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def _rpc(body: dict, *, timeout: float) -> list[dict]:
    url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    headers = _headers()
    if not url or not headers:
        raise RuntimeError("SUPABASE_URL or SUPABASE_SERVICE_KEY is not set")

    endpoint = f"{url}/rest/v1/rpc/match_kb_hybrid"
    shared = runtime.get_supabase_client()
    if shared is not None:
        # The shared client's read timeout is 3s, which is too long for this
        # path; override per request rather than adding a fourth lifespan client.
        res = await shared.post(endpoint, json=body, headers=headers, timeout=timeout)
    else:
        # No lifespan: the eval CLI and the tests.
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            res = await client.post(endpoint, json=body, headers=headers)

    if res.status_code >= 400:
        raise RuntimeError(f"kb rpc failed: {res.status_code} {res.text[:160]}")
    rows = res.json()
    if not isinstance(rows, list):
        raise RuntimeError("kb rpc returned an unexpected shape")
    return rows


async def _search(
    question: str, audience: str | None, *, include_authenticated: bool, k: int
) -> list[KbChunk]:
    # The embedding is optional. Without it the vector arm of the fusion is empty
    # and match_kb_hybrid degrades to keyword-only, which is a worse search but
    # still a search -- better than no KB at all when the embedding API is down.
    embedding = None
    shared = runtime.get_supabase_client()
    if shared is not None:
        embedding = await embed_query(shared, question)
    else:
        async with httpx.AsyncClient() as client:
            embedding = await embed_query(client, question)
    observability.record("kb_embedded", 0 if embedding else 1)

    rows = await _rpc(
        {
            # OR-joined, not the raw question -- see fts_query.
            "query_text": fts_query(question),
            "query_embedding": to_pgvector(embedding),
            "p_audience": audience,
            "p_include_authenticated": include_authenticated,
            "match_count": k,
        },
        timeout=timeout_s(),
    )
    return [KbChunk.from_row(r) for r in rows]


async def search_kb(
    question: str,
    audience: str | None,
    *,
    include_authenticated: bool,
    k: int | None = None,
) -> list[KbChunk]:
    """Hybrid keyword + vector search. Returns [] rather than raising, ever.

    Wrapped in `wait_for` as well as try/except because the two failure modes are
    different: an exception is a broken query, a timeout is a slow database, and
    on this path both have to become "fall through to live search" inside a
    bounded number of milliseconds.
    """
    if not question.strip():
        return []
    try:
        return await asyncio.wait_for(
            _search(
                question,
                audience,
                include_authenticated=include_authenticated,
                k=k or max_chunks(),
            ),
            timeout=timeout_s(),
        )
    except asyncio.TimeoutError:
        observability.incr("kb_error")
        logger.warning("kb lookup timed out", extra={"timeout_s": timeout_s()})
        return []
    except Exception as exc:
        observability.incr("kb_error")
        logger.warning("kb lookup failed", extra={"error": type(exc).__name__})
        return []


# ── Turning chunks into citable sources ───────────────────────────────────────


def _stale(chunk: KbChunk) -> bool:
    """True when this document should not be used to answer at all.

    `valid_until` is the explicit expiry a page carries -- a deadline page for a
    term that has ended. Independent of the question classifier in
    services/freshness.py: that one asks whether the *question* expires, this
    asks whether the *document* already has.
    """
    if not chunk.valid_until:
        return False
    try:
        return date.fromisoformat(chunk.valid_until[:10]) < date.today()
    except ValueError:
        return False


def kb_items(chunks: list[KbChunk]) -> list[dict]:
    """Group chunks into one citable item per document, in rank order.

    One item per document, not per chunk, because `[N]` has to resolve to
    something a person can open. Three citations pointing at the same page is
    noise, and the marker is a 1-based index into the source list, so the mapping
    must be one-to-one with a URL.

    Within a document the chunks are concatenated in `chunk_index` order, not
    rank order: prose has to read forwards. A heading is emitted when it differs
    from the previous chunk's, which keeps a merged section's structure visible
    without repeating a heading on every chunk.
    """
    by_doc: dict[str, list[KbChunk]] = {}
    for chunk in chunks:
        if _stale(chunk):
            continue
        by_doc.setdefault(chunk.document_id, []).append(chunk)

    ranked = sorted(
        by_doc.values(),
        key=lambda group: (
            -max(c.rank for c in group),
            # Tie-break on freshness, matching search_kb_chunks' own ordering.
            -(len(group[0].last_verified_at or "")),
        ),
    )

    items: list[dict] = []
    for group in ranked:
        ordered = sorted(group, key=lambda c: c.chunk_index)
        parts: list[str] = []
        previous_heading = None
        for chunk in ordered:
            if chunk.heading and chunk.heading != previous_heading:
                parts.append(f"{chunk.heading}\n{chunk.content}")
            else:
                parts.append(chunk.content)
            previous_heading = chunk.heading
        head = ordered[0]
        items.append(
            {
                "title": head.title or head.url,
                "url": head.url,
                "content": "\n\n".join(parts),
                # Rendered into the citation header, so an answer says how old
                # its source is instead of implying it is current.
                "verified_on": (head.last_verified_at or head.fetched_at or "")[:10] or None,
            }
        )
    return items


def sufficient(chunks: list[KbChunk]) -> bool:
    """Is this good enough to answer from, instead of searching the web?

    Deliberately conservative. Falling through costs a few seconds; answering
    from a weak hit costs the user a wrong answer with a citation on it.
    """
    fresh = [c for c in chunks if not _stale(c)]
    if not fresh:
        return False

    top = max(c.rank for c in fresh)
    if top < min_rank():
        return False

    # **Count documents only among chunks that clear min_rank.** Counting every
    # returned chunk was the original bug and it made this gate inert: the vector
    # arm always returns its k nearest neighbours, so `len(documents) >= 2` was
    # satisfied by literally any query. Measured before the fix,
    # "quantum chromodynamics lattice gauge theory" came back as 12 chunks across
    # 11 documents and was judged sufficient.
    #
    # Above the threshold the count means what it was meant to mean: one document
    # agreeing with the question is as often a coincidence as an answer, two is a
    # signal, and a single document both arms rank first is allowed through on its
    # own -- which is what KB_STRONG_RANK is for.
    usable = [c for c in fresh if c.rank >= min_rank()]
    documents = {c.document_id for c in usable}
    return len(documents) >= 2 or top >= strong_rank()
