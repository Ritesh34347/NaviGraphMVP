"""Real tests for the gateway's `/lineage` and `/lineage/{trace_id}` real
proxy routes (Phase 15.1, LIMITATIONS.md item 63).

Duplicates `test_ask.py`'s real-RSA-keypair/real-`AzureAdTokenVerifier`
JWT helpers rather than importing them -- this repo's established
per-file test-helper convention (see `test_ask.py`'s own docstring citing
`test_auth_client.py`'s identical precedent). Only the two actual network
calls (JWKS fetch, outbound call to agent-runtime) are faked; the real
verification/proxying logic under test is exercised for real.

NOTE: requires `fastapi` to be installed, exactly like this package's
pre-existing `test_healthz.py`/`test_ask.py`.
"""

from __future__ import annotations

import time

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm
from navigraph_gateway.main import app
from navigraph_shared.auth import AzureAdTokenVerifier

_TENANT_ID = "22222222-2222-2222-2222-222222222222"
_AUDIENCE = "api://navigraph-gateway-test"
_ISSUER = f"https://login.microsoftonline.com/{_TENANT_ID}/v2.0"
_KID = "gateway-lineage-test-kid"


class _FakeJwksClient(jwt.PyJWKClient):
    def __init__(self, jwks: dict) -> None:
        super().__init__("https://example.invalid/discovery/v2.0/keys")
        self._jwks = jwks

    def fetch_data(self) -> dict:
        return self._jwks


def _generate_token(*, exp_delta: float = 3600.0) -> tuple[str, _FakeJwksClient]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    jwk = RSAAlgorithm.to_jwk(public_key, as_dict=True)
    jwk["kid"] = _KID
    jwk["use"] = "sig"
    jwk["alg"] = "RS256"

    now = time.time()
    payload: dict[str, object] = {
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "oid": "real-verified-user",
        "iat": now,
        "nbf": now,
        "exp": now + exp_delta,
    }

    token = jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": _KID})
    return token, _FakeJwksClient({"keys": [jwk]})


def _verifier_for(jwks_client: _FakeJwksClient) -> AzureAdTokenVerifier:
    return AzureAdTokenVerifier(tenant_id=_TENANT_ID, audience=_AUDIENCE, jwks_client=jwks_client)


def _mock_agent_runtime(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="http://agent-runtime-test", transport=httpx.MockTransport(handler)
    )


def test_lineage_search_with_no_verifier_configured_proxies_to_agent_runtime() -> None:
    captured_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(200, json={"tenant_id": "tenant-a", "traces": []})

    with TestClient(app) as client:
        app.state.token_verifier = None
        app.state.http_client = _mock_agent_runtime(handler)

        response = client.get("/lineage", params={"tenant_id": "tenant-a"})

    assert response.status_code == 200
    assert response.json() == {"tenant_id": "tenant-a", "traces": []}
    assert len(captured_requests) == 1
    assert captured_requests[0].url.path == "/lineage"
    assert dict(captured_requests[0].url.params) == {"tenant_id": "tenant-a", "limit": "50", "offset": "0"}


def test_lineage_search_forwards_optional_filters_only_when_given() -> None:
    captured_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(200, json={"tenant_id": "tenant-a", "traces": []})

    with TestClient(app) as client:
        app.state.token_verifier = None
        app.state.http_client = _mock_agent_runtime(handler)

        response = client.get(
            "/lineage",
            params={
                "tenant_id": "tenant-a",
                "agent_name": "query.sql_generation",
                "search_text": "revenue",
                "limit": 10,
                "offset": 5,
            },
        )

    assert response.status_code == 200
    forwarded = dict(captured_requests[0].url.params)
    assert forwarded["agent_name"] == "query.sql_generation"
    assert forwarded["search_text"] == "revenue"
    assert forwarded["limit"] == "10"
    assert forwarded["offset"] == "5"
    assert "since" not in forwarded
    assert "until" not in forwarded


def test_lineage_trace_detail_with_no_verifier_configured_proxies_to_agent_runtime() -> None:
    captured_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(200, json={"trace_id": "trace-1", "tenant_id": "tenant-a", "events": []})

    with TestClient(app) as client:
        app.state.token_verifier = None
        app.state.http_client = _mock_agent_runtime(handler)

        response = client.get("/lineage/trace-1", params={"tenant_id": "tenant-a"})

    assert response.status_code == 200
    assert response.json()["trace_id"] == "trace-1"
    assert captured_requests[0].url.path == "/lineage/trace-1"
    assert dict(captured_requests[0].url.params) == {"tenant_id": "tenant-a"}


def test_lineage_search_requires_a_bearer_token_when_a_verifier_is_configured() -> None:
    _, jwks_client = _generate_token()

    with TestClient(app) as client:
        app.state.token_verifier = _verifier_for(jwks_client)

        response = client.get("/lineage", params={"tenant_id": "tenant-a"})

    assert response.status_code == 401


def test_lineage_trace_detail_requires_a_bearer_token_when_a_verifier_is_configured() -> None:
    _, jwks_client = _generate_token()

    with TestClient(app) as client:
        app.state.token_verifier = _verifier_for(jwks_client)

        response = client.get("/lineage/trace-1", params={"tenant_id": "tenant-a"})

    assert response.status_code == 401


def test_lineage_search_with_an_expired_token_is_rejected() -> None:
    token, jwks_client = _generate_token(exp_delta=-3600.0)

    with TestClient(app) as client:
        app.state.token_verifier = _verifier_for(jwks_client)

        response = client.get(
            "/lineage",
            params={"tenant_id": "tenant-a"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 401


def test_lineage_search_with_a_valid_token_succeeds() -> None:
    token, jwks_client = _generate_token()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"tenant_id": "tenant-a", "traces": []})

    with TestClient(app) as client:
        app.state.token_verifier = _verifier_for(jwks_client)
        app.state.http_client = _mock_agent_runtime(handler)

        response = client.get(
            "/lineage",
            params={"tenant_id": "tenant-a"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200


def test_lineage_search_returns_502_when_agent_runtime_is_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with TestClient(app) as client:
        app.state.token_verifier = None
        app.state.http_client = _mock_agent_runtime(handler)

        response = client.get("/lineage", params={"tenant_id": "tenant-a"})

    assert response.status_code == 502
