"""Azure AD (Entra ID) bearer-token verification.

Closes the gap logged in LIMITATIONS.md's Azure AD token verification
item: `RequestContext.roles`/`claims` have always been caller-supplied,
never cryptographically verified -- a caller could self-declare `admin`.
This module builds the REAL, generic, fully-tested verification mechanism
(RS256 JWT signature check via the tenant's real JWKS, issuer/audience
validation, `roles`/`tid` claim extraction) -- but it is NOT wired to any
live tenant yet. `AzureADSettings.azure_ad_enabled` defaults to `False`
specifically so nothing about `/ask`'s or `/mcp`'s existing behavior
changes until a real Azure AD app registration (tenant_id, client_id) is
provided and this flag is flipped on -- the same "build now, wire later"
discipline this session already used for Snowflake/Anthropic/Azure
credentials: never fabricate a tenant, never fake a token, but the
mechanism itself must be real and provably correct today.

Follows the exact ABC/real/fake triad already established by
`navigraph_shared.llm.client` (`LLMClient`/`AnthropicLLMClient`/
`FakeLLMClient`) and `navigraph_shared.opa.client`
(`OpaClient`/`HttpOpaClient`/`FakeOpaClient`) -- same constructor shape
(`settings` + an optional `transport` injection point for tests), same
"at most one configured behavior" rule for the fake.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field

from navigraph_shared.auth.registry import register_verifier
from navigraph_shared.config import NaviGraphSettings


class AzureADSettings(NaviGraphSettings):
    """Settings for Azure AD token verification.

    `azure_ad_enabled` is `False` by default -- see this module's
    docstring for why. `azure_ad_tenant_id`/`azure_ad_client_id` are the
    real Azure AD app registration's tenant/client (application) IDs,
    required only once `azure_ad_enabled=True`.
    """

    azure_ad_enabled: bool = False
    azure_ad_tenant_id: str = ""
    azure_ad_client_id: str = ""


class VerifiedIdentity(BaseModel):
    """The result of successfully verifying a bearer token -- everything a
    caller needs to populate a trustworthy `RequestContext`."""

    model_config = ConfigDict(extra="forbid")

    subject: str
    tenant_id: str
    roles: list[str] = Field(default_factory=list)
    raw_claims: dict[str, Any] = Field(default_factory=dict)


class AzureADTokenError(Exception):
    """Raised by `AzureADTokenVerifier.verify()` on any validation failure
    -- expired token, bad signature, wrong audience/issuer, malformed
    token, or an unreachable JWKS endpoint. Callers should treat this the
    same way `PolicyAuthorizationAgent` already treats an unreachable OPA:
    fail closed, never treat it as an implicit allow."""


class AzureADTokenVerifier(ABC):
    """Verifies an Azure AD bearer token and extracts a trustworthy
    identity from it."""

    @abstractmethod
    async def verify(self, bearer_token: str) -> VerifiedIdentity:
        """Verify `bearer_token`'s signature, issuer, audience, and
        expiry, and return the identity it asserts.

        Raises `AzureADTokenError` on any failure -- never returns a
        partially-trusted or best-effort identity.
        """
        raise NotImplementedError


class HttpAzureADTokenVerifier(AzureADTokenVerifier):
    """Real implementation: fetches the tenant's real JWKS over HTTPS,
    validates the token's RS256 signature against the matching key (by
    `kid`), and checks issuer/audience/expiry via `PyJWT`.

    The JWKS response is cached in-memory with a TTL (`jwks_cache_ttl_seconds`,
    default 1 hour -- Azure AD's own signing keys rotate infrequently and
    publish well in advance of rotation, so this is a safe, real cache, not
    a correctness risk) rather than re-fetched on every single token
    verification. `httpx`/`PyJWT` are imported lazily inside `verify()`,
    mirroring `HttpOpaClient`'s/`AnthropicLLMClient`'s exact lazy-import
    pattern -- importing this module, or exercising tests that mock the
    transport entirely, never requires either package to be importable at
    module-load time (though both are real, declared dependencies).
    """

    def __init__(
        self,
        settings: AzureADSettings | None = None,
        *,
        transport: Any | None = None,
        jwks_cache_ttl_seconds: float = 3600.0,
    ) -> None:
        self._settings = settings or AzureADSettings()
        self._transport = transport
        self._jwks_cache_ttl_seconds = jwks_cache_ttl_seconds
        self._jwks_cache: dict[str, Any] | None = None
        self._jwks_cache_expires_at: float = 0.0

    async def _get_jwks(self) -> dict[str, Any]:
        import httpx

        now = time.monotonic()
        if self._jwks_cache is not None and now < self._jwks_cache_expires_at:
            return self._jwks_cache

        url = (
            f"https://login.microsoftonline.com/"
            f"{self._settings.azure_ad_tenant_id}/discovery/v2.0/keys"
        )
        async with httpx.AsyncClient(timeout=10.0, transport=self._transport) as client:
            response = await client.get(url)
            response.raise_for_status()
            jwks: dict[str, Any] = response.json()

        self._jwks_cache = jwks
        self._jwks_cache_expires_at = now + self._jwks_cache_ttl_seconds
        return jwks

    async def verify(self, bearer_token: str) -> VerifiedIdentity:
        import jwt
        from cryptography.hazmat.primitives.asymmetric import rsa
        from jwt.algorithms import RSAAlgorithm

        try:
            unverified_header = jwt.get_unverified_header(bearer_token)
        except jwt.PyJWTError as exc:
            raise AzureADTokenError(f"malformed token header: {exc}") from exc

        kid = unverified_header.get("kid")

        try:
            jwks = await self._get_jwks()
        except Exception as exc:
            raise AzureADTokenError(f"could not fetch JWKS: {exc}") from exc

        matching_jwk = next(
            (key for key in jwks.get("keys", []) if key.get("kid") == kid),
            None,
        )
        if matching_jwk is None:
            raise AzureADTokenError(f"no JWKS key found for kid={kid!r}")

        # A JWKS only ever publishes PUBLIC keys -- `from_jwk`'s return type
        # is a broader union (it also handles private-key JWKs, which never
        # appear in a real JWKS response) purely for the general case.
        public_key = cast(rsa.RSAPublicKey, RSAAlgorithm.from_jwk(matching_jwk))

        tenant_id = self._settings.azure_ad_tenant_id
        try:
            claims = jwt.decode(
                bearer_token,
                key=public_key,
                algorithms=["RS256"],
                audience=self._settings.azure_ad_client_id,
                issuer=f"https://login.microsoftonline.com/{tenant_id}/v2.0",
            )
        except jwt.PyJWTError as exc:
            raise AzureADTokenError(f"token validation failed: {exc}") from exc

        return VerifiedIdentity(
            subject=claims.get("sub", ""),
            tenant_id=claims.get("tid", ""),
            # App Roles arrive as a `roles` claim (a list) once assigned in
            # the app registration -- absent entirely for a token with no
            # roles assigned, treated as an honest empty list, never an
            # error (a verified-but-roleless identity is real and valid;
            # downstream OPA policy is what decides whether that's enough
            # access, not this verifier).
            roles=claims.get("roles") or [],
            raw_claims=claims,
        )


class FakeAzureADTokenVerifier(AzureADTokenVerifier):
    """Test double. Configure with EXACTLY ONE of `identity` (verification
    succeeds) or `raise_exc` (verification fails) -- mirrors
    `FakeOpaClient`'s identical "at most one configured behavior" rule.
    Records every token passed to `verify()` in `self.calls`."""

    def __init__(
        self,
        *,
        identity: VerifiedIdentity | None = None,
        raise_exc: AzureADTokenError | None = None,
    ) -> None:
        if identity is not None and raise_exc is not None:
            raise ValueError("configure at most one of identity/raise_exc")
        self._identity = identity
        self._raise_exc = raise_exc
        self.calls: list[str] = []

    async def verify(self, bearer_token: str) -> VerifiedIdentity:
        self.calls.append(bearer_token)
        if self._raise_exc is not None:
            raise self._raise_exc
        if self._identity is not None:
            return self._identity
        raise AzureADTokenError(
            "FakeAzureADTokenVerifier not configured with an identity or exception"
        )


# Phase 4 of the configurable-platform build plan: self-registers as an
# import side effect, mirroring `navigraph_connectors.snowflake`/etc.'s
# exact pattern -- see `registry.py`'s own docstring for why this, not a
# caller-remembered separate import, is what triggers registration.
register_verifier("azure_ad", HttpAzureADTokenVerifier, AzureADSettings)
