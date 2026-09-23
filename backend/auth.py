"""Identity and authority for the API.

Two separate questions, deliberately kept apart:

* **Who is this?** A Supabase access token or a guest token, verified here.
  `get_principal` returns a `Principal`; `require_principal` admits either,
  `require_user` requires a real account and answers a guest with 403.
* **What may they do?** Read from `public.admin_grants` via PostgREST with the
  service role. `require_capability(cap)` gates an endpoint on one grant.

Authority never comes from the token and never from `profiles.role`. The role
column has been client-writable since day one, so no value in it is trustworthy
-- that is the whole reason `admin_grants` exists as a separate, server-written
table (supabase/migrations/20260917000200_profiles_privilege_guard.sql).

Grants are fetched lazily, only by `require_capability`, and are NOT carried on
`Principal`. Putting them there would add a PostgREST round trip to every
/api/chat, which is exactly the class of serial wait the latency work removed.

**Why the server verifies the token itself** rather than calling
`supabase.auth.getUser(token)`: that would add a network round trip to Supabase
on every message. Verification against the project's published keys is local and
costs microseconds.

Env is read per call, not captured at import. The rest of the backend uses
import-time constants (services/job_fetcher.py:21-24), but the test suite has no
monkeypatch fixture and overrides env with `patch.dict(os.environ, ...)`, which
only works for call-time reads.
"""
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

import httpx
import jwt
from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import runtime

logger = logging.getLogger(__name__)

# auto_error=False is load-bearing: with auto_error=True, FastAPI answers a
# missing header with 403, which tells the client "you may not do this" when the
# truth is "you are not signed in". We want 401 + WWW-Authenticate so the client
# can tell those apart.
_bearer = HTTPBearer(auto_error=False)

_UNAUTHENTICATED = {"WWW-Authenticate": "Bearer"}

# Tokens issued to the anon key or the service role are not user sessions. The
# issuer check already rejects the service key in backend/.env (its `iss` is
# "supabase", not the project's auth URL), but if anyone ever points
# SUPABASE_JWT_SECRET at the project's JWT secret, this is the second wall.
_NON_USER_ROLES = {"anon", "service_role"}

_ASYMMETRIC = ("ES256", "RS256")

# ── Guest sessions ────────────────────────────────────────────────────────────
#
# A guest token is minted by POST /api/guest/session and signed with a secret
# this backend owns. It is disjoint from a Supabase session on three axes at
# once -- signing key, issuer and audience -- so neither can be replayed as the
# other, and a failure of any one check is enough.
#
# Supabase native anonymous sign-in was considered and rejected: it creates a
# real auth.users row per guest, which fires handle_new_user() (001:41-52) into
# a NOT NULL profiles.email (001:15), and would then hand the guest full
# own-row RLS access -- the opposite of ephemeral.
GUEST_KID = "guest-v1"
GUEST_ISSUER = "sjsu-copilot"
GUEST_AUDIENCE = "guest"
GUEST_SUB_PREFIX = "guest:"
GUEST_TTL_SECONDS = 2 * 60 * 60


@dataclass(frozen=True)
class Principal:
    """The verified caller.

    Two kinds, and the difference is authority rather than how they were
    checked. A guest holds a real, verified, unexpired token and is a
    legitimate caller of the chat endpoints; they simply have no account, so
    anything that writes a user-owned row is closed to them. That is a 403, not
    a 401 -- see require_user.
    """

    kind: Literal["user", "guest"]
    user_id: str | None = None
    guest_id: str | None = None
    email: str | None = None
    claims: dict = field(default_factory=dict)

    @property
    def rate_key(self) -> str:
        """Stable per-caller key for rate limiting; cannot collide across kinds."""
        if self.kind == "user":
            return f"user:{self.user_id}"
        return f"{GUEST_SUB_PREFIX}{self.guest_id}"


def guest_secret() -> str:
    """HMAC key for guest tokens. Empty means guest sessions are unavailable."""
    return os.getenv("GUEST_JWT_SECRET", "").strip()


def _supabase_url() -> str:
    return os.getenv("SUPABASE_URL", "").rstrip("/")


def _invalid(reason: str, **extra) -> HTTPException:
    """401 for any bad token, with the specific reason logged but not returned.

    The caller is told only "invalid token" so the endpoint is not an oracle for
    which part of a forged token was wrong.
    """
    logger.warning("token rejected", extra={"reason": reason, **extra})
    return HTTPException(status_code=401, detail="invalid token", headers=_UNAUTHENTICATED)


