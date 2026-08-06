"""Unit tests for `HttpAzureADTokenVerifier` and `FakeAzureADTokenVerifier`.

`HttpAzureADTokenVerifier` is tested against a real, self-signed RSA
keypair and a real JWT signed with it (via `PyJWT`) -- the JWKS HTTP
fetch is mocked via `httpx.MockTransport` (the same injection pattern
already used for `HttpOpaClient`'s tests), but the actual signature
verification, issuer/audience/expiry checks, and claim extraction all run
for real against real cryptographic material. This is real, complete,
spec-compliant JWT/JWKS verification -- no live Azure AD tenant is needed
to prove it works correctly; only a live tenant is needed to point it at
one (see LIMITATIONS.md's Azure AD item).
"""

from __future__ import annotations

import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from navigraph_shared.auth.azure_ad import (
    AzureADSettings,
    AzureADTokenError,
    FakeAzureADTokenVerifier,
    HttpAzureADTokenVerifier,
    VerifiedIdentity,
)

_TENANT_ID = "11111111-1111-1111-1111-111111111111"
_CLIENT_ID = "22222222-2222-2222-2222-222222222222"
_KID = "test-signing-key"


def _keypair() -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _sign_token(private_key: rsa.RSAPrivateKey, **claim_overrides: object) -> str:
    now = int(time.time())
    claims = {
        "sub": "user-123",
        "tid": _TENANT_ID,
        "aud": _CLIENT_ID,
        "iss": f"https://login.microsoftonline.com/{_TENANT_ID}/v2.0",
        "iat": now,
        "exp": now + 3600,
        "roles": ["analyst"],
    }
    claims.update(claim_overrides)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": _KID})


def _jwks_transport(public_key: rsa.RSAPublicKey) -> httpx.MockTransport:
    jwk = RSAAlgorithm.to_jwk(public_key, as_dict=True)
    jwk["kid"] = _KID
    jwk["use"] = "sig"
    jwk["kty"] = "RSA"

    def handler(request: httpx.Request) -> httpx.Response:
        assert f"/{_TENANT_ID}/discovery/v2.0/keys" in str(request.url)
        return httpx.Response(200, json={"keys": [jwk]})

    return httpx.MockTransport(handler)


def _verifier(transport: httpx.MockTransport) -> HttpAzureADTokenVerifier:
    settings = AzureADSettings(azure_ad_tenant_id=_TENANT_ID, azure_ad_client_id=_CLIENT_ID)
    return HttpAzureADTokenVerifier(settings, transport=transport)


