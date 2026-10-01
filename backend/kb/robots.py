"""robots.txt, fetched once per host per run.

Three things here are deliberate rather than incidental.

**The parser never fetches.** `urllib.robotparser.RobotFileParser` will happily
open a URL itself, synchronously, with no timeout -- on the event loop. So the
text is fetched with the same httpx client as everything else and handed to
`.parse()`.

**A crawl delay from robots always wins over our own.** `catalog.sjsu.edu`
returns `crawl-delay: 120`, which is about thirty pages an hour. That is not a
number to argue with: the alternative to honouring it is being blocked, and
`www.sjsu.edu/robots.txt` asks in plain English -- *"Note: Please do not over
load the servers"* -- with an ITS contact address.

**An unreachable robots.txt skips the host for the run.** A 404 means "no rules,
crawl freely", which is the standard reading. A 5xx or a timeout means the server
is already unhealthy, and the conservative response to that is to come back
later rather than to assume permission.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

logger = logging.getLogger(__name__)

# The crawler identifies itself. Three reasons, in order of how much they matter:
#
#   1. Claiming to obey a rule addressed to crawlers while presenting as Chrome
#      is incoherent -- robots.txt compliance needs a matching token.
#   2. A bulk crawler pretending to be a browser is exactly the traffic a WAF
#      bans, and when it does, the 403 is unattributable. An identified bot that
#      gets blocked can be unblocked by asking.
#   3. SJSU's logs can separate ingestion from live chat traffic, which matters
#      the first time someone has to debug one of them.
#
# Deliberately NOT runtime.USER_AGENT, which is a spoofed Chrome string used by
# the live retrieval path. Changing that one affects every user's answer
# immediately and is a different decision with a different blast radius.
USER_AGENT = (
    "SJSUCopilotBot/0.1 "
    "(+https://github.com/sravan1023/SJSU-Copilot; knowledge-base ingestion)"
)

ROBOTS_TIMEOUT = 10.0


@dataclass
class HostRules:
    """What robots.txt says about one host, plus whether we could read it."""

    host: str
    reachable: bool
    parser: RobotFileParser | None = None
    crawl_delay: float | None = None
    status: int | None = None
    error: str | None = None

    def allows(self, url: str) -> bool:
        """True when robots.txt permits fetching this URL.

        An unreachable robots.txt returns False for every URL: the caller skips
        the host rather than guessing.
        """
        if not self.reachable:
            return False
        if self.parser is None:
            return True  # 404: no rules at all
        return self.parser.can_fetch(USER_AGENT, url)


@dataclass
class RobotsCache:
    """One lookup per host for the lifetime of a run."""

    client: httpx.AsyncClient
    _hosts: dict[str, HostRules] = field(default_factory=dict)

    async def for_url(self, url: str) -> HostRules:
        host = urlsplit(url).netloc.lower()
        cached = self._hosts.get(host)
        if cached is not None:
            return cached
        rules = await self._fetch(url, host)
        self._hosts[host] = rules
        return rules

    async def _fetch(self, url: str, host: str) -> HostRules:
        parts = urlsplit(url)
        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        try:
            res = await self.client.get(
                robots_url,
                timeout=ROBOTS_TIMEOUT,
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
            )
        except Exception as exc:
            logger.warning(
                "robots.txt unreachable; skipping host for this run",
                extra={"host": host, "error": type(exc).__name__},
            )
            return HostRules(host, reachable=False, error=type(exc).__name__)

        if res.status_code == 404:
            # No rules published. The standard reading is "crawl freely", and
            # our own delay still applies.
            logger.info("no robots.txt; proceeding", extra={"host": host})
            return HostRules(host, reachable=True, parser=None, status=404)

        if res.status_code >= 400:
            # Including 5xx: the server is unhealthy, so do not add load to it
            # on the assumption that we are allowed.
            logger.warning(
                "robots.txt returned an error; skipping host for this run",
                extra={"host": host, "status": res.status_code},
            )
            return HostRules(host, reachable=False, status=res.status_code)

        parser = RobotFileParser()
        parser.parse(res.text.splitlines())

        # crawl_delay() looks for a group matching our token and falls back to
        # the `*` group, which is where SJSU puts its 120.
        delay = parser.crawl_delay(USER_AGENT)
        try:
            delay = float(delay) if delay is not None else None
        except (TypeError, ValueError):
            delay = None

        if delay:
            logger.info(
                "robots.txt sets a crawl delay",
                extra={"host": host, "crawl_delay_s": delay},
            )
        return HostRules(
            host, reachable=True, parser=parser, crawl_delay=delay, status=res.status_code
        )
