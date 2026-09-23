"""
Per-caller rate limits.

Run from backend/ with:
    python -m pytest tests/test_ratelimit.py

The session fixture in conftest.py sets RATE_LIMIT_ENABLED=false, because the
other suites drive dozens of in-process requests and would otherwise trip
budgets in ways that present as unrelated auth failures. Everything here turns
it back on explicitly, which also means these tests fail loudly if the default
ever flips.

Most of this exercises the module directly rather than over HTTP: a token
bucket is arithmetic, and driving it through the app would mean mocking a
provider to test a counter. The two things that genuinely need the app -- that
the limiter is wired to /api/chat at all, and that the mint endpoint guards
itself -- go through it.
"""
import asyncio
import os
import time
from unittest.mock import patch

import httpx
import pytest

import main
import ratelimit
from auth import Principal

from .conftest import AUTH_HEADERS, GUEST_HEADERS

ON = {"RATE_LIMIT_ENABLED": "true"}

USER = Principal(kind="user", user_id="11111111-1111-1111-1111-111111111111")
GUEST = Principal(kind="guest", guest_id="a" * 32)


@pytest.fixture(autouse=True)
def _clean():
    ratelimit.clear_rate_limits()
    yield
    ratelimit.clear_rate_limits()


def _request(path, headers=None, env=None, **kwargs):
    async def go():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(path, headers=headers or {}, **kwargs)

    with patch.dict(os.environ, {**ON, **(env or {})}):
        return asyncio.run(go())


# ── The bucket ────────────────────────────────────────────────────────────────


def test_a_burst_is_allowed_then_refused():
    limit = ratelimit.Limit(per_minute=60, burst=3)
    with patch.dict(os.environ, ON):
        assert ratelimit._take("k", limit) == 0.0
        assert ratelimit._take("k", limit) == 0.0
        assert ratelimit._take("k", limit) == 0.0
        wait = ratelimit._take("k", limit)
    assert wait > 0, "the fourth request in a burst of 3 must be refused"


def test_tokens_refill_with_time():
    limit = ratelimit.Limit(per_minute=60, burst=2)  # one token per second
    with patch.dict(os.environ, ON), patch("ratelimit.time.monotonic") as clock:
        clock.return_value = 1000.0
        assert ratelimit._take("k", limit) == 0.0
        assert ratelimit._take("k", limit) == 0.0
        assert ratelimit._take("k", limit) > 0

        clock.return_value = 1001.5  # 1.5s -> 1.5 tokens
        assert ratelimit._take("k", limit) == 0.0


def test_the_wait_is_how_long_until_a_token_exists():
    limit = ratelimit.Limit(per_minute=60, burst=1)  # one per second
    with patch.dict(os.environ, ON), patch("ratelimit.time.monotonic") as clock:
        clock.return_value = 500.0
        assert ratelimit._take("k", limit) == 0.0
        wait = ratelimit._take("k", limit)
    # Empty bucket refilling at 1/s: the next token is ~1s away.
    assert 0.9 < wait <= 1.0


def test_callers_do_not_share_a_bucket():
    limit = ratelimit.Limit(per_minute=60, burst=1)
    with patch.dict(os.environ, ON):
        assert ratelimit._take("alice", limit) == 0.0
        assert ratelimit._take("bob", limit) == 0.0, "bob must not pay for alice"
        assert ratelimit._take("alice", limit) > 0


def test_a_guest_and_a_user_cannot_collide():
    """rate_key is namespaced, so a guest id can never name a user's bucket."""
    assert USER.rate_key != GUEST.rate_key
    assert USER.rate_key.startswith("user:")
    assert GUEST.rate_key.startswith("guest:")


def test_a_guest_gets_a_smaller_budget_than_a_user():
    with patch.dict(os.environ, ON):
        assert ratelimit.limit_for("guest").per_minute < ratelimit.limit_for("user").per_minute


def test_the_bucket_dict_stays_bounded():
    """An unbounded dict keyed by something the caller picks is a memory leak."""
    limit = ratelimit.Limit(per_minute=600, burst=5)
    with patch.dict(os.environ, ON), patch.object(ratelimit, "_MAX_KEYS", 8):
        for i in range(50):
            ratelimit._take(f"key-{i}", limit)
        assert len(ratelimit._buckets) <= 8


def test_disabling_the_limiter_lets_everything_through():
    limit = ratelimit.Limit(per_minute=60, burst=1)
    with patch.dict(os.environ, {"RATE_LIMIT_ENABLED": "false"}):
        # _take is the arithmetic and does not consult the flag; the dependency
        # does. Assert the flag is what the dependency reads.
        assert ratelimit._enabled() is False
    with patch.dict(os.environ, ON):
        assert ratelimit._enabled() is True
        ratelimit._take("k", limit)


def test_clear_rate_limits_resets_everything():
    limit = ratelimit.Limit(per_minute=60, burst=1)
    with patch.dict(os.environ, ON):
        ratelimit._take("k", limit)
        assert ratelimit._buckets
        ratelimit.acquire_slot(USER)
        assert ratelimit._inflight
    ratelimit.clear_rate_limits()
    assert not ratelimit._buckets and not ratelimit._inflight


