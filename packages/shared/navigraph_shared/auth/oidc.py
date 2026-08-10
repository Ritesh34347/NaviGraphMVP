"""Generic OIDC (OpenID Connect) bearer-token verification -- a second,
non-Azure `AzureADTokenVerifier` implementation, proving that ABC is
genuinely reusable across identity providers, not just theoretically
generic (Phase 4 of the configurable-platform build plan).

Real OIDC discovery: fetches `{issuer}/.well-known/openid-configuration`,
extracts `jwks_uri`, then fetches THAT for the real signing keys -- rather
than assuming a fixed well-known path the way `HttpAzureADTokenVerifier`'s
Azure-specific shortcut does. Azure AD's own discovery document also
exposes `jwks_uri` at that same standard path; this is not a special case,
just a more standards-general implementation of the same real mechanism,
usable against any spec-compliant provider (Auth0, Okta, a self-hosted
Keycloak realm, etc.).

Claim names for `tenant_id`/`roles` are NOT standardized across OIDC
providers the way Azure AD's `tid`/`roles` are -- `oidc_tenant_id_claim`/
`oidc_roles_claim` are configurable per tenant for exactly that reason,
defaulting to this platform's own vocabulary (`tenant_id`/`roles`), the
sensible default for a provider a NaviGraph operator configures
themselves (e.g. a Keycloak realm whose custom claim mapper emits exactly
those names).
"""

from __future__ import annotations

import time
from typing import Any, cast

from cryptography.hazmat.primitives.asymmetric import rsa

from navigraph_shared.auth.azure_ad import (
    AzureADTokenError,
    AzureADTokenVerifier,
    VerifiedIdentity,
)
from navigraph_shared.auth.registry import register_verifier
from navigraph_shared.config import NaviGraphSettings


class OidcSettings(NaviGraphSettings):
    """Settings for generic OIDC token verification.

    `oidc_issuer` must be the provider's real issuer URL (no trailing
    slash required -- stripped before use), the same value it also
    asserts as the token's `iss` claim. `oidc_tenant_id_claim`/
    `oidc_roles_claim` name which claims in the verified token map to
    `VerifiedIdentity.tenant_id`/`.roles` -- see this module's docstring
    for why these default to `tenant_id`/`roles` rather than a
    provider-specific convention.
    """

    oidc_issuer: str = ""
    oidc_audience: str = ""
    oidc_tenant_id_claim: str = "tenant_id"
    oidc_roles_claim: str = "roles"


class HttpOidcTokenVerifier(AzureADTokenVerifier):
    """Real JWT/JWKS verification against any standards-compliant OIDC
    provider. Same constructor shape, JWKS in-memory caching (TTL-based,
    default 1 hour), and lazy `httpx`/`PyJWT` imports as
    `HttpAzureADTokenVerifier` -- see that class's docstring for why the
    lazy imports and cache TTL are safe, real choices, not shortcuts.
    """

    def __init__(
        self,
        settings: OidcSettings | None = None,
        *,
        transport: Any | None = None,
        jwks_cache_ttl_seconds: float = 3600.0,
    ) -> None:
        self._settings = settings or OidcSettings()
        self._transport = transport
        self._jwks_cache_ttl_seconds = jwks_cache_ttl_seconds
        self._jwks_cache: dict[str, Any] | None = None
        self._jwks_cache_expires_at: float = 0.0

    async def _get_jwks(self) -> dict[str, Any]:
        import httpx

        now = time.monotonic()
        if self._jwks_cache is not None and now < self._jwks_cache_expires_at:
            return self._jwks_cache

        issuer = self._settings.oidc_issuer.rstrip("/")
        async with httpx.AsyncClient(timeout=10.0, transport=self._transport) as client:
            discovery_response = await client.get(f"{issuer}/.well-known/openid-configuration")
            discovery_response.raise_for_status()
            jwks_uri = discovery_response.json()["jwks_uri"]

            jwks_response = await client.get(jwks_uri)
            jwks_response.raise_for_status()
            jwks: dict[str, Any] = jwks_response.json()

        self._jwks_cache = jwks
        self._jwks_cache_expires_at = now + self._jwks_cache_ttl_seconds
        return jwks

    async def verify(self, bearer_token: str) -> VerifiedIdentity:
        import jwt
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

        try:
            claims = jwt.decode(
                bearer_token,
                key=public_key,
                algorithms=["RS256"],
                audience=self._settings.oidc_audience,
                issuer=self._settings.oidc_issuer,
            )
        except jwt.PyJWTError as exc:
            raise AzureADTokenError(f"token validation failed: {exc}") from exc

        return VerifiedIdentity(
            subject=claims.get("sub", ""),
            tenant_id=claims.get(self._settings.oidc_tenant_id_claim, ""),
            roles=claims.get(self._settings.oidc_roles_claim) or [],
            raw_claims=claims,
        )


# Phase 4 of the configurable-platform build plan: self-registers as an
# import side effect -- see `azure_ad.py`'s identical call and
# `registry.py`'s own docstring for why.
register_verifier("oidc", HttpOidcTokenVerifier, OidcSettings)