def _verify(token: str, *, key, algorithms, audience, issuer, options, alg) -> dict:
    """jwt.decode with this project's error vocabulary.

    `algorithms` is always a literal list supplied by the caller's branch, never
    anything read off the token.
    """
    try:
        return jwt.decode(
            token,
            key,
            algorithms=algorithms,
            audience=audience,
            issuer=issuer,
            options=options,
        )
    except jwt.ExpiredSignatureError as exc:
        # The only distinguished 401. Expiry is not secret -- the client holds
        # the token and can read `exp` -- and it is the one failure where the
        # right client behaviour is refresh-and-retry rather than sign-out. For
        # a guest, "retry" means minting a fresh session, which is why there is
        # no guest refresh endpoint.
        logger.info("token expired")
        raise HTTPException(
            status_code=401, detail="token expired", headers=_UNAUTHENTICATED
        ) from exc
    except jwt.PyJWTError as exc:
        raise _invalid("verification failed", alg=alg, error=type(exc).__name__) from exc


def _decode(token: str) -> dict:
    """Verify an access token -- Supabase session or guest -- and return its claims.

    The header is read unverified and used only to **route** into one of three
    disjoint branches. The token's own `alg` is never passed into `algorithms=`:
    every branch supplies a fixed literal list, which is what makes algorithm
    confusion impossible, where an RS256/ES256 public key is replayed as an
    HMAC secret.

    Routing on an unverified `kid` is not a trust decision. It selects which
    key to try; the signature, `aud`, `iss` and `exp` are then all verified
    against that branch's own values, so a token that lies about its `kid`
    simply fails. The JWKS branch already routes this way --
    `get_signing_key_from_jwt` reads the unverified `kid` to pick a public key.
    """
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise _invalid("malformed header", error=type(exc).__name__) from exc

    alg = header.get("alg")
    options = {"require": ["exp", "sub", "aud", "iss"]}

    # Guest branch first, and self-contained: it needs no SUPABASE_URL, so a
    # guest session keeps working if the Supabase config is absent.
    if header.get("kid") == GUEST_KID:
        # Asserted by name rather than relying on PyJWT's default, so a
        # `kid=guest-v1, alg=none` token is refused for the stated reason.
        if alg != "HS256":
            raise _invalid("guest token must be HS256", alg=str(alg))
        key = guest_secret()
        if not key:
            raise _invalid("guest token but GUEST_JWT_SECRET is not configured")
        claims = _verify(
            token,
            key=key,
            algorithms=["HS256"],
            audience=GUEST_AUDIENCE,
            issuer=GUEST_ISSUER,
            options=options,
            alg=alg,
        )
        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub.startswith(GUEST_SUB_PREFIX):
            raise _invalid("guest token subject is not a guest id")
        return claims

    url = _supabase_url()
    if not url:
        logger.error("SUPABASE_URL is not set; cannot verify tokens")
        raise HTTPException(status_code=503, detail="token verification unavailable")

    audience = os.getenv("SUPABASE_JWT_AUD", "authenticated")
    issuer = f"{url}/auth/v1"

    if alg in _ASYMMETRIC:
        client = runtime.get_jwks_client()
        if client is None:
            logger.error("no JWKS client; is SUPABASE_URL set and the lifespan running?")
            raise HTTPException(status_code=503, detail="token verification unavailable")
        try:
            # PyJWKClient uses synchronous urllib. This runs inside a sync
            # helper called from a thread (see verify_token), so the loop is
            # never blocked by a cache miss.
            key = client.get_signing_key_from_jwt(token).key
        except jwt.PyJWTError as exc:
            raise _invalid("no signing key for kid", alg=alg, error=type(exc).__name__) from exc
        except Exception as exc:
            logger.error(
                "JWKS fetch failed", extra={"error": type(exc).__name__}, exc_info=True
            )
            raise HTTPException(
                status_code=503, detail="token verification unavailable"
            ) from exc
        algorithms = list(_ASYMMETRIC)

    elif alg == "HS256":
        # This project's JWKS holds one ES256 key, so an HS256 user token is
        # anomalous. Still supported: the project is mid-migration and still
        # carries a legacy HS256 service key.
        key = os.getenv("SUPABASE_JWT_SECRET", "")
        if not key:
            raise _invalid("HS256 token but no SUPABASE_JWT_SECRET configured", alg=alg)
        algorithms = ["HS256"]

    else:
        # Includes "none".
        raise _invalid("unsupported algorithm", alg=str(alg))

    claims = _verify(
        token,
        key=key,
        algorithms=algorithms,
        audience=audience,
        issuer=issuer,
        options=options,
        alg=alg,
    )

    if claims.get("role") in _NON_USER_ROLES:
        raise _invalid("not a user session", role=claims.get("role"))

    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise _invalid("no subject")

    # A Supabase session whose subject looked like a guest id would be
    # indistinguishable from one downstream. GoTrue issues uuids so this cannot
    # happen today, but the failure would be silent and the check is one line.
    if sub.startswith(GUEST_SUB_PREFIX):
        raise _invalid("session subject collides with the guest namespace")

    if alg == "HS256":
        logger.warning("HS256 token accepted", extra={"sub": sub})

    return claims


