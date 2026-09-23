"""Per-caller request limits.

The API had none. That was survivable while every caller had to hold a Supabase
session obtained through an @sjsu.edu signup; it stops being survivable the
moment `POST /api/guest/session` will hand a token to anybody who asks, because
`/api/chat` spends real money at a provider whose free tier is 8,000 tokens per
minute **for the whole organisation**.

**What this actually buys, stated honestly.** A caller can mint unlimited guest
ids, so the per-principal bucket alone is decorative against a determined
attacker -- they would simply take a fresh identity each time. The per-IP
bucket is the real control, and it is in turn blunted by NAT: one campus
library shares one bucket. So this slows abuse and caps accidental runaway
clients. It does not prevent a determined attacker, and nothing in-process can.

**In-process state.** Correct for the single uvicorn process this runs as
(`main.py` starts it with no `workers=`). With N workers the effective limit is
N x these numbers, because each holds its own dicts. Phase 5 moves the two dict
accesses to a shared store; the signatures here are chosen so nothing else
changes.

**Env is read per call, not at import.** `runtime.py` reads its constants at
import, which makes them invisible to `patch.dict(os.environ, ...)` -- the
idiom this test suite uses everywhere. `auth.py` reads per call for exactly
this reason and so does this module.
"""
import logging
import math
import os
import time
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request

from auth import Principal, require_principal

logger = logging.getLogger(__name__)

# Bounded like auth.py's _grant_cache, and for the same reason: an unbounded
# dict keyed by something the caller chooses is a memory leak with extra steps.
_MAX_KEYS = 4096

# key -> (tokens remaining, monotonic timestamp of the last refill)
_buckets: dict[str, tuple[float, float]] = {}

# principal key -> requests currently in flight
_inflight: dict[str, int] = {}


def clear_rate_limits() -> None:
    """Drop all limiter state. For tests; nothing in the app calls it.

    `tests/test_auth.py::_request` calls this next to `clear_grant_cache()`.
    Without it, a suite that drives dozens of in-process requests starts
    tripping buckets and the failures present as unrelated auth errors.
    """
    _buckets.clear()
    _inflight.clear()


def _enabled() -> bool:
    return os.getenv("RATE_LIMIT_ENABLED", "true").strip().lower() not in ("0", "false", "no")


