"""A per-request trace for routes outside the chat stream.

`observability.begin()` is otherwise called only by chat, which leaves `stage`,
`record` and `incr` as no-ops (and `current_request_id()` None, so warnings log
`request_id: null`) everywhere else. Attach `Depends(trace_request)` to a router
to get the same one-line `request timings` JSON that chat emits, so
`bench/report.py` joins it the same way.

The line is written in the dependency's `finally`, so it appears on success and
on an HTTPException alike. Only the route template is added to the chat fields,
never the query string or any caller text.
"""
import re
import uuid
from typing import AsyncIterator

from fastapi import Request

import observability

_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")  # same rule as routers/chat.py


async def trace_request(request: Request) -> AsyncIterator[None]:
    supplied = request.headers.get("x-request-id") or ""
    request_id = supplied if _REQUEST_ID_RE.fullmatch(supplied) else uuid.uuid4().hex
    trace = observability.begin(request_id)
    outcome = "ok"
    try:
        yield
    except BaseException:
        outcome = "error"
        raise
    finally:
        route = request.scope.get("route")
        observability.flush(trace, outcome=outcome, route=getattr(route, "path", None))
