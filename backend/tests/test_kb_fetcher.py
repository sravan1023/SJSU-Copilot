"""
`conditional_get`'s byte cap and raw-HTML mode, against respx. No network.

Run from backend/ with:
    python -m pytest tests/test_kb_fetcher.py

The campus parsers (SERVICES_BUILD_PLAN F2) need two things the KB never did: a
cap above the live crawler's, and the raw markup with no text extraction. The
last test pins the default path, because every KB seed goes through it.
"""
import asyncio
from unittest.mock import patch

import httpx
import respx

from kb import fetcher
from kb.fetcher import Outcome, conditional_get
from kb.robots import HostRules

URL = "https://www.sjsu.edu/classes/schedules/fall-2026.php"
RULES = HostRules(host="www.sjsu.edu", reachable=True)


def _page(target_bytes: int) -> str:
    row = "<tr><td>CS 46A</td><td>Introduction to Programming</td></tr>"
    body = "<p>" + ("Words about the schedule. " * 20) + "</p>"
    html = "<html><body><main>" + body + "<table id='classSchedule'>"
    while len(html) < target_bytes:
        html += row
    return html + "</table></main></body></html>"


def _get(html: str, **kwargs):
    async def run():
        async def public(_host):
            return True

        with patch.object(fetcher, "_host_is_public", public), respx.mock:
            respx.get(URL).respond(
                200, text=html, headers={"content-type": "text/html; charset=utf-8"}
            )
            async with httpx.AsyncClient() as client:
                return await conditional_get(client, URL, rules=RULES, **kwargs)

    return asyncio.run(run())


def test_truncated_flag_is_set_at_the_cap():
    res = _get(_page(5000), max_bytes=1000)
    assert res.ok and res.truncated


def test_raised_cap_reads_a_large_page_in_full():
    html = _page(3_800_000)
    res = _get(html, max_bytes=8_000_000, extract=False)
    assert res.ok and not res.truncated
    assert len(res.html) == len(html)
    assert res.html.count("<tr>") == html.count("<tr>")


def test_extract_false_returns_raw_html_and_skips_the_thin_check():
    html = "<html><body><main><table id='classSchedule'><tr><td>x</td></tr></table></main></body></html>"
    thin = _get(html)
    assert thin.outcome is Outcome.THIN
    raw = _get(html, extract=False)
    assert raw.ok and raw.html == html and raw.text is None


def test_default_path_is_unchanged():
    res = _get(_page(5000))
    assert res.ok and not res.truncated
    assert res.text and "Words about the schedule" in res.text
    assert res.html and "classSchedule" in res.html
