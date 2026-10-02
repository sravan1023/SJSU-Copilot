"""Registration Info read API: terms and deadlines.

Open to guests (`require_principal` inside `rate_limited_scope`): it is all
public campus data. The scope is "reg", the key `ratelimit._SCOPE_DEFAULTS`
uses, so browsing deadlines can't spend the chat budget.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query

import observability
from campus import terms
from ratelimit import rate_limited_scope
from routers.trace import trace_request
from services import registration

logger = logging.getLogger(__name__)
# The trace dependency is listed first so it runs before auth and the rate
# limiter: their warnings then carry the request id.
router = APIRouter(tags=["registration"], dependencies=[Depends(trace_request)])

_reg_limited = Depends(rate_limited_scope("reg"))


def _guard() -> None:
    # Read per call so the rollback is one env change and a restart. Checked
    # after auth and rate limiting: a 503 is for callers who could have been served.
    if not registration.enabled():
        raise HTTPException(status_code=503, detail="Registration info is turned off")


def _unavailable(exc: registration.RegistrationUnavailable) -> HTTPException:
    logger.warning(
        "registration unavailable",
        extra={"request_id": observability.current_request_id(), "reason": str(exc)},
    )
    return HTTPException(status_code=503, detail="Registration info is temporarily unavailable")


@router.get("/registration/terms", dependencies=[_reg_limited])
async def get_terms():
    _guard()
    try:
        return await registration.list_terms()
    except registration.RegistrationUnavailable as exc:
        raise _unavailable(exc)


@router.get("/registration/deadlines", dependencies=[_reg_limited])
async def get_deadlines(term: str | None = Query(default=None, max_length=32)):
    _guard()
    today = registration.pacific_today()
    if term is None:
        resolved = registration.resolve_term("this semester", today)
    else:
        if not terms.TERM_KEY_RE.match(term):
            raise HTTPException(status_code=422, detail="term must look like fall-2026")
        # The page's selector chose this term; no question exists to have "named" it.
        resolved = registration.explicit_term(term)
    observability.record("reg_term_resolution", resolved.kind)
    try:
        body = await registration.term_events(resolved.term, today=today)
    except registration.RegistrationUnavailable as exc:
        raise _unavailable(exc)
    body["how"] = resolved.how
    return body
