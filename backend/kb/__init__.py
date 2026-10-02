"""Knowledge base ingestion.

Deliberately outside `services/`, because none of this runs inside a request.
`services/` is the chat path, where every millisecond is on a user's critical
path; this is an offline pipeline where being slow is free and being blocked is
not.

What this package does NOT do, and why it matters: it does not reuse
`services/web_search.crawl_sources` or `_fetch_page`. Those are built for a
six-second deadline inside a chat turn -- `_fetch_page` truncates every page to
6,000 characters before the caller sees it, and `crawl_sources` collapses "empty
page", "DNS failed", "403" and "budget expired" into one `{"content": ""}` stub.
Both are correct choices there and wrong here, where the whole point is a
complete page and a reason for every page we skip. The genuinely reusable parts
-- `_extract_main_text`, `_host_is_public`, `_canonicalize_url` -- are imported.
"""
