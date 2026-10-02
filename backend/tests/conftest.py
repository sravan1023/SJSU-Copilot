"""
pytest bridge for the hand-rolled suites, and the credentials they run with.

Most files here predate pytest: each test function calls `_check(label, cond)`,
which counts a failure in the module's FAIL / FAILURES globals instead of
raising. Collected by pytest as-is, a failing check would still show as a
passing test. The hook below fails the test whenever its run added to FAILURES,
so `python -m pytest` and `python -m tests.<suite>` agree.

**Credentials.** Eighteen request sites across test_chat_stream,
test_observability, test_chat_e2e and test_professors POST to gated routes.
They used to run under `AUTH_OPTIONAL=true`, a flag that let a request with no
Authorization header through. That flag is gone: once guest principals exist,
"admit an unauthenticated caller" is a privilege escalation rather than a
convenience, and it was a footgun besides -- a test of the enforcing path could
pass for the wrong reason.

They now carry a real, verified token, minted here and signed with a secret
that exists only in this process. `AUTH_HEADERS` is a signed-in *user*, which
is what those suites were always pretending to be; `/api/professors` is behind
`require_user` and would answer a guest with 403. `GUEST_HEADERS` is available
for anything that wants the other side.

Nothing here weakens a gate: an endpoint that rejects these headers rejects
them for a real reason, which is the property AUTH_OPTIONAL could not offer.
"""
import os
import time
from unittest.mock import patch

import jwt
import pytest

# Secrets that exist only for this process. Not read from the environment, so a
# developer's real .env can never be what the suite runs against.
JWT_SECRET = "conftest-hs256-secret-not-a-real-key-0123456789abcdef"
GUEST_SECRET = "conftest-guest-secret-not-a-real-key-0123456789abcdef"
SUPABASE_URL = "https://conftest.supabase.co"
ISSUER = f"{SUPABASE_URL}/auth/v1"
USER_ID = "00000000-0000-4000-8000-00000000f00d"

# Applied for the whole session. Individual tests override with their own
# patch.dict, which nests and restores.
SESSION_ENV = {
    "SUPABASE_URL": SUPABASE_URL,
    "SUPABASE_JWT_SECRET": JWT_SECRET,
    "SUPABASE_JWT_AUD": "authenticated",
    "GUEST_JWT_SECRET": GUEST_SECRET,
    # These suites drive dozens of in-process requests back to back. Budgets
    # are a separate concern with their own tests; leaving them on here would
    # surface as unrelated 429s. tests/test_ratelimit.py turns them back on.
    "RATE_LIMIT_ENABLED": "false",
}


def _user_token(**overrides):
    now = int(time.time())
    claims = {
        "sub": USER_ID,
        "aud": "authenticated",
        "iss": ISSUER,
        "exp": now + 3600,
        "iat": now,
        "email": "suite@sjsu.edu",
        "role": "authenticated",
        **overrides,
    }
    return jwt.encode(claims, JWT_SECRET, algorithm="HS256")


def _guest_token(**overrides):
    # Mirrors routers/guest.py. Duplicated rather than imported so a bug in the
    # minting endpoint cannot quietly make these suites agree with it.
    now = int(time.time())
    claims = {
        "sub": "guest:0123456789abcdef0123456789abcdef",
        "aud": "guest",
        "iss": "sjsu-copilot",
        "exp": now + 3600,
        "iat": now,
        **overrides,
    }
    return jwt.encode(claims, GUEST_SECRET, algorithm="HS256", headers={"kid": "guest-v1"})


AUTH_HEADERS = {"Authorization": f"Bearer {_user_token()}"}
GUEST_HEADERS = {"Authorization": f"Bearer {_guest_token()}"}


@pytest.fixture(scope="session", autouse=True)
def _session_credentials():
    """Make the tokens above verifiable for the whole session."""
    with patch.dict(os.environ, SESSION_ENV):
        yield


@pytest.hookimpl(wrapper=True)
def pytest_pyfunc_call(pyfuncitem):
    failures = getattr(pyfuncitem.module, "FAILURES", None)
    before = len(failures) if isinstance(failures, list) else None

    result = yield

    if before is not None and len(failures) > before:
        lines = [f"{label}  {detail}".rstrip() for label, detail in failures[before:]]
        raise AssertionError("check failed:\n  " + "\n  ".join(lines))
    return result
