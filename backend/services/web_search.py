import asyncio
import hashlib
import ipaddress
import logging
import os
import re
import socket
import time
from contextlib import asynccontextmanager
from urllib.parse import urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup
from ddgs import DDGS

import observability
import runtime
from services.llm import rewrite_query_for_sjsu

logger = logging.getLogger(__name__)


def _digest(text: str) -> str:
    """Short, stable fingerprint of user-derived text, for logs.

    Logs carry lengths and this hash, never the text itself: the same query can
    be spotted across requests without the question being written anywhere.
    """
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:12]

PREFERRED_DOMAINS = [
    "sjsu.edu",
    "catalog.sjsu.edu",
    "library.sjsu.edu",
    "blogs.sjsu.edu",
    "one.sjsu.edu",
]

BLOCKED_DOMAINS = [
    "reddit.com",
    "quora.com",
    "pinterest.com",
    "medium.com",
    "fandom.com",
    "wikihow.com",
    "blogspot.com",
]

OTHER_UNIVERSITY_DOMAINS = [
    "santaclara.edu",
    "stanford.edu",
    "berkeley.edu",
    "calstate.edu",
    "sfsu.edu",
    "csulb.edu",
    "csufresno.edu",
    "csun.edu",
    "fullerton.edu",
    "sdsu.edu",
    "ucla.edu",
    "ucsd.edu",
    "ucdavis.edu",
    "ucsc.edu",
    "ucr.edu",
    "ucmerced.edu",
    "ucsb.edu",
    "ucsf.edu",
    "uci.edu",
    "scu.edu",
]

MAX_RESULTS = 20
MAX_SOURCES = 5
MAX_CHARS_PER_PAGE = 6000
# ~2,800 tokens of context. Was 24,000 chars (~6,800 tokens), which on Groq's
# free plan could consume most of the 8,000 tokens/minute the whole
# organisation shares. services/token_budget.py enforces the overall ceiling;
# this keeps retrieval from producing work that would only be trimmed away.
MAX_TOTAL_CHARS = 10000
MAX_BYTES_PER_PAGE = 2_000_000
MAX_REDIRECTS = 3
REQUEST_TIMEOUT = 10.0

# Total wall-clock budget for retrieval: rewrite + search + crawl. Previously
# each step had its own timeout and the pipeline had none, so a slow search
# followed by five slow pages could stack up well past half a minute with the
# user seeing nothing. Whatever has finished when the budget runs out is used,
# and the rest is abandoned -- sources that were never crawled fall back to
# their search snippet in build_rag_prompt.
RETRIEVAL_DEADLINE = float(os.getenv("RETRIEVAL_DEADLINE", "6.0"))
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"


def _hostname(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _host_matches(hostname: str, domain: str) -> bool:
    if not hostname:
        return False
    domain = domain.lower()
    return hostname == domain or hostname.endswith("." + domain)


def _canonicalize_url(url: str) -> str:
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urlunparse((scheme, netloc, path, parsed.params, parsed.query, ""))


def search_web(query: str) -> list[dict]:
    results = []
    if not query or not query.strip():
        return results
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=MAX_RESULTS):
                url = r.get("href") or r.get("url")
                if not url:
                    continue
                results.append(
                    {
                        "title": r.get("title") or "",
                        "url": url,
                        "snippet": r.get("body") or "",
                    }
                )
    except Exception as exc:
        # Type only, no traceback or message: the query is user text, and the
        # search libraries' errors can embed the request URL, which contains it.
        # "No results found" from one engine is routine; the other search usually
        # still supplies sources.
        observability.incr("search_failed")
        logger.warning(
            "DDGS search failed",
            extra={"error": type(exc).__name__, "query_chars": len(query), "query_hash": _digest(query)},
        )
        return []
    return results


def _is_blocked(url: str) -> bool:
    host = _hostname(url)
    return any(_host_matches(host, bad) for bad in BLOCKED_DOMAINS)


def _is_preferred(url: str) -> bool:
    host = _hostname(url)
    return any(_host_matches(host, dom) for dom in PREFERRED_DOMAINS)


def _is_other_university(url: str) -> bool:
    host = _hostname(url)
    return any(_host_matches(host, dom) for dom in OTHER_UNIVERSITY_DOMAINS)


