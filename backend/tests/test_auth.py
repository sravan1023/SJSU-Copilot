"""
Authentication and capability checks, through the real routers.

Tokens are signed here rather than fetched: HS256 with a test secret, and ES256
with a P-256 key generated per session whose public half is handed to a stubbed
JWKS client. Only key *transport* is stubbed -- every assertion below runs real
PyJWT verification, so a signature, audience, issuer or expiry failure is a real
one. Nothing here touches the network.

Run from backend/ with:
    python -m pytest tests/test_auth.py
"""
import asyncio
import base64
import json
import logging
import os
import time
from unittest.mock import patch

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

import auth
import main
import runtime

PROJECT = "https://test-project.supabase.co"
ISSUER = f"{PROJECT}/auth/v1"
HS_SECRET = "test-jwt-secret-not-a-real-one"
GUEST_SECRET = "test-guest-secret-not-a-real-one"
ALICE = "11111111-1111-1111-1111-111111111111"

ENV = {
    "SUPABASE_URL": PROJECT,
    "SUPABASE_SERVICE_KEY": "test-service-key",
    "SUPABASE_JWT_AUD": "authenticated",
    "GUEST_JWT_SECRET": GUEST_SECRET,
    # Budgets have their own suite. Leaving them on here would turn the dozens
    # of in-process requests below into unrelated 429s.
    "RATE_LIMIT_ENABLED": "false",
    # main.py's load_dotenv() pulls the real key out of backend/.env. Most tests
    # here expect a 401 before the handler runs, so nothing should reach Groq --
    # but if a regression ever let one through, it must not bill the account.
    # Tests that do want a stream override this and mock the endpoint.
    "GROQ_API_KEY": "test-key-not-real",
}

CHAT_BODY = {"messages": [{"role": "user", "content": "hi"}]}


# ── Token minting ─────────────────────────────────────────────────────────────


def _claims(**overrides):
    now = int(time.time())
    claims = {
        "sub": ALICE,
        "aud": "authenticated",
        "iss": ISSUER,
        "exp": now + 3600,
        "iat": now,
        "email": "alice@sjsu.edu",
        "role": "authenticated",
    }
    claims.update(overrides)
    return claims


def _hs256(**overrides):
    return jwt.encode(_claims(**overrides), HS_SECRET, algorithm="HS256")


_ec_key = None


def _ec_keypair():
    """One P-256 key per session; keygen is fast but not free."""
    global _ec_key
    if _ec_key is None:
        _ec_key = ec.generate_private_key(ec.SECP256R1())
    return _ec_key


def _b64(value: int) -> str:
    return base64.urlsafe_b64encode(value.to_bytes(32, "big")).rstrip(b"=").decode()


def _public_jwk():
    numbers = _ec_keypair().public_key().public_numbers()
    return {
        "kty": "EC",
        "crv": "P-256",
        "x": _b64(numbers.x),
        "y": _b64(numbers.y),
        "alg": "ES256",
        "use": "sig",
        "kid": "test-key-1",
    }


def _es256(**overrides):
    pem = _ec_keypair().private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return jwt.encode(
        _claims(**overrides), pem, algorithm="ES256", headers={"kid": "test-key-1"}
    )


def _guest(secret=GUEST_SECRET, kid="guest-v1", alg="HS256", **overrides):
    """A guest token, minted the way routers/guest.py does.

    Written out here rather than imported from the router so a bug in the
    minting endpoint cannot make these tests agree with it.
    """
    now = int(time.time())
    claims = {
        "sub": "guest:" + "a" * 32,
        "aud": "guest",
        "iss": "sjsu-copilot",
        "exp": now + 3600,
        "iat": now,
    }
    claims.update(overrides)
    headers = {"kid": kid} if kid else {}
    return jwt.encode(claims, secret, algorithm=alg, headers=headers)


class _StubJWKS:
    """Stands in for jwt.PyJWKClient, serving the locally generated key."""

    def __init__(self):
        self.calls = 0

    def get_signing_key_from_jwt(self, token):
        self.calls += 1
        return jwt.PyJWK.from_dict(_public_jwk())

    def get_signing_keys(self):
        return [jwt.PyJWK.from_dict(_public_jwk())]


