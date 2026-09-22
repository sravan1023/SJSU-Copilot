"""
pytest bridge for the hand-rolled suites.

Most files here predate pytest: each test function calls `_check(label, cond)`,
which counts a failure in the module's FAIL / FAILURES globals instead of
raising. Collected by pytest as-is, a failing check would still show as a
passing test. This hook fails the test whenever its run added to FAILURES, so
`python -m pytest` and `python -m tests.<suite>` agree.
"""
import pytest


@pytest.hookimpl(wrapper=True)
def pytest_pyfunc_call(pyfuncitem):
    failures = getattr(pyfuncitem.module, "FAILURES", None)
    before = len(failures) if isinstance(failures, list) else None

    result = yield

    if before is not None and len(failures) > before:
        lines = [f"{label}  {detail}".rstrip() for label, detail in failures[before:]]
        raise AssertionError("check failed:\n  " + "\n  ".join(lines))
    return result