def _num(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("%s is not a number; using %s", name, default)
        return default
    return value if value >= 0 else default


@dataclass(frozen=True)
class Limit:
    per_minute: float
    burst: float

    @property
    def refill_per_second(self) -> float:
        return self.per_minute / 60.0


def limit_for(kind: str) -> Limit:
    """The bucket a principal of this kind gets."""
    if kind == "guest":
        return Limit(_num("GUEST_RATE_PER_MIN", 10), _num("GUEST_RATE_BURST", 4))
    # 30/min matches the provider's own per-organisation request ceiling, so a
    # single signed-in user cannot consume the whole allowance on their own.
    return Limit(_num("USER_RATE_PER_MIN", 30), _num("USER_RATE_BURST", 10))


def ip_limit() -> Limit:
    """The budget for an address, independent of who is calling from it.

    Deliberately its own numbers rather than the per-principal ones, and
    deliberately generous. An address is not a person: a campus library, a
    lecture hall on wifi and a household all share one, so a ceiling tight
    enough to be meaningful against one attacker would lock out a whole room of
    legitimate users. It is a backstop against a single host hammering the API,
    not a per-user control -- that is what the principal bucket is.

    Sizing it off the per-principal limits would also be wrong in a subtler
    way: the same `ip:` key would be refilled with a guest's rate on one
    request and a user's on the next, so its ceiling would flip around
    depending on who happened to call last.
    """
    return Limit(_num("IP_RATE_PER_MIN", 120), _num("IP_RATE_BURST", 40))


def ip_session_limit() -> Limit:
    """Guards POST /api/guest/session itself, which mints the tokens."""
    return Limit(_num("GUEST_SESSION_PER_HOUR", 20) / 60.0, _num("GUEST_SESSION_BURST", 5))


def _evict_if_full(key: str) -> None:
    """Make room by dropping the least recently active caller.

    Same shape as auth.py's grant-cache eviction. The honest caveat: under a
    flood of fresh keys an attacker can evict their own throttled bucket and
    start over. That is what the IP bucket is for -- an attacker controls many
    guest ids but few addresses, so the IP bucket is not evictable by guest-id
    churn.
    """
    if len(_buckets) < _MAX_KEYS or key in _buckets:
        return
    _buckets.pop(min(_buckets, key=lambda k: _buckets[k][1]), None)


def _take(key: str, limit: Limit) -> float:
    """Spend one token. Returns 0.0 on success, else seconds until one is free."""
    now = time.monotonic()
    tokens, last = _buckets.get(key, (limit.burst, now))

    tokens = min(limit.burst, tokens + (now - last) * limit.refill_per_second)

    if tokens < 1.0:
        _buckets[key] = (tokens, now)
        if limit.refill_per_second <= 0:
            return 3600.0
        return (1.0 - tokens) / limit.refill_per_second

    _evict_if_full(key)
    _buckets[key] = (tokens - 1.0, now)
    return 0.0


def _too_many(retry_after: float) -> HTTPException:
    # Integer seconds, always present. Deliberately no X-RateLimit-* headers:
    # these numbers are not a published contract, and main.py's CORS
    # allow_headers would have to be widened before a browser could read them.
    return HTTPException(
        status_code=429,
        detail="too many requests",
        headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
    )


def client_ip(request: Request) -> str:
    """The caller's address.

    **X-Forwarded-For is deliberately ignored.** There is no deployment config
    in this repo, so there is no trusted proxy whose hop count could be
    validated, and honouring the header unconditionally would let any caller
    pick their own bucket by setting it. Phase 5 revisits this together with
    `uvicorn --proxy-headers`.
    """
    return request.client.host if request.client else "unknown"


def check_ip(request: Request, limit: Limit) -> None:
    """Rate-limit by address alone. For endpoints with no principal yet."""
    if not _enabled():
        return
    wait = _take(f"ip:{client_ip(request)}", limit)
    if wait:
        logger.warning("rate limited by ip", extra={"retry_after": round(wait, 1)})
        raise _too_many(wait)


def rate_limited(
    request: Request,
    principal: Principal = Depends(require_principal),
) -> Principal:
    """Gate a route on this caller's budget, then hand the principal on.

    **A dependency, not middleware.** `/api/chat` returns a StreamingResponse,
    and `routers/chat.py` already documents that middleware around one finishes
    when the response object is returned rather than when the stream drains --
    so a middleware limiter would release its concurrency slot at the wrong
    moment, exactly where the limit matters.

    Depends on `require_principal`, so an unverified caller is rejected by auth
    before spending a token: an attacker cannot exhaust someone else's bucket,
    or fill the dict, with garbage tokens.
    """
    if not _enabled():
        return principal

    key = principal.rate_key

    # Address first, on its own generous budget. The per-principal key is only
    # as trustworthy as the caller's willingness to keep the same identity, and
    # a guest's costs nothing to replace -- so the address is the only part an
    # attacker cannot churn. It is blunted by NAT in the other direction, which
    # is why ip_limit() is sized for a shared address rather than a person.
    wait = _take(f"ip:{client_ip(request)}", ip_limit())
    if wait:
        logger.warning(
            "rate limited by ip",
            extra={"kind": principal.kind, "retry_after": round(wait, 1)},
        )
        raise _too_many(wait)

    wait = _take(key, limit_for(principal.kind))
    if wait:
        logger.warning(
            "rate limited", extra={"kind": principal.kind, "retry_after": round(wait, 1)}
        )
        raise _too_many(wait)

    return principal


# ── Concurrency ───────────────────────────────────────────────────────────────


def max_concurrent() -> int:
    return int(_num("MAX_CONCURRENT_PER_PRINCIPAL", 2))


def acquire_slot(principal: Principal) -> bool:
    """Claim one concurrent slot. False when the caller is already at the cap.

    A counter rather than a dict of `asyncio.Semaphore`, because a *held*
    Semaphore cannot be safely evicted, so that dict would grow without bound
    for the same reason the buckets would. An int is evict-safe: a key at zero
    is holding nothing and can be dropped.
    """
    if not _enabled():
        return True
    key = principal.rate_key
    current = _inflight.get(key, 0)
    if current >= max_concurrent():
        return False
    _inflight[key] = current + 1
    return True


def release_slot(principal: Principal) -> None:
    """Return a slot. Must run when the *stream* ends, not when the handler does."""
    key = principal.rate_key
    current = _inflight.get(key, 0) - 1
    if current > 0:
        _inflight[key] = current
    else:
        # Drop at zero rather than storing 0 forever; this is what keeps
        # _inflight bounded without an eviction pass.
        _inflight.pop(key, None)


def concurrency_error() -> HTTPException:
    return HTTPException(
        status_code=429,
        detail="too many concurrent requests",
        headers={"Retry-After": "5"},
    )