# ── Drivers ───────────────────────────────────────────────────────────────────


def _request(method, path, token=None, env=None, jwks=None, **kwargs):
    """Drive the real app in-process and return the response."""
    headers = kwargs.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"

    async def go():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, headers=headers, **kwargs)

    auth.clear_grant_cache()
    with patch.dict(os.environ, {**ENV, **(env or {})}), \
         patch.object(runtime, "_jwks_client", jwks):
        return asyncio.run(go())


def _chat(token=None, env=None, jwks=None):
    return _request("POST", "/api/chat", token=token, env=env, jwks=jwks, json=CHAT_BODY)


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


# ── 1. The unauthenticated cases ──────────────────────────────────────────────


def test_no_header_is_401_with_a_challenge():
    res = _chat()
    assert res.status_code == 401
    assert res.json()["detail"] == "authentication required"
    # 401 + challenge, not FastAPI's default 403 for a missing bearer: the
    # client has to be able to tell "sign in" from "you may not do this".
    assert res.headers["www-authenticate"] == "Bearer"


def test_the_session_credentials_can_be_overridden():
    """Guards the session fixture in conftest.py.

    That fixture sets a signing secret and a token for the whole session so the
    pre-auth suites keep working. If a per-test override ever stopped taking
    effect, every enforcement test here would pass for the wrong reason --
    which is the failure mode the old AUTH_OPTIONAL fixture actually had.
    """
    from .conftest import GUEST_SECRET as SESSION_GUEST_SECRET

    assert os.getenv("GUEST_JWT_SECRET") == SESSION_GUEST_SECRET
    with patch.dict(os.environ, {"GUEST_JWT_SECRET": "something-else"}):
        assert auth.guest_secret() == "something-else"
    assert auth.guest_secret() == SESSION_GUEST_SECRET


def test_a_missing_header_is_never_forgiven():
    """There is no mode in which an unauthenticated request is served.

    AUTH_OPTIONAL used to provide one, for local development and the bench
    harness. Both now mint a real guest token instead, and the flag is gone:
    once a guest principal is a legitimate caller, a switch that admits an
    unauthenticated one is an escalation rather than a convenience.
    """
    assert "AUTH_OPTIONAL" not in os.environ
    assert not hasattr(auth, "auth_optional")
    assert not hasattr(auth, "ANONYMOUS")

    for env in ({}, {"AUTH_OPTIONAL": "true"}):
        res = _chat(env=env)
        assert res.status_code == 401, f"no header must be 401 even with {env}"


# ── 2. Token verification ─────────────────────────────────────────────────────


def _hs_env(**extra):
    return {"SUPABASE_JWT_SECRET": HS_SECRET, **extra}


def test_forged_signature_is_rejected():
    forged = jwt.encode(_claims(), "the-wrong-secret", algorithm="HS256")
    res = _chat(token=forged, env=_hs_env())
    assert res.status_code == 401
    # Collapsed to a generic message: the endpoint must not say which part of a
    # forged token was wrong.
    assert res.json()["detail"] == "invalid token"


def test_expired_token_says_so():
    token = _hs256(exp=int(time.time()) - 60)
    res = _chat(token=token, env=_hs_env())
    assert res.status_code == 401
    # The one distinguished 401: the right client response is refresh-and-retry,
    # not sign-out, and expiry is not secret from the token's holder.
    assert res.json()["detail"] == "token expired"


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "some-other-audience"},
        {"iss": "https://evil.example.com/auth/v1"},
        {"role": "service_role"},
        {"role": "anon"},
    ],
    ids=["wrong-aud", "wrong-iss", "service-role", "anon-role"],
)
def test_claims_are_checked(overrides):
    res = _chat(token=_hs256(**overrides), env=_hs_env())
    assert res.status_code == 401


