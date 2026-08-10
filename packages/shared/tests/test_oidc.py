"""Unit tests for `HttpOidcTokenVerifier` -- a second, non-Azure
`AzureADTokenVerifier` implementation (Phase 4 of the configurable-
platform build plan).

Same real-cryptography-over-mocked-transport approach as
`test_azure_ad.py`: a real self-signed RSA keypair, a real JWT signed
with it (via `PyJWT`), and `httpx.MockTransport` standing in for the
network -- but here mocking BOTH the discovery document and the JWKS
fetch it points to, since real OIDC discovery is two HTTP calls, not one.
"""

from __future__ import annotations

import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from navigraph_shared.auth.azure_ad import AzureADTokenError, VerifiedIdentity
from navigraph_shared.auth.oidc import HttpOidcTokenVerifier, OidcSettings

_ISSUER = "https://idp.example.com"
_AUDIENCE = "navigraph"
_KID = "test-signing-key"


def _keypair() -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _sign_token(private_key: rsa.RSAPrivateKey, **claim_overrides: object) -> str:
    now = int(time.time())
    claims = {
        "sub": "user-123",
        "tenant_id": "tenant-a",
        "aud": _AUDIENCE,
        "iss": _ISSUER,
        "iat": now,
        "exp": now + 3600,
        "roles": ["analyst"],
    }
    claims.update(claim_overrides)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": _KID})


def _discovery_and_jwks_transport(public_key: rsa.RSAPublicKey) -> httpx.MockTransport:
    jwk = RSAAlgorithm.to_jwk(public_key, as_dict=True)
    jwk["kid"] = _KID
    jwk["use"] = "sig"
    jwk["kty"] = "RSA"

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == f"{_ISSUER}/.well-known/openid-configuration":
            return httpx.Response(200, json={"jwks_uri": f"{_ISSUER}/jwks.json"})
        if url == f"{_ISSUER}/jwks.json":
            return httpx.Response(200, json={"keys": [jwk]})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _verifier(transport: httpx.MockTransport, **settings_overrides: object) -> HttpOidcTokenVerifier:
    settings = OidcSettings(oidc_issuer=_ISSUER, oidc_audience=_AUDIENCE, **settings_overrides)
    return HttpOidcTokenVerifier(settings, transport=transport)


class TestHttpOidcTokenVerifier:
    async def test_verifies_a_real_signed_token_via_real_discovery(self) -> None:
        private_key, public_key = _keypair()
        token = _sign_token(private_key)
        verifier = _verifier(_discovery_and_jwks_transport(public_key))

        identity = await verifier.verify(token)

        assert identity == VerifiedIdentity(
            subject="user-123",
            tenant_id="tenant-a",
            roles=["analyst"],
            raw_claims=identity.raw_claims,
        )

    async def test_missing_roles_claim_is_an_empty_list_not_an_error(self) -> None:
        private_key, public_key = _keypair()
        token = _sign_token(private_key, roles=None)
        verifier = _verifier(_discovery_and_jwks_transport(public_key))

        identity = await verifier.verify(token)

        assert identity.roles == []

    async def test_tenant_id_and_roles_claim_names_are_configurable(self) -> None:
        """A provider that doesn't use this platform's own `tenant_id`/
        `roles` claim vocabulary (e.g. a namespaced Auth0-style claim)
        must still map correctly via the configured claim names."""

        private_key, public_key = _keypair()
        token = _sign_token(
            private_key,
            tenant_id=None,
            roles=None,
            **{"https://navigraph/org_id": "tenant-b", "https://navigraph/roles": ["admin"]},
        )
        verifier = _verifier(
            _discovery_and_jwks_transport(public_key),
            oidc_tenant_id_claim="https://navigraph/org_id",
            oidc_roles_claim="https://navigraph/roles",
        )

        identity = await verifier.verify(token)

        assert identity.tenant_id == "tenant-b"
        assert identity.roles == ["admin"]

    async def test_expired_token_raises(self) -> None:
        private_key, public_key = _keypair()
        now = int(time.time())
        token = _sign_token(private_key, iat=now - 7200, exp=now - 3600)
        verifier = _verifier(_discovery_and_jwks_transport(public_key))

        with pytest.raises(AzureADTokenError, match="token validation failed"):
            await verifier.verify(token)

    async def test_wrong_audience_raises(self) -> None:
        private_key, public_key = _keypair()
        token = _sign_token(private_key, aud="some-other-app")
        verifier = _verifier(_discovery_and_jwks_transport(public_key))

        with pytest.raises(AzureADTokenError, match="token validation failed"):
            await verifier.verify(token)

    async def test_wrong_issuer_raises(self) -> None:
        private_key, public_key = _keypair()
        token = _sign_token(private_key, iss="https://not-the-real-idp.example.com")
        verifier = _verifier(_discovery_and_jwks_transport(public_key))

        with pytest.raises(AzureADTokenError, match="token validation failed"):
            await verifier.verify(token)

    async def test_tampered_signature_raises(self) -> None:
        _private_key, public_key = _keypair()
        other_private_key, _ = _keypair()
        token = _sign_token(other_private_key)
        verifier = _verifier(_discovery_and_jwks_transport(public_key))

        with pytest.raises(AzureADTokenError, match="token validation failed"):
            await verifier.verify(token)

    async def test_unknown_kid_raises(self) -> None:
        private_key, public_key = _keypair()
        token = jwt.encode(
            {
                "sub": "user-123",
                "tenant_id": "tenant-a",
                "aud": _AUDIENCE,
                "iss": _ISSUER,
                "iat": int(time.time()),
                "exp": int(time.time()) + 3600,
            },
            private_key,
            algorithm="RS256",
            headers={"kid": "some-other-kid-not-in-jwks"},
        )
        verifier = _verifier(_discovery_and_jwks_transport(public_key))

        with pytest.raises(AzureADTokenError, match="no JWKS key found"):
            await verifier.verify(token)

    async def test_malformed_token_raises(self) -> None:
        _private_key, public_key = _keypair()
        verifier = _verifier(_discovery_and_jwks_transport(public_key))

        with pytest.raises(AzureADTokenError, match="malformed token header"):
            await verifier.verify("not-a-real-jwt")

    async def test_jwks_response_is_cached_across_calls(self) -> None:
        private_key, public_key = _keypair()
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            url = str(request.url)
            if url == f"{_ISSUER}/.well-known/openid-configuration":
                return httpx.Response(200, json={"jwks_uri": f"{_ISSUER}/jwks.json"})
            jwk = RSAAlgorithm.to_jwk(public_key, as_dict=True)
            jwk["kid"] = _KID
            return httpx.Response(200, json={"keys": [jwk]})

        settings = OidcSettings(oidc_issuer=_ISSUER, oidc_audience=_AUDIENCE)
        verifier = HttpOidcTokenVerifier(settings, transport=httpx.MockTransport(handler))

        await verifier.verify(_sign_token(private_key))
        await verifier.verify(_sign_token(private_key))

        # 2 calls (discovery + jwks) for the FIRST verify only -- the
        # second verify must hit the cache, not re-fetch either document.
        assert call_count == 2
