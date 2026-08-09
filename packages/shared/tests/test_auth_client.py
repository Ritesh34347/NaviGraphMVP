"""Real unit tests for `AzureAdTokenVerifier` and `FakeTokenVerifier`.

Mirrors `navigraph_connectors.snowflake`'s established crypto-testing
convention: a REAL RSA keypair is generated with `cryptography`, a REAL
JWT is signed with it via PyJWT, and a REAL JWKS document is built from
the public key -- only the actual network fetch of Azure AD's discovery
endpoint is replaced (by subclassing `jwt.PyJWKClient` and overriding just
`fetch_data()`), so every other real PyJWT code path (signature
verification, `kid` matching, refresh-on-unknown-`kid`, issuer/audience/
expiry checks) is genuinely exercised, not mocked away.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from navigraph_shared.auth.client import (
    AzureAdTokenVerifier,
    FakeTokenVerifier,
    TokenClaims,
    TokenVerificationError,
)

_TENANT_ID = "11111111-1111-1111-1111-111111111111"
_AUDIENCE = "api://navigraph"
_ISSUER = f"https://login.microsoftonline.com/{_TENANT_ID}/v2.0"
_KID = "test-signing-key-1"


class _FakeJwksClient(jwt.PyJWKClient):
    """A real `PyJWKClient` with only its network fetch replaced by a
    canned JWKS document -- every other method (caching, `kid` matching,
    refresh-on-miss) is the real, unmodified PyJWT implementation."""

    def __init__(self, jwks: dict, **kwargs: object) -> None:
        super().__init__("https://example.invalid/discovery/v2.0/keys", **kwargs)
        self._jwks = jwks
        self.fetch_count = 0

    def fetch_data(self) -> dict:
        self.fetch_count += 1
        return self._jwks


def _generate_keypair() -> tuple[object, object]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _jwks_for(public_key: object, *, kid: str = _KID) -> dict:
    jwk = RSAAlgorithm.to_jwk(public_key, as_dict=True)
    jwk["kid"] = kid
    jwk["use"] = "sig"
    jwk["alg"] = "RS256"
    return {"keys": [jwk]}


def _sign(
    private_key: object,
    *,
    kid: str = _KID,
    audience: str = _AUDIENCE,
    issuer: str = _ISSUER,
    subject: str | None = "user-sub-1",
    oid: str | None = "user-oid-1",
    roles: list[str] | None = None,
    algorithm: str = "RS256",
    now: float | None = None,
    exp_delta: float = 3600.0,
    omit_claims: tuple[str, ...] = (),
) -> str:
    now = time.time() if now is None else now
    payload: dict[str, object] = {
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "nbf": now,
        "exp": now + exp_delta,
    }
    if subject is not None:
        payload["sub"] = subject
    if oid is not None:
        payload["oid"] = oid
    if roles is not None:
        payload["roles"] = roles
    for claim in omit_claims:
        payload.pop(claim, None)

    return jwt.encode(payload, private_key, algorithm=algorithm, headers={"kid": kid})


def _verifier(jwks_client: _FakeJwksClient) -> AzureAdTokenVerifier:
    return AzureAdTokenVerifier(
        tenant_id=_TENANT_ID, audience=_AUDIENCE, jwks_client=jwks_client
    )


def test_valid_token_verifies_and_extracts_claims() -> None:
    private_key, public_key = _generate_keypair()
    jwks_client = _FakeJwksClient(_jwks_for(public_key))
    token = _sign(private_key, roles=["analyst", "pii_viewer"])

    claims = _verifier(jwks_client).verify(token)

    assert claims == TokenClaims(
        subject="user-oid-1",
        roles=["analyst", "pii_viewer"],
        raw_claims=claims.raw_claims,
    )
    assert claims.raw_claims["iss"] == _ISSUER
    assert claims.raw_claims["aud"] == _AUDIENCE


def test_subject_falls_back_to_sub_when_oid_is_absent() -> None:
    private_key, public_key = _generate_keypair()
    jwks_client = _FakeJwksClient(_jwks_for(public_key))
    token = _sign(private_key, oid=None, subject="user-sub-only")

    claims = _verifier(jwks_client).verify(token)

    assert claims.subject == "user-sub-only"


def test_missing_subject_and_oid_is_rejected() -> None:
    private_key, public_key = _generate_keypair()
    jwks_client = _FakeJwksClient(_jwks_for(public_key))
    token = _sign(private_key, oid=None, subject=None)

    with pytest.raises(TokenVerificationError, match="oid.*sub"):
        _verifier(jwks_client).verify(token)


def test_no_roles_claim_defaults_to_empty_list() -> None:
    private_key, public_key = _generate_keypair()
    jwks_client = _FakeJwksClient(_jwks_for(public_key))
    token = _sign(private_key, roles=None)

    claims = _verifier(jwks_client).verify(token)

    assert claims.roles == []


def test_expired_token_is_rejected() -> None:
    private_key, public_key = _generate_keypair()
    jwks_client = _FakeJwksClient(_jwks_for(public_key))
    token = _sign(private_key, now=time.time() - 7200, exp_delta=3600)

    with pytest.raises(TokenVerificationError, match="verification failed"):
        _verifier(jwks_client).verify(token)


def test_wrong_audience_is_rejected() -> None:
    private_key, public_key = _generate_keypair()
    jwks_client = _FakeJwksClient(_jwks_for(public_key))
    token = _sign(private_key, audience="api://some-other-app")

    with pytest.raises(TokenVerificationError, match="verification failed"):
        _verifier(jwks_client).verify(token)


def test_wrong_issuer_is_rejected() -> None:
    private_key, public_key = _generate_keypair()
    jwks_client = _FakeJwksClient(_jwks_for(public_key))
    token = _sign(
        private_key,
        issuer="https://login.microsoftonline.com/some-other-tenant/v2.0",
    )

    with pytest.raises(TokenVerificationError, match="verification failed"):
        _verifier(jwks_client).verify(token)


def test_missing_exp_claim_is_rejected_not_treated_as_never_expiring() -> None:
    private_key, public_key = _generate_keypair()
    jwks_client = _FakeJwksClient(_jwks_for(public_key))
    token = _sign(private_key, omit_claims=("exp",))

    with pytest.raises(TokenVerificationError, match="verification failed"):
        _verifier(jwks_client).verify(token)


def test_tampered_signature_is_rejected() -> None:
    """A token re-signed with an attacker's OWN keypair (simulating a
    forged token) must fail signature verification against the real
    tenant's JWKS -- proves the signature is actually checked, not just
    the payload's shape."""

    _, real_public_key = _generate_keypair()
    attacker_private_key, _ = _generate_keypair()
    jwks_client = _FakeJwksClient(_jwks_for(real_public_key))

    forged_token = _sign(attacker_private_key)

    with pytest.raises(TokenVerificationError, match="verification failed"):
        _verifier(jwks_client).verify(forged_token)