@pytest.mark.parametrize("missing", ["exp", "sub", "aud", "iss"])
def test_a_token_missing_a_required_claim_is_rejected(missing):
    """Absence has to fail, not just a wrong value.

    A token with no `exp` would otherwise never expire. This is what
    options={"require": [...]} buys, and without it only `sub` was covered
    (by the explicit check in _decode).
    """
    claims = _claims()
    claims.pop(missing)
    token = jwt.encode(claims, HS_SECRET, algorithm="HS256")
    assert _chat(token=token, env=_hs_env()).status_code == 401


def test_the_legacy_service_key_shape_is_not_a_session():
    """backend/.env holds a legacy HS256 service key. It is not a user."""
    token = jwt.encode(
        {"iss": "supabase", "role": "service_role", "exp": int(time.time()) + 3600},
        HS_SECRET,
        algorithm="HS256",
    )
    assert _chat(token=token, env=_hs_env()).status_code == 401


def test_alg_none_and_unsupported_algs_are_rejected():
    unsigned = jwt.encode(_claims(), key="", algorithm="none")
    assert _chat(token=unsigned, env=_hs_env()).status_code == 401
    assert _chat(token=_hs256(), env=_hs_env()).status_code != 401  # control: HS256 works

    hs512 = jwt.encode(_claims(), HS_SECRET, algorithm="HS512")
    assert _chat(token=hs512, env=_hs_env()).status_code == 401


def test_hs256_is_refused_when_no_secret_is_configured():
    assert _chat(token=_hs256(), env={"SUPABASE_JWT_SECRET": ""}).status_code == 401


def test_algorithm_confusion_is_impossible():
    """An ES256 public key replayed as an HMAC secret must not verify.

    This is why the algorithm is dispatched into two disjoint branches instead
    of being passed through into `algorithms=`.

    The token is assembled by hand: jwt.encode refuses to HMAC-sign with a PEM
    ("The specified key is an asymmetric key ... and should not be used as an
    HMAC secret"). That guard is PyJWT's, on the *signing* side, and an attacker
    would not be using PyJWT. What is under test is this server's verification.
    """
    import hashlib
    import hmac

    public_pem = (
        _ec_keypair()
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )

    def segment(payload: dict) -> bytes:
        raw = json.dumps(payload, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    signing_input = b".".join(
        [segment({"alg": "HS256", "typ": "JWT", "kid": "test-key-1"}), segment(_claims())]
    )
    signature = base64.urlsafe_b64encode(
        hmac.new(public_pem, signing_input, hashlib.sha256).digest()
    ).rstrip(b"=")
    confused = (signing_input + b"." + signature).decode()

    assert _chat(token=confused, env={"SUPABASE_JWT_SECRET": ""}).status_code == 401
    assert _chat(token=confused, env=_hs_env(), jwks=_StubJWKS()).status_code == 401


def test_es256_via_jwks_is_accepted():
    with patch("routers.chat.build_rag_prompt", _no_rag), \
         patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}), \
         respx.mock(assert_all_called=False) as mock:
        from services import llm

        mock.post(llm.GROQ_API_URL).mock(return_value=_groq_ok())
        res = _chat(token=_es256(), jwks=_StubJWKS())
    assert res.status_code == 200


def test_es256_without_a_jwks_client_is_503_not_401():
    """An outage must not look like a bad token, or clients sign users out."""
    res = _chat(token=_es256(), jwks=None)
    assert res.status_code == 503
    assert res.json()["detail"] == "token verification unavailable"


# ── 3. The gated surface ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path,body",
    [
        ("/api/chat", CHAT_BODY),
        ("/api/generate-title", {"message": "hello"}),
        ("/api/auto-behavior", CHAT_BODY),
        ("/api/professors", {"message": "who teaches CS 151"}),
    ],
)
def test_every_gated_endpoint_rejects_anonymous(path, body):
    assert _request("POST", path, json=body).status_code == 401


def test_telemetry_stays_open():
    """Posted with keepalive on pagehide, routinely after sign-out."""
    res = _request(
        "POST",
        "/api/telemetry",
        json={"events": [{"kind": "send", "outcome": "ok", "marks": {"ack": 3.0}}]},
    )
    assert res.status_code == 204


def test_health_stays_open():
    assert _request("GET", "/").status_code == 200


# ── 4. Capabilities ───────────────────────────────────────────────────────────


