"""Guest sessions.

A visitor with no SJSU account can use the assistant. They need an identity the
server issued and can verify, because §4.3 is right that a client-supplied raw
guest id must never be trusted -- it would be a free-form string the caller
picks, which is not an identity at all.

So this mints a short-lived token signed with a secret the backend owns. What
it deliberately is *not*:

* **Not a Supabase anonymous sign-in.** That creates a real `auth.users` row
  per visitor, which fires `handle_new_user()` into a NOT NULL `profiles.email`
  and then grants the visitor full own-row RLS access -- the opposite of
  ephemeral, and three permanent rows per curious passer-by.
* **Not refreshable.** A guest past the TTL gets 401 and the client mints a new
  session. That *is* the "refresh ends the session" requirement rather than a
  gap in it, and a refresh endpoint would quietly turn a 2-hour identity into a
  permanent one.
* **Not a bearer of any authority.** The token says only "some visitor". Every
  endpoint that writes a user-owned row is behind `require_user`, which answers
  a guest with 403.

**This route is rate-limited by address**, without which it is a free
token-minting oracle: a caller who can mint identities at will defeats the
per-principal bucket by taking a fresh one for every request.
"""
import logging
import time
import uuid

import jwt
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import ratelimit
from auth import (
    GUEST_AUDIENCE,
    GUEST_ISSUER,
    GUEST_KID,
    GUEST_SUB_PREFIX,
    GUEST_TTL_SECONDS,
    guest_secret,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["guest"])


class GuestSession(BaseModel):
    token: str
    expires_in: int
    guest_id: str


@router.post("/guest/session", response_model=GuestSession)
async def create_guest_session(request: Request) -> GuestSession:
    secret = guest_secret()
    if not secret:
        # Fails closed and says so at the right volume: without the secret no
        # guest token could verify anyway, so minting one would hand out
        # credentials that are already dead.
        logger.error("guest session requested but GUEST_JWT_SECRET is not set")
        raise HTTPException(status_code=503, detail="guest sessions are unavailable")

    ratelimit.check_ip(request, ratelimit.ip_session_limit())

    guest_id = uuid.uuid4().hex
    now = int(time.time())
    claims = {
        "sub": f"{GUEST_SUB_PREFIX}{guest_id}",
        "aud": GUEST_AUDIENCE,
        "iss": GUEST_ISSUER,
        "iat": now,
        "exp": now + GUEST_TTL_SECONDS,
    }
    token = jwt.encode(claims, secret, algorithm="HS256", headers={"kid": GUEST_KID})

    # The id, not the token. A log is not a place to put a credential.
    logger.info("guest session issued", extra={"guest_id": guest_id})

    return GuestSession(token=token, expires_in=GUEST_TTL_SECONDS, guest_id=guest_id)