def _is_edu(url: str) -> bool:
    host = _hostname(url)
    return host.endswith(".edu") or host == "edu"


def _dedup_results(results: list[dict]) -> list[dict]:
    seen: set[str] = set()
    deduped: list[dict] = []
    for r in results:
        url = r.get("url") or ""
        if not url:
            continue
        key = _canonicalize_url(url)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    return deduped


def rank_sources(results: list[dict]) -> list[dict]:
    deduped = _dedup_results(results)
    filtered = [
        r for r in deduped
        if r.get("url")
        and not _is_blocked(r["url"])
        and not _is_other_university(r["url"])
    ]

    def score(item: dict) -> int:
        url = item.get("url") or ""
        if _is_preferred(url):
            return 100
        if _is_edu(url):
            return 5
        return 0

    ranked = sorted(enumerate(filtered), key=lambda pair: (-score(pair[1]), pair[0]))
    return [item for _, item in ranked][:MAX_SOURCES]


def _clean_text(text: str) -> str:
    normalized = re.sub(r"\r\n?", "\n", text)
    normalized = re.sub(r"[ \t\f\v]+", " ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def _extract_main_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "footer", "nav", "aside", "header", "form"]):
        tag.decompose()
    candidates = [
        soup.find("main"),
        soup.find("article"),
        soup.find("div", id="content"),
        soup.find("div", id="main-content"),
        soup.find("div", id="page-content"),
        soup.find("section"),
        soup.body,
    ]
    best_text = ""
    for node in candidates:
        if not node:
            continue
        text = _clean_text(node.get_text(separator="\n", strip=True))
        if len(text) > len(best_text):
            best_text = text
    return best_text


async def _host_is_public(host: str) -> bool:
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
    except socket.gaierror:
        return False
    if not infos:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except (ValueError, IndexError):
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False
    return True


async def _safe_stream_get(client: httpx.AsyncClient, url: str) -> tuple[str, str] | None:
    """Walk redirects manually, validating the host at each hop. Returns (final_url, body_text) or None."""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        parsed = urlparse(current)
        if parsed.scheme not in ("http", "https"):
            observability.incr("crawl_blocked")
            return None
        host = parsed.hostname
        if not host or not await _host_is_public(host):
            observability.incr("crawl_blocked")
            return None
        try:
            async with client.stream("GET", current) as resp:
                if resp.is_redirect:
                    location = resp.headers.get("location")
                    if not location:
                        observability.incr("crawl_error")
                        return None
                    current = str(httpx.URL(current).join(location))
                    continue
                if resp.status_code >= 400:
                    observability.incr("crawl_http_error")
                    return None
                content_type = resp.headers.get("content-type", "")
                if "text/html" not in content_type.lower():
                    observability.incr("crawl_non_html")
                    return None
                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) >= MAX_BYTES_PER_PAGE:
                        buf = buf[:MAX_BYTES_PER_PAGE]
                        break
                encoding = resp.charset_encoding or "utf-8"
                try:
                    text = bytes(buf).decode(encoding, errors="replace")
                except LookupError:
                    text = bytes(buf).decode("utf-8", errors="replace")
                observability.incr("crawl_ok")
                return current, text
        except Exception:
            observability.incr("crawl_error")
            logger.exception("fetch failed for %s", current)
            return None
    observability.incr("crawl_too_many_redirects")
    return None


async def _fetch_page(client: httpx.AsyncClient, url: str, sem: asyncio.Semaphore) -> dict:
    async with sem:
        result = await _safe_stream_get(client, url)
        if not result:
            return {"url": url, "content": ""}
        _final, html = result
        # BeautifulSoup over up to 2 MB of HTML is the one genuinely CPU-bound
        # step here. Run it on the bounded pool so it doesn't stall the event
        # loop for every other in-flight request. Timed separately from the
        # fetch; summed across pages, so it can exceed the crawl's wall time.
        with observability.stage("rag.extract"):
            content = await runtime.run_blocking(_extract_main_text, html)
        if len(content) > MAX_CHARS_PER_PAGE:
            content = content[:MAX_CHARS_PER_PAGE].rsplit(" ", 1)[0] + "..."
        return {"url": url, "content": content}