def _grants_route(mock, rows, status=200):
    return mock.get(f"{PROJECT}/rest/v1/admin_grants").mock(
        return_value=httpx.Response(status, json=rows)
    )


def _jobs(token, jwks, mock_rows=None, status=200):
    with respx.mock(assert_all_called=False) as mock, \
         patch("routers.jobs.run_job_fetch_cycle", _fake_job_cycle):
        route = _grants_route(mock, mock_rows if mock_rows is not None else [], status)
        res = _request("POST", "/api/jobs/fetch", token=token, jwks=jwks, json={})
        return res, route


async def _fake_job_cycle(query=None):
    return {"ok": True, "inserted": 0}


def test_capability_missing_is_403():
    res, _ = _jobs(_es256(), _StubJWKS(), mock_rows=[])
    assert res.status_code == 403
    assert res.json()["detail"] == "missing capability: run_jobs"


def test_capability_present_runs_the_handler():
    res, _ = _jobs(
        _es256(), _StubJWKS(), mock_rows=[{"capability": "run_jobs", "expires_at": None}]
    )
    assert res.status_code == 200
    assert res.json()["ok"] is True


def test_expired_grant_does_not_count():
    res, _ = _jobs(
        _es256(),
        _StubJWKS(),
        mock_rows=[{"capability": "run_jobs", "expires_at": "2020-01-01T00:00:00+00:00"}],
    )
    assert res.status_code == 403


def test_capability_refuses_a_guest_with_403():
    """A guest is authenticated, just not entitled -- and never can be.

    Grants are seeded by hand against an account, so there is no user_id to
    look one up against. Same 403 as require_user, for the same reason: 401
    would tell a client holding a valid token to authenticate and retry.
    """
    with respx.mock(assert_all_called=False) as mock:
        _grants_route(mock, [])
        res = _request("POST", "/api/jobs/fetch", token=_guest(), json={})
    assert res.status_code == 403


def test_postgrest_failure_fails_closed_and_is_not_cached():
    """503, not 403 -- an outage is distinguishable from a missing grant."""
    auth.clear_grant_cache()
    jwks = _StubJWKS()
    token = _es256()

    async def go(client, headers):
        return await client.post("/api/jobs/fetch", headers=headers, json={})

    with respx.mock(assert_all_called=False) as mock, \
         patch.dict(os.environ, ENV), \
         patch.object(runtime, "_jwks_client", jwks):
        route = mock.get(f"{PROJECT}/rest/v1/admin_grants").mock(
            side_effect=[
                httpx.Response(500, json={"message": "boom"}),
                httpx.Response(200, json=[{"capability": "run_jobs", "expires_at": None}]),
            ]
        )

        async def drive():
            transport = httpx.ASGITransport(app=main.app)
            headers = {"Authorization": f"Bearer {token}"}
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                first = await go(c, headers)
                with patch("routers.jobs.run_job_fetch_cycle", _fake_job_cycle):
                    second = await go(c, headers)
                return first, second

        first, second = asyncio.run(drive())

    assert first.status_code == 503
    assert first.json()["detail"] == "capability check unavailable"
    # The failure was not cached, so the retry really re-queried.
    assert second.status_code == 200
    assert route.call_count == 2


def test_grants_are_cached_for_the_ttl():
    auth.clear_grant_cache()
    jwks = _StubJWKS()
    token = _es256()

    with respx.mock(assert_all_called=False) as mock, \
         patch.dict(os.environ, ENV), \
         patch.object(runtime, "_jwks_client", jwks), \
         patch("routers.jobs.run_job_fetch_cycle", _fake_job_cycle):
        route = _grants_route(mock, [{"capability": "run_jobs", "expires_at": None}])

        async def drive():
            transport = httpx.ASGITransport(app=main.app)
            headers = {"Authorization": f"Bearer {token}"}
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                await c.post("/api/jobs/fetch", headers=headers, json={})
                await c.post("/api/jobs/fetch", headers=headers, json={})
                assert route.call_count == 1, "second call should hit the cache"

                # Expire the entry rather than sleeping 60s.
                expiry, caps = auth._grant_cache[ALICE]
                auth._grant_cache[ALICE] = (time.monotonic() - 1, caps)
                await c.post("/api/jobs/fetch", headers=headers, json={})

        asyncio.run(drive())

    assert route.call_count == 2