def test_tampered_payload_after_signing_is_rejected() -> None:
    """Flipping a single character in a real token's payload segment (a
    role-escalation attempt: analyst -> admin, encoded) must invalidate its
    real signature."""

    private_key, public_key = _generate_keypair()
    jwks_client = _FakeJwksClient(_jwks_for(public_key))
    token = _sign(private_key, roles=["analyst"])

    header_b64, payload_b64, signature_b64 = token.split(".")
    # Flip the payload's last character -- corrupts the base64url content
    # (and therefore the signed bytes) without needing to know its
    # plaintext structure.
    last_char = payload_b64[-1]
    replacement = "A" if last_char != "A" else "B"
    tampered_payload_b64 = payload_b64[:-1] + replacement
    tampered_token = f"{header_b64}.{tampered_payload_b64}.{signature_b64}"

    with pytest.raises(TokenVerificationError, match="verification failed"):
        _verifier(jwks_client).verify(tampered_token)


def test_algorithm_none_is_rejected_even_if_header_claims_it() -> None:
    """The classic JWT "algorithm confusion" attack: a token whose header
    claims `alg: none` (no signature at all) must never be accepted, even
    though PyJWT's `jwt.encode` cooperates in constructing one -- the
    `algorithms=["RS256"]` allowlist passed at verify-time is what actually
    defends against this, not anything about the token itself."""

    private_key, public_key = _generate_keypair()
    jwks_client = _FakeJwksClient(_jwks_for(public_key))

    now = time.time()
    payload = {
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "oid": "user-oid-1",
        "iat": now,
        "nbf": now,
        "exp": now + 3600,
    }
    unsigned_token = jwt.encode(payload, key=None, algorithm="none", headers={"kid": _KID})

    with pytest.raises(TokenVerificationError, match="verification failed"):
        _verifier(jwks_client).verify(unsigned_token)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def test_hmac_confusion_attack_using_the_public_key_as_hmac_secret_is_rejected() -> None:
    """A second classic attack: sign a forged token with HS256, using the
    RSA PUBLIC key's PEM bytes as the HMAC secret (public keys are, by
    definition, publicly known -- if a verifier naively re-used whatever
    `alg` the token's own header claims, this would let an attacker forge
    a token trivially). The `algorithms=["RS256"]` allowlist must reject
    this regardless of what the forged token's header claims.

    Hand-crafted with raw `hmac`/`hashlib` rather than `jwt.encode` --
    PyJWT's own `encode()` refuses outright to use a PEM-shaped key as an
    HMAC secret (a real, useful guard rail), but a real attacker mounting
    this attack would not be going through PyJWT's encode path anyway, so
    this test builds the forged token exactly as such an attacker would."""

    _, public_key = _generate_keypair()
    from cryptography.hazmat.primitives import serialization

    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    jwks_client = _FakeJwksClient(_jwks_for(public_key))

    now = time.time()
    header = {"alg": "HS256", "kid": _KID, "typ": "JWT"}
    payload = {
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "oid": "attacker-oid",
        "roles": ["admin"],
        "iat": now,
        "nbf": now,
        "exp": now + 3600,
    }
    signing_input = f"{_b64url(json.dumps(header).encode())}.{_b64url(json.dumps(payload).encode())}"
    signature = hmac.new(public_pem, signing_input.encode(), hashlib.sha256).digest()
    forged_token = f"{signing_input}.{_b64url(signature)}"

    with pytest.raises(TokenVerificationError, match="verification failed"):
        _verifier(jwks_client).verify(forged_token)