@asynccontextmanager
async def _crawl_client():
    """Yield the shared crawl client, or a temporary one outside a lifespan."""
    shared = runtime.get_crawl_client()
    if shared is not None:
        yield shared
        return
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(REQUEST_TIMEOUT),
        follow_redirects=False,  # manual redirect walking re-validates each hop
        headers={"User-Agent": USER_AGENT},
    ) as client:
        yield client


async def crawl_sources(urls: list[str], budget: float | None = None) -> list[dict]:
    """Fetch pages concurrently, returning whatever finished within `budget`.

    Pages that time out come back with empty content rather than failing the
    batch, so the caller can fall back to the search snippet.
    """
    if not urls:
        return []

    # Nothing left in the budget: don't open connections we have no intention
    # of waiting for. Callers fall back to search snippets.
    if budget is not None and budget <= 0:
        observability.incr("crawl_skipped", len(urls))
        logger.info("retrieval budget exhausted before crawling; skipping %d pages", len(urls))
        return [{"url": url, "content": ""} for url in urls]

    sem = asyncio.Semaphore(3)
    async with _crawl_client() as client:
        tasks = {
            asyncio.ensure_future(_fetch_page(client, url, sem)): url
            for url in urls
        }

        with observability.stage("rag.crawl.total"):
            done, pending = await asyncio.wait(tasks.keys(), timeout=budget)

        for task in pending:
            task.cancel()
        if pending:
            observability.incr("crawl_timeout", len(pending))
            logger.info(
                "retrieval budget exhausted; abandoning %d of %d page fetches",
                len(pending),
                len(tasks),
            )

        results = []
        for task, url in tasks.items():
            if task in done and not task.cancelled():
                exc = task.exception()
                if exc is None:
                    results.append(task.result())
                    continue
                logger.warning("page fetch failed for %s: %s", url, exc)
            results.append({"url": url, "content": ""})
        return results


_CONVERSATIONAL_RE = re.compile(
    r"^(hi+|hello+|hey+|yo|sup|thanks?|thank\s*you|thx|ty|ok(?:ay)?|cool|nice|great|"
    r"awesome|yes|yep|yeah|no|nope|nah|sure|got\s*it|understood|please|sorry|bye|goodbye)"
    r"[\s!.?,]*$",
    re.IGNORECASE,
)

_INTERROGATIVE_PREFIXES = (
    "who", "what", "when", "where", "why", "how", "which",
    "is ", "are ", "was ", "were ", "can ", "could ", "do ", "does ", "did ",
    "should ", "would ", "will ", "tell me", "explain", "describe", "list", "show me",
)

_PRONOUN_RE = re.compile(
    r"\b(it|this|that|those|these|they|them|he|she|him|her|more|another|the\s+same)\b",
    re.IGNORECASE,
)

# Requests that operate on the answer already in the conversation rather than
# asking for new information. Searching the web for these wastes a query
# rewrite, two searches, and up to five page fetches to produce context the
# model will not use.
_META_OPERATION_RE = re.compile(
    r"\b(rewrite|rephrase|reword|reformat|shorten|lengthen|expand|simplify|"
    r"clarify|condense|elaborate|summar(?:ise|ize)|translate|repeat|"
    r"bullet|bullets|table|shorter|longer|simpler|concise|verbose)\b",
    re.IGNORECASE,
)

# What a meta request points at: the previous answer.
_META_TARGET_RE = re.compile(
    r"\b(that|this|it|above|again|your\s+(?:answer|response|reply)|"
    r"the\s+(?:answer|response|reply))\b",
    re.IGNORECASE,
)

# Fixed phrases that are unambiguous on their own. Kept separate from
# _META_OPERATION_RE because their verbs ("say") are far too common to list
# there -- "what does it say about parking" is a real question.
_META_PHRASE_RE = re.compile(
    r"\bsay\s+(?:that|it|this)\s+again\b|\bone\s+more\s+time\b|\bin\s+other\s+words\b",
    re.IGNORECASE,
)

# Bare instructions that need no target to be unambiguous.
_META_STANDALONE_RE = re.compile(
    r"^(tl;?dr|shorter|longer|simpler|briefer|bullet\s*points?|in\s+bullets|"
    r"as\s+a\s+list|in\s+a\s+table|again|continue|go\s+on)[\s!.?,]*$",
    re.IGNORECASE,
)