def test_active_grant_filtering():
    """_active is pure, so the expiry rules are checked without a database."""
    far_future = "2099-01-01T00:00:00+00:00"
    rows = [
        {"capability": "never_expires", "expires_at": None},
        {"capability": "still_valid", "expires_at": far_future},
        {"capability": "lapsed", "expires_at": "2020-01-01T00:00:00+00:00"},
        {"capability": "unparseable", "expires_at": "not-a-timestamp"},
        {"capability": None, "expires_at": None},
    ]
    assert auth._active(rows) == frozenset({"never_expires", "still_valid"})


# ── 4b. Guest sessions ────────────────────────────────────────────────────────


def _mint():
    """Drive POST /api/guest/session and return the response."""
    return _request("POST", "/api/guest/session", json=None)


def test_guest_session_mints_a_token_that_verifies():
    """The round trip, which neither half proves alone."""
    res = _mint()
    assert res.status_code == 200
    body = res.json()
    assert body["expires_in"] == auth.GUEST_TTL_SECONDS
    assert body["guest_id"] and "guest:" not in body["guest_id"]
    # The credential must not be echoed into a log line anywhere; the endpoint
    # logs the id only. Here we only care that the token verifies.
    with patch("routers.chat.build_rag_prompt", _no_rag),          respx.mock(assert_all_called=False) as mock:
        from services import llm

        mock.post(llm.GROQ_API_URL).mock(return_value=_groq_ok())
        assert _chat(token=body["token"]).status_code == 200


def test_guest_session_is_unavailable_without_a_secret():
    """Fails closed: minting a token no one could verify helps nobody."""
    res = _request("POST", "/api/guest/session", env={"GUEST_JWT_SECRET": ""}, json=None)
    assert res.status_code == 503


def test_a_guest_may_use_the_chat_path():
    with patch("routers.chat.build_rag_prompt", _no_rag),          respx.mock(assert_all_called=False) as mock:
        from services import llm

        mock.post(llm.GROQ_API_URL).mock(return_value=_groq_ok())
        assert _chat(token=_guest()).status_code == 200


@pytest.mark.parametrize(
    "path,body",
    [
        ("/api/generate-title", {"message": "hello"}),
        ("/api/auto-behavior", CHAT_BODY),
    ],
)
def test_a_guest_may_use_the_compute_helpers(path, body):
    """Pure compute over what the caller supplied; no user-owned row involved.

    A guest's conversation is ephemeral, but it still wants a title in the tab
    and a sensible default tone, and neither endpoint touches a stored row.
    """
    with respx.mock(assert_all_called=False) as mock:
        from services import llm

        # generate-title posts stream: False and reads .json(); auto-behavior
        # never reaches the provider at all.
        mock.post(llm.GROQ_API_URL).mock(return_value=_groq_json())
        assert _request("POST", path, token=_guest(), json=body).status_code == 200


def test_a_guest_is_refused_an_account_only_endpoint_with_403():
    """**The distinction this phase exists to make.**

    401 would tell a client holding a valid, unexpired guest token to
    authenticate and retry -- which re-presents the same token and fails
    identically, forever. 403 is the true statement and the one the UI can act
    on by offering a sign-in.
    """
    res = _request(
        "POST", "/api/professors", token=_guest(), json={"message": "who teaches CS 151"}
    )
    assert res.status_code == 403
    # Not a challenge: there is nothing to re-authenticate as.
    assert "www-authenticate" not in res.headers


def test_no_credentials_is_still_401_not_403():
    """The other half of the same distinction, so neither collapses into it."""
    res = _request("POST", "/api/professors", json={"message": "x"})
    assert res.status_code == 401
    assert res.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "label,token_kwargs",
    [
        ("expired", {"exp": 1}),
        ("wrong audience", {"aud": "authenticated"}),
        ("wrong issuer", {"iss": ISSUER}),
        ("forged signature", {"secret": "not-the-guest-secret"}),
        ("subject outside the guest namespace", {"sub": ALICE}),
        ("alg=none", {"alg": "none", "secret": ""}),
    ],
)
def test_a_bad_guest_token_is_rejected(label, token_kwargs):
    assert _chat(token=_guest(**token_kwargs)).status_code == 401, label


