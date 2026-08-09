"""Real tests for `/ask`'s JWT-verification wiring (LIMITATIONS.md item 23).

Uses a real RSA keypair and real, PyJWT-signed tokens run through a real
`AzureAdTokenVerifier` -- mirroring
`packages/shared/tests/test_auth_client.py`'s exact convention. Only the
two actual network calls this endpoint makes (the JWKS fetch, and the
outbound HTTP call to agent-runtime) are replaced with in-process fakes
(`httpx.MockTransport`/a `PyJWKClient` subclass overriding `fetch_data()`),
never the crypto/verification logic itself.

NOTE: this file requires `fastapi` to be installed, exactly like this
package's pre-existing `test_healthz.py` -- both are skipped in
environments where it isn't (see this repo's CI/verification commands).
"""

from __future__ import annotations

import json
import time

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm
from navigraph_shared.auth import AzureAdTokenVerifier

from navigraph_gateway.main import app

_TENANT_ID = "22222222-2222-2222-2222-222222222222"
_AUDIENCE = "api://navigraph-gateway-test"
_ISSUER = f"https://login.microsoftonline.com/{_TENANT_ID}/v2.0"
_KID = "gateway-test-kid"


class _FakeJwksClient(jwt.PyJWKClient):
    """A real `PyJWKClient` with only its network fetch replaced -- see
    `test_auth_client.py`'s identical helper for the full rationale."""

    def __init__(self, jwks: dict) -> None:
        super().__init__("https://example.invalid/discovery/v2.0/keys")
        self._jwks = jwks

    def fetch_data(self) -> dict:
        return self._jwks


def _generate_token(
    *, roles: list[str] | None = None, oid: str = "real-verified-user", exp_delta: float = 3600.0
) -> tuple[str, _FakeJwksClient]:
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
        "oid": oid,
        "iat": now,
        "nbf": now,
        "exp": now + exp_delta,
    }
    if roles is not None:
        payload["roles"] = roles

    token = jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": _KID})
    return token, _FakeJwksClient({"keys": [jwk]})


def _verifier_for(jwks_client: _FakeJwksClient) -> AzureAdTokenVerifier:
    return AzureAdTokenVerifier(tenant_id=_TENANT_ID, audience=_AUDIENCE, jwks_client=jwks_client)


def _mock_agent_runtime(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="http://agent-runtime-test", transport=httpx.MockTransport(handler)
    )


def _always_answers(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"result": {"outcome": "answered"}})


def test_ask_with_no_verifier_configured_trusts_the_request_body() -> None:
    """The fallback trust model (no AZURE_AD_TENANT_ID/AUDIENCE configured)
    -- preserves the exact pre-Phase-11 behavior for docker-compose/CI
    environments with no live Entra tenant."""

    with TestClient(app) as client:
        app.state.token_verifier = None
        app.state.http_client = _mock_agent_runtime(_always_answers)

        response = client.post(
            "/ask",
            json={
                "question": "hi",
                "tenant_id": "tenant-a",
                "user_id": "user-a",
                "roles": ["analyst"],
                "claims": {"tenant_id": "tenant-a"},
            },
        )

    assert response.status_code == 200


def test_ask_with_a_verifier_configured_requires_a_bearer_token() -> None:
    _, jwks_client = _generate_token()

    with TestClient(app) as client:
        app.state.token_verifier = _verifier_for(jwks_client)

        response = client.post(
            "/ask", json={"question": "hi", "tenant_id": "tenant-a", "user_id": "user-a"}
        )

    assert response.status_code == 401


def test_ask_with_a_malformed_authorization_header_is_rejected() -> None:
    _, jwks_client = _generate_token()

    with TestClient(app) as client:
        app.state.token_verifier = _verifier_for(jwks_client)

        response = client.post(
            "/ask",
            headers={"Authorization": "NotBearer something"},
            json={"question": "hi", "tenant_id": "tenant-a", "user_id": "user-a"},
        )

    assert response.status_code == 401


def test_ask_with_an_expired_bearer_token_is_rejected() -> None:
    token, jwks_client = _generate_token(exp_delta=-3600.0)

    with TestClient(app) as client:
        app.state.token_verifier = _verifier_for(jwks_client)

        response = client.post(
            "/ask",
            headers={"Authorization": f"Bearer {token}"},
            json={"question": "hi", "tenant_id": "tenant-a", "user_id": "user-a"},
        )

    assert response.status_code == 401


def test_ask_with_a_valid_token_uses_verified_identity_not_the_request_body() -> None:
    """The real security property this whole feature exists for: a caller
    presenting a genuinely valid token cannot override their own identity
    or role by also stuffing different values into the JSON body."""

    token, jwks_client = _generate_token(roles=["analyst"], oid="real-verified-user")

    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"result": {"outcome": "answered"}})

    with TestClient(app) as client:
        app.state.token_verifier = _verifier_for(jwks_client)
        app.state.http_client = _mock_agent_runtime(handler)

        response = client.post(
            "/ask",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "question": "hi",
                "tenant_id": "tenant-a",
                # A caller attempting to self-declare a different identity
                # and an elevated role -- both must be ignored outright.
                "user_id": "someone-the-caller-claims-to-be",
                "roles": ["admin"],
                "claims": {"tenant_id": "attacker-controlled"},
            },
        )

    assert response.status_code == 200
    sent_request_context = captured["body"]["request_context"]
    assert sent_request_context["user_id"] == "real-verified-user"
    assert sent_request_context["roles"] == ["analyst"]
    assert "attacker-controlled" not in json.dumps(sent_request_context["claims"])