# Anchors and acronyms that decide whether a query is already search-ready.
_SJSU_MENTION_RE = re.compile(r"\bsjsu\b|\bsan\s+jos[eé]\s+state\b", re.IGNORECASE)

# Campus acronyms whose expansion materially changes search results.
_ACRONYM_RE = re.compile(
    r"\b(CPT|OPT|FAFSA|GE|WST|EOP|I-?20|F-?1|J-?1|MYSJSU|SEVIS|TOEFL|IELTS)\b"
)

MAX_META_REQUEST_WORDS = 12
MIN_SEARCHABLE_WORDS = 4
MAX_SEARCHABLE_WORDS = 20


def _is_conversational(message: str) -> bool:
    stripped = message.strip()
    return bool(_CONVERSATIONAL_RE.match(stripped))


def _is_meta_request(message: str) -> bool:
    """True when the turn asks to restate or reformat the previous answer.

    Deliberately conservative: a meta operation only counts when it also refers
    to the prior turn, so 'summarize that' skips retrieval but 'summarize the CS
    degree requirements' still searches.
    """
    stripped = message.strip()
    if _META_STANDALONE_RE.match(stripped):
        return True
    if len(stripped.split()) > MAX_META_REQUEST_WORDS:
        return False
    if _META_PHRASE_RE.search(stripped):
        return True
    return bool(_META_OPERATION_RE.search(stripped) and _META_TARGET_RE.search(stripped))


def needs_query_rewrite(question: str) -> bool:
    """Whether a question needs the LLM rewrite before searching.

    The rewrite is a provider round trip on the critical path. It earns its
    place when the query lacks an SJSU anchor, is too terse to search well, is
    long enough to need condensing, or contains an acronym whose expansion
    changes the results. An already-specific, already-anchored question does
    not need it.
    """
    stripped = (question or "").strip()
    if not stripped:
        return False

    words = stripped.split()
    if len(words) < MIN_SEARCHABLE_WORDS or len(words) > MAX_SEARCHABLE_WORDS:
        return True
    if _ACRONYM_RE.search(stripped):
        return True
    return not _SJSU_MENTION_RE.search(stripped)


def _looks_like_followup(message: str) -> bool:
    stripped = message.strip()
    if len(stripped.split()) >= 7:
        return False
    lowered = stripped.lower()
    if _PRONOUN_RE.search(lowered):
        return True
    return not any(lowered.startswith(w) for w in _INTERROGATIVE_PREFIXES)


def prepare_rag_query(messages: list[dict]) -> str | None:
    """Pick a search query from the conversation, or return None to skip RAG.

    Skips conversational acknowledgments and requests that operate on the answer
    already in context. For short follow-ups that lean on prior context
    (pronouns, no interrogative), prepends the previous user turn to make the
    search query self-contained.
    """
    if not messages:
        return None
    last_user_idx = next(
        (i for i in range(len(messages) - 1, -1, -1) if messages[i].get("role") == "user"),
        None,
    )
    if last_user_idx is None:
        return None
    last_user = (messages[last_user_idx].get("content") or "").strip()
    if not last_user:
        return None
    if _is_conversational(last_user):
        return None
    if _is_meta_request(last_user):
        logger.info("skipping retrieval: request operates on the previous answer")
        return None
    if _looks_like_followup(last_user):
        prior = next(
            (
                (messages[i].get("content") or "").strip()
                for i in range(last_user_idx - 1, -1, -1)
                if messages[i].get("role") == "user"
            ),
            "",
        )
        if prior:
            return f"{prior} {last_user}".strip()
    return last_user


