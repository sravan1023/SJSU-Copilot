"""Fetching one page, with a reason for every outcome.

This re-implements the redirect loop from `services/web_search._safe_stream_get`
rather than calling it, and that is a deliberate ~25 lines of duplication. Three
things ingestion needs that the live version cannot express:

* **Conditional requests.** A `304 Not Modified` is `< 400` and carries no
  `content-type`, so it falls straight through that function's `text/html` gate
  and comes back as `None` -- indistinguishable from a failure. Conditional GETs
  are the whole mechanism for not re-embedding a page that has not changed.
* **Distinguishable outcomes.** `_safe_stream_get` returns one `None` for
  blocked, 4xx, non-HTML and transport error alike. Here, "how much of SJSU are
  we missing and why" has to be a query against `kb_ingest_runs.skip_reasons`,
  which means every refusal needs a name.
* **PDFs.** SJSU publishes a lot of policy and catalog material as PDF, and the
  live path drops anything that is not `text/html`.

Everything genuinely reusable is imported, not copied: the SSRF guard, the URL
canonicaliser, the host matcher, the byte ceiling and the redirect limit all come
from `services/web_search`. The duplication is the loop, not the judgement.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import urlsplit, urljoin

import httpx

from kb.robots import USER_AGENT, HostRules
from services.web_search import (
    MAX_BYTES_PER_PAGE,
    MAX_REDIRECTS,
    _extract_main_text,
    _host_is_public,
    _host_matches,
    _hostname,
)

logger = logging.getLogger(__name__)

# Below this, an HTTP 200 is not worth storing. The usual cause is a page whose
# body is rendered by JavaScript -- one.sjsu.edu is the likely offender -- where
# the served HTML is a shell. Counting these is how we find out which seeds are
# useless without adding a headless browser to the dependency tree.
#
# **Lowered from 500 to 200 on 2026-10-01, on a measurement.** At 500 the only
# page rejected across all 35 seeds was /registrar/transcripts/ (-> /transcripts/),
# and inspection showed the rejection was wrong: extraction worked correctly and
# returned every word the page has. The page is a 180-character stub whose whole
# body is "All current SJSU students and alumni may order copies of their
# transcript online. We offer both electronic and paper versions." -- which is the
# answer to bench question `alumni-faq-1`, "How do I order an official transcript
# as an alumnus?". A threshold meant to detect *extraction failure* was instead
# rejecting a page for being terse.
#
# 200 is deliberately just under that page. Verified: across the 35 seeds this
# changes exactly one outcome and admits no other page, so it is a measured
# adjustment rather than a loosened guard. A JavaScript shell extracts to tens of
# characters, not two hundred, so the original purpose still holds.
MIN_USEFUL_CHARS = 200

REQUEST_TIMEOUT = 20.0


class Outcome(str, Enum):
    """Why a fetch ended the way it did. Every value becomes a `skip_reasons` key."""

    OK = "ok"
    NOT_MODIFIED = "not_modified"
    ROBOTS_DENIED = "robots_denied"
    ROBOTS_UNAVAILABLE = "robots_unavailable"
    NON_HTML = "non_html"
    HTTP_4XX = "http_4xx"
    HTTP_429 = "http_429"
    HTTP_5XX = "http_5xx"
    TIMEOUT = "timeout"
    TRANSPORT_ERROR = "transport_error"
    BLOCKED_HOST = "blocked_host"
    OFF_ALLOWLIST = "off_allowlist"
    TOO_MANY_REDIRECTS = "too_many_redirects"
    THIN = "thin"
    PDF_UNSUPPORTED = "pdf_unsupported"
    PDF_UNREADABLE = "pdf_unreadable"


# Outcomes worth another attempt: the host is up but transiently unhappy. A 4xx
# other than 429 is a statement about the request, so retrying is just noise.
RETRYABLE = frozenset(
    {Outcome.TIMEOUT, Outcome.HTTP_429, Outcome.HTTP_5XX, Outcome.TRANSPORT_ERROR}
)


@dataclass
class FetchResult:
    outcome: Outcome
    url: str
    final_url: str | None = None
    text: str | None = None
    # The markup, kept only for OK HTML fetches. The chunker needs it: heading
    # paths are the signal that distinguishes "Deadlines" under Admissions from
    # "Deadlines" under Financial Aid, and `text` has already lost every <h2>.
    # Discarding it here was the first version's mistake -- the survey reported
    # 0 of 109 chunks with a heading and the heading-aware path was dead code.
    html: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    status: int | None = None
    content_type: str | None = None
    retry_after: float | None = None
    detail: str | None = None
    links: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.OK


def _is_pdf(content_type: str) -> bool:
    return "application/pdf" in content_type or content_type.endswith("/pdf")


def extract_pdf_text(data: bytes) -> str:
    """Text from a PDF, with repeated page furniture removed.

    `pypdf` happily returns the running header and footer on every page, so a
    thirty-page policy document arrives with its title and page number woven
    through the body thirty times. Left in, that furniture dominates the
    generated tsvector and every chunk looks like every other chunk.

    A line is treated as furniture when it appears on more than half the pages.
    That is a blunt rule, and it is the right kind of blunt: it cannot remove a
    sentence that only occurs once, and the failure mode of being too timid is
    merely noisier chunks.
    """
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover - dependency is declared
        raise

    import io

    reader = PdfReader(io.BytesIO(data))
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")

    if not pages:
        return ""

    counts: dict[str, int] = {}
    for page in pages:
        for line in {ln.strip() for ln in page.splitlines() if ln.strip()}:
            counts[line] = counts.get(line, 0) + 1

    threshold = max(2, len(pages) // 2 + 1)
    furniture = {line for line, n in counts.items() if n >= threshold and len(line) < 120}

    cleaned = []
    for page in pages:
        kept = [ln for ln in (l.strip() for l in page.splitlines()) if ln and ln not in furniture]
        if kept:
            cleaned.append("\n".join(kept))
    return "\n\n".join(cleaned)


def extract_links(html: str, base_url: str, *, allowed_hosts: list[str]) -> list[str]:
    """Absolute, same-site links from a page.

    This parses the **raw** HTML with its own soup rather than reusing
    `_extract_main_text`'s, because that function `decompose()`s `nav`, `header`,
    `footer` and `aside` -- which is exactly where a site keeps the navigation
    worth following.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    out: list[str] = []
    seen: set[str] = set()
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(base_url, href)
        parts = urlsplit(absolute)
        if parts.scheme not in ("http", "https"):
            continue
        host = parts.netloc.lower()
        if not any(_host_matches(host, allowed) for allowed in allowed_hosts):
            continue
        clean = absolute.split("#", 1)[0]
        if clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
    return out


