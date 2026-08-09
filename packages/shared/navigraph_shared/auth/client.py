"""Real Azure AD (Entra ID) JWT verification.

`TokenVerifier` is the abstract base the gateway's HTTP boundary codes
against -- mirroring `navigraph_shared.opa.client`'s and
`navigraph_shared.secrets.client`'s exact ABC/real/fake triad:

- `AzureAdTokenVerifier` -- a REAL implementation: verifies a bearer
  token's signature against the tenant's real JWKS endpoint, and its
  issuer/audience/expiry, via PyJWT. Never returns a "maybe valid" result
  -- either fully verified `TokenClaims` or a raised `TokenVerificationError`.
- `FakeTokenVerifier` -- a no-crypto, no-network test double that returns
  canned claims (or raises, to simulate an invalid/expired/malformed
  token) and records every call made to it.

Resolves LIMITATIONS.md item 23: `RequestContext.roles`/`claims` were
previously always caller-supplied, with no cryptographic verification of
the caller's claimed identity at all. See `navigraph_gateway.main`'s `/ask`
handler for where this is actually wired into the one real HTTP trust
boundary this platform exposes to end users.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TokenVerificationError(Exception):
    """Raised for ANY reason a bearer token could not be verified.

    Deliberately a single, flat exception type rather than a hierarchy of
    "expired" vs "bad signature" vs "wrong audience" subclasses -- callers
    (the gateway's `/ask` handler) only ever need to do one thing on
    failure (reject the request, HTTP 401), never react differently by
    failure reason. The real reason is preserved in the exception's
    message (and its `__cause__`, chained from the underlying `PyJWTError`)
    for logging, not for branching.
    """


class TokenClaims(BaseModel):
    """Verified claims extracted from a real, cryptographically-checked
    bearer token.

    `subject` is the token's stable identity claim (`oid` if present --
    Azure AD's immutable per-user object ID, stable across every app
    registration -- else `sub`). `roles` is the token's own `roles` claim
    verbatim (the Entra app registration's assigned App Roles for this
    user) -- NOT filtered or mapped against NaviGraph's own role vocabulary
    here; that check belongs to OPA's Rego policy
    (`infra/opa/policies/authz.rego`'s `role_allowed`), same as it already
    does for caller-supplied roles today. `raw_claims` is the full decoded
    JWT payload, so a caller can read any other claim (e.g. a real
    tenant-identifying claim, once one is configured in the Entra app
    registration -- see LIMITATIONS.md item 23) without this class needing
    to enumerate every claim Azure AD might ever emit.
    """

    model_config = ConfigDict(extra="forbid")

    subject: str
    roles: list[str] = Field(default_factory=list)
    raw_claims: dict[str, Any] = Field(default_factory=dict)


class TokenVerifier(ABC):
    """Abstract base for verifying a bearer token and extracting its claims."""

    @abstractmethod
    def verify(self, token: str) -> TokenClaims:
        """Verify `token` is a real, currently-valid, correctly-issued
        token, returning its verified claims.

        Raises:
            TokenVerificationError: on ANY failure -- bad/missing/tampered
                signature, expired, wrong audience/issuer, malformed,
                disallowed algorithm, unknown signing key, or a missing
                subject claim. There is no partial-trust return value: a
                caller either gets fully verified claims or an exception,
                never a "maybe valid" result to reason about -- mirrors
                `Connector.test_connection`'s fail-closed philosophy
                applied to identity instead of connectivity.
        """
        raise NotImplementedError


class AzureAdTokenVerifier(TokenVerifier):
    """Real implementation: verifies a real Azure AD (Entra ID) v2.0 access
    token against the tenant's real JWKS endpoint.

    Only `RS256` is ever accepted (passed explicitly as PyJWT's
    `algorithms` allowlist) -- this is what defeats the classic JWT
    "algorithm confusion" attack, where a token's own (attacker-controlled)
    header claims `alg: none` or `alg: HS256` (using the RSA public key,
    which is public by design, as an HMAC secret) to bypass signature
    verification entirely. `exp`/`iat`/`aud`/`iss` are all required to be
    PRESENT (`options={"require": [...]}`), not merely valid-if-present --
    a token missing `exp` entirely must not be treated as "never expires".

    `jwks_client` defaults to a real `jwt.PyJWKClient` hitting the tenant's
    real discovery endpoint (`https://login.microsoftonline.com/{tenant_id}
    /discovery/v2.0/keys`), which already caches the fetched key set
    (default 5-minute lifespan) and automatically retries once against a
    freshly-fetched key set if a token's `kid` isn't found in the cached
    one (e.g. after Azure AD rotates its signing keys) -- both real PyJWT
    behaviors, not reimplemented here. Tests inject a fake object exposing
    the same `get_signing_key_from_jwt(token) -> PyJWK` interface, built
    from a real, locally-generated RSA keypair -- mirroring
    `navigraph_connectors.snowflake`'s established "mock only the network
    call, never the crypto library" testing convention.
    """

    def __init__(
        self,
        *,
        tenant_id: str,
        audience: str,
        issuer: str | None = None,
        leeway_seconds: float = 60.0,
        jwks_client: Any | None = None,
    ) -> None:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        if not audience:
            raise ValueError("audience is required")

        self._audience = audience
        self._issuer = issuer or f"https://login.microsoftonline.com/{tenant_id}/v2.0"
        self._leeway_seconds = leeway_seconds

        if jwks_client is not None:
            self._jwks_client = jwks_client
        else:
            # Imported lazily, mirroring SnowflakeConnector/AzureKeyVault
            # SecretsProvider's established lazy-driver-import convention --
            # importing this module should never require PyJWT to be
            # installed unless this class is actually instantiated.
            import jwt

            jwks_uri = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
            self._jwks_client = jwt.PyJWKClient(jwks_uri)

    def verify(self, token: str) -> TokenClaims:
        import jwt

        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway_seconds,
                options={"require": ["exp", "iat", "aud", "iss"]},
            )
        except jwt.PyJWTError as exc:
            raise TokenVerificationError(f"token verification failed: {exc}") from exc

        subject = payload.get("oid") or payload.get("sub")
        if not subject:
            raise TokenVerificationError("token has neither an 'oid' nor a 'sub' claim")

        roles = payload.get("roles", [])
        if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
            raise TokenVerificationError("token 'roles' claim must be a list of strings")

        return TokenClaims(subject=str(subject), roles=list(roles), raw_claims=payload)


class FakeTokenVerifier(TokenVerifier):
    """No-crypto, no-network test double for `TokenVerifier`.

    Construct with a fixed `claims` (a `TokenClaims`, returned on every
    call) or `raise_exc` (raised on every call, to simulate an invalid,
    expired, or malformed token). Every call is recorded in `self.calls`
    (the raw token string, exactly as received), mirroring
    `FakeOpaClient`/`FakeSecretsProvider`'s identical call-recording
    convention.

    Passing neither raises `TokenVerificationError` on every call, matching
    `FakeOpaClient`'s "no canned response configured -> deny" fail-closed
    default -- a test that forgets to configure this double must not
    accidentally exercise an "authentication succeeded" path.
    """

    def __init__(
        self, claims: TokenClaims | None = None, *, raise_exc: Exception | None = None
    ) -> None:
        if claims is not None and raise_exc is not None:
            raise ValueError("pass at most one of claims, raise_exc")

        self._claims = claims
        self._raise_exc = raise_exc
        self.calls: list[str] = []

    def verify(self, token: str) -> TokenClaims:
        self.calls.append(token)

        if self._raise_exc is not None:
            raise self._raise_exc
        if self._claims is not None:
            return self._claims

        raise TokenVerificationError("no canned claims/exception configured on FakeTokenVerifier")