async def _search_within(rewritten: str, budget: float) -> list[dict]:
    """Run both searches concurrently, keeping whichever finished in time.

    DDGS is synchronous, so these run on the search pool. Note that abandoning
    the await does not stop the underlying thread -- the pool bound is what
    keeps that from accumulating.
    """
    started = time.perf_counter()

    def _timed(name):
        # Each search's own duration, recorded only if it finished in time.
        def _done(task):
            if not task.cancelled():
                observability.add_stage(name, round((time.perf_counter() - started) * 1000, 1))
        return _done

    sjsu = asyncio.ensure_future(runtime.run_search(search_web, f"{rewritten} site:sjsu.edu"))
    general = asyncio.ensure_future(runtime.run_search(search_web, rewritten))
    sjsu.add_done_callback(_timed("rag.search.sjsu"))
    general.add_done_callback(_timed("rag.search.general"))

    with observability.stage("rag.search.gather"):
        done, pending = await asyncio.wait({sjsu, general}, timeout=max(budget, 0))
    for task in pending:
        task.cancel()
    if pending:
        observability.incr("search_abandoned", len(pending))
        logger.info("search budget exhausted; %d of 2 searches abandoned", len(pending))

    results = []
    # Preserve ordering: SJSU-scoped hits first, since rank_sources uses
    # original position as the tiebreak.
    for task in (sjsu, general):
        if task in done and not task.cancelled() and task.exception() is None:
            results.extend(task.result())
        elif task in done and task.exception() is not None:
            logger.warning("search failed: %s", task.exception())
    return results


async def build_rag_prompt(messages: list[dict]) -> tuple[str | None, list[dict]]:
    with observability.stage("rag.prepare"):
        question = prepare_rag_query(messages)
    observability.record("rag_skipped", 0 if question else 1)
    if not question:
        return None, []

    deadline = time.monotonic() + RETRIEVAL_DEADLINE

    # The rewrite is a provider round trip in the critical path. Skip it when
    # the question is already anchored and specific enough to search directly.
    if needs_query_rewrite(question):
        observability.record("rewrite_skipped", 0)
        with observability.stage("rag.rewrite"):
            rewritten = await rewrite_query_for_sjsu(question)
        logger.info(
            "RAG query rewritten",
            extra={
                "question_chars": len(question),
                "query_chars": len(rewritten),
                "query_hash": _digest(rewritten),
            },
        )
    else:
        observability.record("rewrite_skipped", 1)
        rewritten = question
        logger.info("skipping query rewrite: question is already search-ready")

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        logger.info("retrieval budget spent on query rewrite; skipping search")
        return None, []

    try:
        search_results = await _search_within(rewritten, remaining)
    except Exception:
        logger.exception("search step failed", extra={"query_hash": _digest(rewritten)})
        return None, []

    observability.record("search_results", len(search_results))
    if not search_results:
        return None, []

    top_sources = rank_sources(search_results)
    observability.record("sources_found", len(top_sources))
    if not top_sources:
        return None, []

    # Whatever is left goes to crawling. Sources that don't finish fall back to
    # their search snippet below.
    pages = await crawl_sources(
        [s["url"] for s in top_sources],
        budget=max(deadline - time.monotonic(), 0.0),
    )
    with observability.stage("rag.assemble"):
        page_by_url = {p["url"]: p for p in pages}
        used_sources = []
        context_blocks = []
        total_chars = 0
        snippet_fallbacks = 0

        for source in top_sources:
            page = page_by_url.get(source["url"])
            content = page.get("content", "") if page else ""
            if not content:
                snippet = source.get("snippet") or ""
                if snippet:
                    content = snippet
                    snippet_fallbacks += 1
                else:
                    continue
            remaining = MAX_TOTAL_CHARS - total_chars
            if remaining <= 0:
                break
            if len(content) > remaining:
                content = content[:remaining].rsplit(" ", 1)[0] + "..."
            total_chars += len(content)
            used_sources.append(
                {
                    "title": source.get("title") or source["url"],
                    "url": source["url"],
                }
            )
            context_blocks.append(
                f"[{len(used_sources)}] {used_sources[-1]['title']} - {used_sources[-1]['url']}\n{content}"
            )

    observability.record("sources_used", len(used_sources))
    observability.record("snippet_fallbacks", snippet_fallbacks)
    observability.record("context_chars", total_chars)
    if not context_blocks:
        return None, []

    prompt = (
        "Answer the question using the context below. "
        "If the context is incomplete, give the best possible answer and explicitly note what is missing. "
        "Only say you don't know if there is no relevant context at all. "
        "Cite sources using [1], [2], etc.\n\n"
        "Context:\n" + "\n\n".join(context_blocks)
    )
    return prompt, used_sources


async def _demo() -> None:
    question = "SJSU graduation requirements for CS masters program?"
    prompt, sources = await build_rag_prompt([{"role": "user", "content": question}])
    print("Question:", question)
    print("Sources:", sources)
    print("Prompt:\n", prompt)


if __name__ == "__main__":
    asyncio.run(_demo())