class TestHttpAzureADTokenVerifier:
    async def test_verifies_a_real_signed_token_and_extracts_identity(self) -> None:
        private_key, public_key = _keypair()
        token = _sign_token(private_key)
        verifier = _verifier(_jwks_transport(public_key))

        identity = await verifier.verify(token)

        assert identity == VerifiedIdentity(
            subject="user-123",
            tenant_id=_TENANT_ID,
            roles=["analyst"],
            raw_claims=identity.raw_claims,
        )
        assert identity.raw_claims["sub"] == "user-123"

    async def test_missing_roles_claim_is_an_empty_list_not_an_error(self) -> None:
        """A real token with no App Roles assigned in the app registration
        carries no `roles` claim at all -- must be a real, valid, empty-role
        identity, never a verification error."""

        private_key, public_key = _keypair()
        token = _sign_token(private_key, roles=None)
        verifier = _verifier(_jwks_transport(public_key))

        identity = await verifier.verify(token)

        assert identity.roles == []

    async def test_expired_token_raises(self) -> None:
        private_key, public_key = _keypair()
        now = int(time.time())
        token = _sign_token(private_key, iat=now - 7200, exp=now - 3600)
        verifier = _verifier(_jwks_transport(public_key))

        with pytest.raises(AzureADTokenError, match="token validation failed"):
            await verifier.verify(token)

    async def test_wrong_audience_raises(self) -> None:
        private_key, public_key = _keypair()
        token = _sign_token(private_key, aud="some-other-app-id")
        verifier = _verifier(_jwks_transport(public_key))

        with pytest.raises(AzureADTokenError, match="token validation failed"):
            await verifier.verify(token)

    async def test_wrong_issuer_raises(self) -> None:
        private_key, public_key = _keypair()
        token = _sign_token(private_key, iss="https://login.microsoftonline.com/wrong-tenant/v2.0")
        verifier = _verifier(_jwks_transport(public_key))

        with pytest.raises(AzureADTokenError, match="token validation failed"):
            await verifier.verify(token)

    async def test_tampered_signature_raises(self) -> None:
        _private_key, public_key = _keypair()
        other_private_key, _ = _keypair()
        # Signed with a DIFFERENT private key than the one whose public
        # key is published in the (mocked) JWKS -- simulates a forged
        # token.
        token = _sign_token(other_private_key)
        verifier = _verifier(_jwks_transport(public_key))

        with pytest.raises(AzureADTokenError, match="token validation failed"):
            await verifier.verify(token)

    async def test_unknown_kid_raises(self) -> None:
        private_key, public_key = _keypair()
        token = jwt.encode(
            {
                "sub": "user-123",
                "tid": _TENANT_ID,
                "aud": _CLIENT_ID,
                "iss": f"https://login.microsoftonline.com/{_TENANT_ID}/v2.0",
                "iat": int(time.time()),
                "exp": int(time.time()) + 3600,
            },
            private_key,
            algorithm="RS256",
            headers={"kid": "some-other-kid-not-in-jwks"},
        )
        verifier = _verifier(_jwks_transport(public_key))

        with pytest.raises(AzureADTokenError, match="no JWKS key found"):
            await verifier.verify(token)

    async def test_malformed_token_raises(self) -> None:
        _private_key, public_key = _keypair()
        verifier = _verifier(_jwks_transport(public_key))

        with pytest.raises(AzureADTokenError, match="malformed token header"):
            await verifier.verify("not-a-real-jwt")

    async def test_jwks_response_is_cached_across_calls(self) -> None:
        private_key, public_key = _keypair()
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            jwk = RSAAlgorithm.to_jwk(public_key, as_dict=True)
            jwk["kid"] = _KID
            return httpx.Response(200, json={"keys": [jwk]})

        settings = AzureADSettings(azure_ad_tenant_id=_TENANT_ID, azure_ad_client_id=_CLIENT_ID)
        verifier = HttpAzureADTokenVerifier(settings, transport=httpx.MockTransport(handler))

        await verifier.verify(_sign_token(private_key))
        await verifier.verify(_sign_token(private_key))

        assert call_count == 1


class TestFakeAzureADTokenVerifier:
    async def test_returns_configured_identity_and_records_calls(self) -> None:
        identity = VerifiedIdentity(subject="user-1", tenant_id="tenant-a", roles=["admin"])
        verifier = FakeAzureADTokenVerifier(identity=identity)

        result = await verifier.verify("some-token")

        assert result == identity
        assert verifier.calls == ["some-token"]

    async def test_raises_configured_exception(self) -> None:
        verifier = FakeAzureADTokenVerifier(raise_exc=AzureADTokenError("expired"))

        with pytest.raises(AzureADTokenError, match="expired"):
            await verifier.verify("some-token")

    async def test_unconfigured_verifier_raises(self) -> None:
        verifier = FakeAzureADTokenVerifier()

        with pytest.raises(AzureADTokenError, match="not configured"):
            await verifier.verify("some-token")

    def test_rejects_more_than_one_configured_behavior(self) -> None:
        with pytest.raises(ValueError, match="at most one"):
            FakeAzureADTokenVerifier(
                identity=VerifiedIdentity(subject="u", tenant_id="t"),
                raise_exc=AzureADTokenError("x"),
            )