async def get_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> Principal:
    """Verify the bearer token, or fail closed.

    Records its own duration on `request.state.auth_verify_ms`. It cannot write
    to observability directly: a dependency runs before the handler, and
    routers/chat.py calls observability.begin() inside the stream generator, so
    the trace ContextVar is still unset here. The chat route transfers it.
    """
    started = time.perf_counter()
    try:
        if credentials is None or not (credentials.credentials or "").strip():
            logger.info("unauthenticated request", extra={"path": request.url.path})
            raise HTTPException(
                status_code=401, detail="authentication required", headers=_UNAUTHENTICATED
            )

        # Verification is CPU work plus, on a JWKS cache miss, a blocking HTTP
        # fetch. Both belong off the event loop.
        claims = await runtime.run_blocking(_decode, credentials.credentials)

        # The kind comes from the **verified** issuer, never from the `kid`
        # header that routed the decode. The routing hint must not be able to
        # become the identity.
        if claims.get("iss") == GUEST_ISSUER:
            return Principal(
                kind="guest",
                guest_id=claims["sub"][len(GUEST_SUB_PREFIX):],
                claims=claims,
            )

        return Principal(
            kind="user",
            user_id=claims["sub"],
            email=claims.get("email"),
            claims=claims,
        )
    finally:
        request.state.auth_verify_ms = round((time.perf_counter() - started) * 1000, 1)


def require_principal(principal: Principal = Depends(get_principal)) -> Principal:
    """Any verified caller: a signed-in user or a guest.

    For endpoints a guest is meant to use -- the chat path and the two pure
    compute helpers beside it. There is nothing extra to check here; arriving
    with a Principal at all means the token verified.
    """
    return principal


def require_user(principal: Principal = Depends(get_principal)) -> Principal:
    """Require a real account. A guest gets 403, not 401.

    This distinction is the whole point. 401 means "I do not know who you are;
    authenticate and retry", and a client holding a valid guest token would do
    exactly that -- re-presenting the same token, failing identically, forever.
    403 means "I know who you are and this is not for you", which is the true
    statement and the one the UI can act on by offering a sign-in.

    Same three-way split require_capability already uses: 401 for no
    credentials, 403 for credentials that are not enough, 503 for cannot tell.
    """
    if principal.kind != "user":
        raise HTTPException(
            status_code=403, detail="this requires a signed-in account"
        )
    return principal


# ── Capabilities ──────────────────────────────────────────────────────────────

_GRANT_TTL_S = 60.0
_GRANT_CACHE_MAX = 512

# sub -> (monotonic expiry, capabilities). Successes only, including the empty
# set; a failure is never cached, so a Supabase blip is not pinned for a minute.
_grant_cache: dict[str, tuple[float, frozenset[str]]] = {}


def clear_grant_cache() -> None:
    """Drop every cached grant. For tests; nothing in the app calls this."""
    _grant_cache.clear()


def _unavailable(reason: str, **extra) -> HTTPException:
    logger.error("capability check unavailable", extra={"reason": reason, **extra})
    return HTTPException(status_code=503, detail="capability check unavailable")


