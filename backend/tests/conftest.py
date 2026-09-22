"""
pytest bridge for the hand-rolled suites.

Most files here predate pytest: each test function calls `_check(label, cond)`,
which counts a failure in the module's FAIL / FAILURES globals instead of
raising. Collected by pytest as-is, a failing check would still show as a
passing test. This hook fails the test whenever its run added to FAILURES, so
`python -m pytest` and `python -m tests.<suite>` agree.
"""
import os
from unittest.mock import patch

import pytest


@pytest.fixture(scope="session", autouse=True)
def _auth_optional_for_pre_auth_suites():
    """Let the suites written before authentication existed keep posting.

    Eleven request sites across test_chat_stream, test_observability,
    test_chat_e2e and test_professors POST with no Authorization header. Rather
    than mint a token at each, they run with AUTH_OPTIONAL=true.

    The enforcing default is what tests/test_auth.py covers: patch.dict nests,
    so its per-test override wins and restores. That file also asserts the
    override actually takes effect -- without which this fixture could hide a
    regression by making an enforcement test pass for the wrong reason.

    Delete this together with the flag in Phase 2.
    """
    with patch.dict(os.environ, {"AUTH_OPTIONAL": "true"}):
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
