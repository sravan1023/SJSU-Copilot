"""
Per-request stage timings.

Each chat request produces one JSON log line ("request timings") holding how
long each stage took, when the key moments happened, and a few counters, keyed
by request_id so a benchmark run or the browser's own timings can be joined to
it.

This uses a ContextVar rather than a request object passed down every call.
web_search is a set of module-level functions with no request handle, and a
ContextVar set in the SSE generator is visible in every task that generator
creates, so stages deep inside retrieval are timed with no extra plumbing.

Units: `stages` are durations in ms (summed if a stage runs more than once,
e.g. one extract per crawled page). `marks` are ms since the request began,
first occurrence wins. Nothing here records message text -- only sizes, counts
and outcomes.
"""
import contextvars
import logging
import time
from contextlib import contextmanager

logger = logging.getLogger("timings")


class _Trace:
    __slots__ = ("request_id", "start", "stages", "marks", "counters")

    def __init__(self, request_id: str):
        self.request_id = request_id
        self.start = time.perf_counter()
        # Seeded so the key is in every line even on a route with no principal
        # dependency. auth.py measures itself onto request.state (it runs before
        # this trace exists) and routers/chat.py adds the real duration here.
        self.stages: dict[str, float] = {"auth_verify": 0.0}
        self.marks: dict[str, float] = {}
        self.counters: dict[str, int | str] = {}


_trace: contextvars.ContextVar[_Trace | None] = contextvars.ContextVar("trace", default=None)


def _ms(seconds: float) -> float:
    return round(seconds * 1000, 1)


def begin(request_id: str) -> _Trace:
    """Start timing a request in the current context.

    Keep the returned trace and pass it to flush(): an abandoned generator can
    be finalised outside the request's context, where the ContextVar is unset.
    """
    trace = _Trace(request_id)
    _trace.set(trace)
    return trace


def current_request_id() -> str | None:
    trace = _trace.get()
    return trace.request_id if trace else None


@contextmanager
def stage(name: str):
    """Time the enclosed block. Works around awaits in async code too."""
    trace = _trace.get()
    if trace is None:
        yield
        return
    started = time.perf_counter()
    try:
        yield
    finally:
        add_stage(name, _ms(time.perf_counter() - started))


def add_stage(name: str, ms: float) -> None:
    """Add a duration measured elsewhere (e.g. in a task's done callback)."""
    trace = _trace.get()
    if trace is not None:
        trace.stages[name] = round(trace.stages.get(name, 0.0) + ms, 1)


def mark(name: str) -> None:
    """Record when something happened, relative to the start of the request."""
    trace = _trace.get()
    if trace is not None and name not in trace.marks:
        trace.marks[name] = _ms(time.perf_counter() - trace.start)


def record(key: str, value: int | str) -> None:
    trace = _trace.get()
    if trace is not None:
        trace.counters[key] = value


def incr(key: str, n: int = 1) -> None:
    trace = _trace.get()
    if trace is not None:
        trace.counters[key] = int(trace.counters.get(key, 0)) + n


def flush(trace: _Trace | None = None, **fields) -> dict | None:
    """Emit the timings line for a request and stop tracking it.

    Call from a `finally:` in the SSE generator, so the line is written on
    success, error, and client disconnect alike. Returns the payload (for tests).
    """
    trace = trace or _trace.get()
    if trace is None:
        return None
    if _trace.get() is trace:
        _trace.set(None)
    payload = {
        "request_id": trace.request_id,
        "total_ms": _ms(time.perf_counter() - trace.start),
        "stages": trace.stages,
        "marks": trace.marks,
        "counters": trace.counters,
        **fields,
    }
    logger.info("request timings", extra=payload)
    return payload