def test_unknown_kid_triggers_a_real_refresh_before_failing() -> None:
    """Simulates real key rotation: the token was signed with a key the
    JWKS client hasn't fetched yet. PyJWKClient's own real (not
    reimplemented) behavior is to refetch once before giving up -- proven
    here by a fake JWKS client that only starts serving the right key
    after its second `fetch_data()` call."""

    private_key, public_key = _generate_keypair()
    decoy_private_key, decoy_public_key = _generate_keypair()
    rotated_kid = "rotated-key-2"
    stale_jwks = _jwks_for(decoy_public_key, kid="stale-key-1")

    class _RotatingJwksClient(_FakeJwksClient):
        def fetch_data(self) -> dict:
            self.fetch_count += 1
            if self.fetch_count == 1:
                # First fetch: stale key set -- a real, still-valid key, but
                # not the one this token was actually signed with.
                return stale_jwks
            return self._jwks

    jwks_client = _RotatingJwksClient(_jwks_for(public_key, kid=rotated_kid))
    token = _sign(private_key, kid=rotated_kid)
    del decoy_private_key  # unused, kept only to document the decoy key's origin

    claims = _verifier(jwks_client).verify(token)

    assert claims.subject == "user-oid-1"
    assert jwks_client.fetch_count == 2


def test_kid_that_never_matches_any_key_is_wrapped_as_a_verification_error() -> None:
    """A `kid` that never matches any key (real rotation exhausted, or a
    forged `kid`) surfaces as a `TokenVerificationError`, not a raw
    `PyJWKClientError` leaking out of this abstraction."""

    _, public_key = _generate_keypair()
    jwks_client = _FakeJwksClient(_jwks_for(public_key))
    private_key_for_unknown_kid, _ = _generate_keypair()
    token = _sign(private_key_for_unknown_kid, kid="never-registered-kid")

    with pytest.raises(TokenVerificationError, match="verification failed"):
        _verifier(jwks_client).verify(token)


def test_constructor_rejects_empty_tenant_id_or_audience() -> None:
    with pytest.raises(ValueError, match="tenant_id"):
        AzureAdTokenVerifier(tenant_id="", audience=_AUDIENCE)

    with pytest.raises(ValueError, match="audience"):
        AzureAdTokenVerifier(tenant_id=_TENANT_ID, audience="")


class TestFakeTokenVerifier:
    def test_returns_canned_claims_and_records_the_call(self) -> None:
        claims = TokenClaims(subject="user-1", roles=["analyst"], raw_claims={})
        verifier = FakeTokenVerifier(claims)

        result = verifier.verify("some-raw-token")

        assert result is claims
        assert verifier.calls == ["some-raw-token"]

    def test_raises_configured_exception(self) -> None:
        verifier = FakeTokenVerifier(raise_exc=TokenVerificationError("expired"))

        with pytest.raises(TokenVerificationError, match="expired"):
            verifier.verify("some-raw-token")

    def test_no_configuration_fails_closed_by_default(self) -> None:
        """Mirrors `FakeOpaClient`'s "no response configured -> deny"
        convention: a test that forgets to configure this double must not
        accidentally exercise an "authentication succeeded" path."""

        verifier = FakeTokenVerifier()

        with pytest.raises(TokenVerificationError):
            verifier.verify("some-raw-token")

    def test_rejects_both_claims_and_raise_exc_configured_together(self) -> None:
        with pytest.raises(ValueError, match="at most one"):
            FakeTokenVerifier(
                TokenClaims(subject="user-1", raw_claims={}),
                raise_exc=TokenVerificationError("x"),
            )