async def conditional_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    rules: HostRules,
    etag: str | None = None,
    last_modified: str | None = None,
    allowed_hosts: list[str] | None = None,
    want_links: bool = False,
) -> FetchResult:
    """Fetch one URL, following redirects by hand and re-checking every hop.

    Redirects are walked manually, as the live crawler does, so the scheme and
    host can be re-validated at each hop -- an open redirect on a permitted host
    is otherwise a route to anywhere.
    """
    if not rules.reachable:
        return FetchResult(Outcome.ROBOTS_UNAVAILABLE, url, detail=rules.error, status=rules.status)
    if not rules.allows(url):
        return FetchResult(Outcome.ROBOTS_DENIED, url)

    allowed = allowed_hosts or [_hostname(url)]
    headers = {"User-Agent": USER_AGENT}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    current = url
    for _ in range(MAX_REDIRECTS + 1):
        parts = urlsplit(current)
        if parts.scheme not in ("http", "https"):
            return FetchResult(Outcome.BLOCKED_HOST, url, detail="non-http scheme")
        host = parts.netloc.lower()
        if not any(_host_matches(host, a) for a in allowed):
            return FetchResult(Outcome.OFF_ALLOWLIST, url, final_url=current, detail=host)
        if not await _host_is_public(parts.hostname or ""):
            return FetchResult(Outcome.BLOCKED_HOST, url, final_url=current, detail=host)

        try:
            async with client.stream(
                "GET", current, headers=headers, timeout=REQUEST_TIMEOUT
            ) as res:
                # 304 first: it has no content-type and no body, so every check
                # below would misread it.
                if res.status_code == 304:
                    return FetchResult(
                        Outcome.NOT_MODIFIED, url, final_url=current, status=304,
                        etag=res.headers.get("etag") or etag,
                        last_modified=res.headers.get("last-modified") or last_modified,
                    )

                if res.status_code in (301, 302, 303, 307, 308):
                    location = res.headers.get("location")
                    if not location:
                        return FetchResult(
                            Outcome.HTTP_4XX, url, final_url=current,
                            status=res.status_code, detail="redirect without location",
                        )
                    current = urljoin(current, location)
                    continue

                if res.status_code == 429:
                    retry = res.headers.get("retry-after")
                    try:
                        wait = float(retry) if retry else None
                    except ValueError:
                        wait = None
                    return FetchResult(
                        Outcome.HTTP_429, url, final_url=current, status=429, retry_after=wait
                    )
                if 500 <= res.status_code:
                    return FetchResult(Outcome.HTTP_5XX, url, final_url=current, status=res.status_code)
                if 400 <= res.status_code:
                    return FetchResult(Outcome.HTTP_4XX, url, final_url=current, status=res.status_code)

                content_type = (res.headers.get("content-type") or "").lower()
                etag_out = res.headers.get("etag")
                lm_out = res.headers.get("last-modified")

                body = bytearray()
                async for chunk in res.aiter_bytes():
                    body.extend(chunk)
                    if len(body) >= MAX_BYTES_PER_PAGE:
                        break

                if _is_pdf(content_type):
                    try:
                        text = extract_pdf_text(bytes(body))
                    except ImportError:
                        return FetchResult(
                            Outcome.PDF_UNSUPPORTED, url, final_url=current,
                            status=res.status_code, content_type=content_type,
                            detail="pypdf not installed",
                        )
                    except Exception as exc:
                        return FetchResult(
                            Outcome.PDF_UNREADABLE, url, final_url=current,
                            status=res.status_code, content_type=content_type,
                            detail=type(exc).__name__,
                        )
                    if len(text.strip()) < MIN_USEFUL_CHARS:
                        return FetchResult(
                            Outcome.THIN, url, final_url=current, status=res.status_code,
                            content_type=content_type, detail=f"{len(text.strip())} chars",
                        )
                    return FetchResult(
                        Outcome.OK, url, final_url=current, text=text, status=res.status_code,
                        content_type=content_type, etag=etag_out, last_modified=lm_out,
                    )

                if "text/html" not in content_type:
                    return FetchResult(
                        Outcome.NON_HTML, url, final_url=current,
                        status=res.status_code, content_type=content_type,
                    )

                html = bytes(body).decode(res.charset_encoding or "utf-8", errors="replace")
                text = _extract_main_text(html)
                if len(text.strip()) < MIN_USEFUL_CHARS:
                    # Almost always a JavaScript-rendered page. Counted rather
                    # than fixed: adding a headless browser for a handful of
                    # seeds is the wrong trade.
                    return FetchResult(
                        Outcome.THIN, url, final_url=current, status=res.status_code,
                        content_type=content_type, detail=f"{len(text.strip())} chars",
                    )

                links = (
                    extract_links(html, current, allowed_hosts=allowed) if want_links else []
                )
                return FetchResult(
                    Outcome.OK, url, final_url=current, text=text, html=html,
                    status=res.status_code, content_type=content_type,
                    etag=etag_out, last_modified=lm_out, links=links,
                )

        except httpx.TimeoutException:
            return FetchResult(Outcome.TIMEOUT, url, final_url=current)
        except httpx.HTTPError as exc:
            return FetchResult(
                Outcome.TRANSPORT_ERROR, url, final_url=current, detail=type(exc).__name__
            )

    return FetchResult(Outcome.TOO_MANY_REDIRECTS, url, final_url=current)
