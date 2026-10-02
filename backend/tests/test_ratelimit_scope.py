"""F5: scoped rate-limit buckets. Calls the dependency directly; the bucket is
arithmetic and needs no app."""
import os
from unittest.mock import patch

import pytest
from fastapi import HTTPException

import ratelimit
from auth import Principal

ON = {"RATE_LIMIT_ENABLED": "true"}
USER = Principal(kind="user", user_id="11111111-1111-1111-1111-111111111111")
GUEST = Principal(kind="guest", guest_id="a" * 32)


class _Req:
    def __init__(self, host="1.2.3.4"):
        self.client = type("C", (), {"host": host})()


@pytest.fixture(autouse=True)
def _clean():
    ratelimit.clear_rate_limits()
    yield
    ratelimit.clear_rate_limits()


def _calls(dep, principal, host="1.2.3.4", n=100):
    """Number of calls admitted before the first 429."""
    for i in range(n):
        try:
            dep(_Req(host), principal)
        except HTTPException as e:
            assert e.status_code == 429
            return i
    return n


def test_scope_is_isolated_from_chat():
    env = {**ON, "REG_RATE_USER_BURST": "3", "USER_RATE_BURST": "2", "USER_RATE_PER_MIN": "0",
           "REG_RATE_USER_PER_MIN": "0"}
    with patch.dict(os.environ, env):
        reg = ratelimit.rate_limited_scope("reg")
        assert _calls(reg, USER) == 3
        # Registration exhausted; chat bucket for the same user and IP is untouched.
        for _ in range(2):
            ratelimit.rate_limited(_Req(), USER)
        with pytest.raises(HTTPException):
            ratelimit.rate_limited(_Req(), USER)


def test_user_and_guest_limits_differ_and_default_to_60_and_30():
    assert ratelimit.scope_limit_for("reg", "user").per_minute == 60
    assert ratelimit.scope_limit_for("reg", "guest").per_minute == 30
    with patch.dict(os.environ, {**ON, "REG_RATE_USER_BURST": "5", "REG_RATE_GUEST_BURST": "2",
                                 "REG_RATE_USER_PER_MIN": "0", "REG_RATE_GUEST_PER_MIN": "0"}):
        reg = ratelimit.rate_limited_scope("reg")
        assert _calls(reg, USER) == 5
        assert _calls(reg, GUEST, host="9.9.9.9") == 2
    with patch.dict(os.environ, {"REG_RATE_USER_PER_MIN": "12", "REG_RATE_GUEST_PER_MIN": "7"}):
        assert ratelimit.scope_limit_for("reg", "user").per_minute == 12
        assert ratelimit.scope_limit_for("reg", "guest").per_minute == 7


def test_scope_ip_bucket_is_separate_from_chat_ip_bucket():
    env = {**ON, "REG_IP_RATE_BURST": "2", "REG_IP_RATE_PER_MIN": "0", "IP_RATE_BURST": "40"}
    with patch.dict(os.environ, env):
        reg = ratelimit.rate_limited_scope("reg")
        # Different principals, same address: the shared IP bucket trips.
        reg(_Req(), USER)
        reg(_Req(), GUEST)
        with pytest.raises(HTTPException) as e:
            reg(_Req(), Principal(kind="guest", guest_id="b" * 32))
        assert "Retry-After" in e.value.headers
        # Chat's address bucket is a different key and still open.
        ratelimit.rate_limited(_Req(), USER)
        assert "ip:1.2.3.4" in ratelimit._buckets and "reg:ip:1.2.3.4" in ratelimit._buckets


def test_disabled_means_no_limit_and_no_state():
    env = {"RATE_LIMIT_ENABLED": "false", "REG_RATE_USER_BURST": "1", "REG_RATE_USER_PER_MIN": "0"}
    with patch.dict(os.environ, env):
        reg = ratelimit.rate_limited_scope("reg")
        assert _calls(reg, USER, n=50) == 50
    assert ratelimit._buckets == {}