def test_the_two_token_families_cannot_be_swapped():
    """Disjoint on key, issuer and audience -- any one of the three is enough.

    A Supabase session replayed as a guest, and a guest replayed as a session.
    Neither can work, and neither should depend on the secret alone.
    """
    # A real user token carrying the guest routing hint: the kid sends it to
    # the guest branch, where the key, issuer and audience all disagree.
    user_with_guest_kid = jwt.encode(
        _claims(), HS_SECRET, algorithm="HS256", headers={"kid": "guest-v1"}
    )
    assert _chat(token=user_with_guest_kid, env=_hs_env()).status_code == 401

    # A guest token with the kid stripped falls into the Supabase HS256 branch,
    # where its issuer and audience are wrong.
    assert _chat(token=_guest(kid=None), env=_hs_env()).status_code == 401

    # And a guest token signed with the *Supabase* secret still fails, because
    # the guest branch verifies against GUEST_JWT_SECRET.
    assert _chat(token=_guest(secret=HS_SECRET)).status_code == 401


def test_a_session_may_not_claim_a_guest_subject():
    """Closes the namespace: `sub` decides which principal the app sees."""
    token = _hs256(sub="guest:" + "b" * 32)
    assert _chat(token=token, env=_hs_env()).status_code == 401


def test_the_guest_route_is_deliberately_open():
    """Like /api/telemetry, and recorded here so it is a decision, not a gap."""
    assert _mint().status_code == 200


# ── 5. Observability ──────────────────────────────────────────────────────────


def test_startup_states_the_auth_posture():
    """The log should always say which way the server is running."""

    def posture(env):
        capture = _Capture()
        logging.getLogger("runtime").addHandler(capture)

        async def go():
            async with runtime.lifespan(None):
                pass

        try:
            with patch.dict(os.environ, {**ENV, **env}):
                asyncio.run(go())
        finally:
            logging.getLogger("runtime").removeHandler(capture)
        return capture.records

    enforced = [r for r in posture({}) if r.getMessage() == "auth enforced"]
    assert len(enforced) == 1
    assert enforced[0].auth_optional is False

    # guest_sessions is on the line because its failure mode is otherwise
    # invisible: with no secret, POST /api/guest/session answers 503 and no
    # visitor can use the app while every signed-in path keeps working.
    assert enforced[0].guest_sessions is True

    without = [
        r for r in posture({"GUEST_JWT_SECRET": ""})
        if r.getMessage() == "auth enforced"
    ]
    assert without[0].guest_sessions is False


def test_the_timing_line_carries_the_principal():
    capture = _Capture()
    logging.getLogger("timings").addHandler(capture)
    try:
        with patch("routers.chat.build_rag_prompt", _no_rag), \
             patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}), \
             respx.mock(assert_all_called=False) as mock:
            from services import llm

            mock.post(llm.GROQ_API_URL).mock(return_value=_groq_ok())
            assert _chat(token=_es256(), jwks=_StubJWKS()).status_code == 200
        line = next(r for r in capture.records if r.getMessage() == "request timings")
    finally:
        logging.getLogger("timings").removeHandler(capture)

    assert line.counters["principal"] == "user"
    # Deliberately not `> 0`: an HS256 decode can take under 0.05ms and the
    # timing rounds to one decimal, so a positive assertion would be flaky.
    assert "auth_verify" in line.stages


# ── Shared stubs ──────────────────────────────────────────────────────────────


async def _no_rag(messages, audience=None):
    return "", []


def _groq_json(content="A Short Title"):
    """Non-streaming completion, as generate_title issues (stream: False)."""
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": content}}]},
    )


def _groq_ok():
    body = (
        f"data: {json.dumps({'choices': [{'delta': {'content': 'ok'}}]})}\n\n"
        "data: [DONE]\n\n"
    ).encode()
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)