def _active(rows: list[dict]) -> frozenset[str]:
    """Capabilities whose grant has not expired.

    The expiry predicate is applied here rather than in the PostgREST query: the
    `or=(expires_at.is.null,expires_at.gt.<iso>)` form is easy to get subtly
    wrong, a user has a handful of grants, and this is unit-testable with no
    database. An unparseable timestamp counts as expired.
    """
    now = datetime.now(timezone.utc)
    active = set()
    for row in rows:
        capability = row.get("capability")
        if not capability:
            continue
        expires_at = row.get("expires_at")
        if expires_at:
            try:
                if datetime.fromisoformat(expires_at) <= now:
                    continue
            except (TypeError, ValueError):
                logger.warning(
                    "grant has an unparseable expires_at; treating as expired",
                    extra={"capability": capability},
                )
                continue
        active.add(capability)
    return frozenset(active)


async def _fetch_grants(user_id: str) -> frozenset[str]:
    """Read one user's grants from PostgREST with the service role."""
    url = _supabase_url()
    key = os.getenv("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        raise _unavailable("SUPABASE_URL or SUPABASE_SERVICE_KEY is not set")

    shared = runtime.get_supabase_client()
    try:
        if shared is not None:
            response = await shared.get(
                f"{url}/rest/v1/admin_grants",
                params={"select": "capability,expires_at", "user_id": f"eq.{user_id}"},
                headers={
                    "apikey": key,
                    "Authorization": f"Bearer {key}",
                    "Accept": "application/json",
                },
            )
        else:
            # No lifespan (ASGITransport tests, scripts). Same fallback shape as
            # services/llm.py's provider client.
            async with httpx.AsyncClient(timeout=3.0, follow_redirects=False) as client:
                response = await client.get(
                    f"{url}/rest/v1/admin_grants",
                    params={"select": "capability,expires_at", "user_id": f"eq.{user_id}"},
                    headers={
                        "apikey": key,
                        "Authorization": f"Bearer {key}",
                        "Accept": "application/json",
                    },
                )
    except httpx.HTTPError as exc:
        raise _unavailable("PostgREST unreachable", error=type(exc).__name__) from exc

    # Checked explicitly rather than with raise_for_status, so the message stays
    # ours. A missing admin_grants table arrives here as a 404.
    if response.status_code >= 400:
        raise _unavailable("PostgREST error", status=response.status_code)

    try:
        rows = response.json()
    except ValueError as exc:
        raise _unavailable("PostgREST returned non-JSON") from exc

    if not isinstance(rows, list):
        raise _unavailable("PostgREST returned an unexpected shape")

    return _active(rows)


async def get_capabilities(user_id: str) -> frozenset[str]:
    """This user's active capabilities, cached for 60s.

    Grants are seeded by hand, so a change takes up to a minute to take effect.
    """
    cached = _grant_cache.get(user_id)
    now = time.monotonic()
    if cached is not None and cached[0] > now:
        return cached[1]

    capabilities = await _fetch_grants(user_id)

    if len(_grant_cache) >= _GRANT_CACHE_MAX and user_id not in _grant_cache:
        _grant_cache.pop(min(_grant_cache, key=lambda k: _grant_cache[k][0]), None)
    _grant_cache[user_id] = (now + _GRANT_TTL_S, capabilities)
    return capabilities


def require_capability(capability: str) -> Callable[..., Awaitable[Principal]]:
    """Gate an endpoint on one grant in public.admin_grants.

    **Fails closed**, as a 503, when the grant cannot be read. The only
    endpoints behind this drive write pipelines with the service role, so
    failing open would mean "Supabase is down, therefore any signed-in user may
    run them" -- the exact escalation this phase exists to close. Neither is on
    a user-visible path, so the cost of failing closed is close to zero.

    503 rather than 403 so an outage is distinguishable from a missing grant.
    """

    async def dependency(principal: Principal = Depends(get_principal)) -> Principal:
        # A guest has no user_id to look a grant up against, and never will --
        # grants are seeded by hand against an account. Same 403 as
        # require_user, for the same reason: they are authenticated, just not
        # entitled.
        if principal.kind != "user":
            raise HTTPException(
                status_code=403, detail="this requires a signed-in account"
            )
        granted = await get_capabilities(principal.user_id)
        if capability not in granted:
            logger.warning(
                "capability denied",
                extra={"sub": principal.user_id, "capability": capability},
            )
            raise HTTPException(status_code=403, detail=f"missing capability: {capability}")
        return principal

    return dependency