# ── Concurrency ───────────────────────────────────────────────────────────────


def test_concurrent_slots_are_capped_and_returned():
    with patch.dict(os.environ, {**ON, "MAX_CONCURRENT_PER_PRINCIPAL": "2"}):
        assert ratelimit.acquire_slot(USER) is True
        assert ratelimit.acquire_slot(USER) is True
        assert ratelimit.acquire_slot(USER) is False, "third must be refused"

        ratelimit.release_slot(USER)
        assert ratelimit.acquire_slot(USER) is True


def test_a_released_slot_leaves_no_entry_behind():
    """Dropping at zero is what keeps _inflight bounded without an eviction pass."""
    with patch.dict(os.environ, ON):
        ratelimit.acquire_slot(GUEST)
        ratelimit.release_slot(GUEST)
        assert GUEST.rate_key not in ratelimit._inflight


def test_an_over_release_cannot_go_negative():
    with patch.dict(os.environ, ON):
        ratelimit.release_slot(USER)
        ratelimit.release_slot(USER)
        assert ratelimit._inflight.get(USER.rate_key, 0) == 0
        assert ratelimit.acquire_slot(USER) is True


# ── Wiring ────────────────────────────────────────────────────────────────────


def test_the_chat_route_answers_429_with_a_retry_after():
    """The limiter is actually attached, and says when to come back."""
    env = {**ON, "USER_RATE_PER_MIN": "60", "USER_RATE_BURST": "1"}
    body = {"messages": [{"role": "user", "content": "hi"}]}

    first = _request("/api/chat", headers=AUTH_HEADERS, env=env, json=body)
    assert first.status_code != 429

    second = _request("/api/chat", headers=AUTH_HEADERS, env=env, json=body)
    assert second.status_code == 429
    retry = second.headers["retry-after"]
    assert retry.isdigit() and int(retry) >= 1, retry


def test_an_unverified_caller_is_rejected_before_spending_a_token():
    """Otherwise a stranger could exhaust a bucket, or fill the dict, for free."""
    res = _request("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert res.status_code == 401
    assert not ratelimit._buckets, "a rejected caller must not create a bucket"


def test_the_mint_endpoint_limits_itself_by_address():
    """Without this it is a free token-minting oracle.

    A caller who can take a fresh identity per request defeats the per-guest
    bucket entirely, so the endpoint that hands out identities has to be capped
    on something the caller cannot change as cheaply.
    """
    env = {**ON, "GUEST_SESSION_PER_HOUR": "60", "GUEST_SESSION_BURST": "2"}
    codes = [_request("/api/guest/session", env=env).status_code for _ in range(4)]
    assert codes[:2] == [200, 200], codes
    assert 429 in codes[2:], codes


def test_a_guest_and_a_user_do_not_share_a_budget_over_http():
    """Two principals from one address must not spend each other's allowance.

    They do share the address bucket, deliberately -- but that one is sized for
    a shared address, so a single neighbour cannot lock the room out. An
    earlier version reused the per-principal limit for the address key, which
    also meant the same bucket was refilled at the guest rate or the user rate
    depending on who called last.
    """
    env = {**ON, "GUEST_RATE_PER_MIN": "60", "GUEST_RATE_BURST": "1",
           "USER_RATE_PER_MIN": "60", "USER_RATE_BURST": "1"}
    body = {"messages": [{"role": "user", "content": "hi"}]}

    assert _request("/api/chat", headers=GUEST_HEADERS, env=env, json=body).status_code != 429
    assert _request("/api/chat", headers=AUTH_HEADERS, env=env, json=body).status_code != 429

    # Each has now spent their own single token.
    assert _request("/api/chat", headers=GUEST_HEADERS, env=env, json=body).status_code == 429
    assert _request("/api/chat", headers=AUTH_HEADERS, env=env, json=body).status_code == 429


def test_one_address_does_not_lock_out_its_neighbours():
    """The NAT case, stated as a test: many callers behind one address."""
    env = {**ON, "IP_RATE_PER_MIN": "600", "IP_RATE_BURST": "30",
           "USER_RATE_PER_MIN": "600", "USER_RATE_BURST": "5"}
    body = {"messages": [{"role": "user", "content": "hi"}]}

    # Ten distinct principals from the same address, one request each.
    from .conftest import _user_token

    for i in range(10):
        token = _user_token(sub=f"00000000-0000-4000-8000-{i:012d}")
        res = _request(
            "/api/chat",
            headers={"Authorization": f"Bearer {token}"},
            env=env,
            json=body,
        )
        assert res.status_code != 429, f"caller {i} was locked out by a neighbour"


def test_the_limiter_is_off_by_default_in_this_suite_only():
    """conftest disables it session-wide; every test here opts back in.

    Stated as an assertion so the arrangement cannot rot into "the limiter is
    off everywhere and nobody noticed".
    """
    assert os.getenv("RATE_LIMIT_ENABLED") == "false"
    with patch.dict(os.environ, ON):
        assert ratelimit._enabled() is True
