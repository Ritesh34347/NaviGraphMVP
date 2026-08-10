"""Per-tenant identity-verifier resolution for the gateway (Phase 4 of the
configurable-platform build plan).

CHICKEN-AND-EGG NOTE: which verifier to use for a request is resolved
from the SAME `tenant_id` every other part of this codebase already
trusts pre-auth (`/ask`'s request-body field, `/lineage`'s query param) --
not a new subdomain/path-based scheme. This repo's entire multi-tenant
trust model already rests on that one pre-auth signal; `infra/opa/
policies/authz.rego`'s `tenant_claim_matches` rule performs the exact
same kind of post-auth cross-check `main.py` also performs here (the
verified identity's own `tenant_id` claim must match what the caller
declared, or the request is rejected regardless of which verifier
resolved it). Introducing a second, URL-based pre-auth signal just for
this one lookup would add a new trust boundary inconsistent with every
other tenant-scoped operation in this system, for no real security gain --
the real protection is the post-auth match, which this module's caller
enforces either way.

FAILS SAFE, NOT CLOSED: if `lookup` raises or returns nothing for a
tenant, `resolve()` falls back to `default_verifier` -- the exact,
single, global verifier every tenant already had before this module
existed. A metadata-catalog outage must never be a new way for every
tenant's auth to start failing; it can only ever fall back to
already-shipped, global behavior. Deliberately decoupled from
`metadata_catalog` itself (`lookup` is injected, not hardcoded) so this
class's caching/fallback logic is unit-testable with a plain fake
callable -- `main.py` wires the real, catalog-backed lookup separately.

Resolved verifiers are cached in-memory per tenant with a TTL (mirroring
`HttpAzureADTokenVerifier`'s own JWKS-caching pattern), since `lookup`'s
real implementation is a live Postgres query and this resolves on every
request needing identity verification, not once at process startup.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from navigraph_shared.auth.azure_ad import AzureADTokenVerifier
from navigraph_shared.auth.registry import build_verifier

TenantProviderLookup = Callable[[str], "tuple[str, dict] | None"]


class TenantVerifierResolver:
    """Caches, per `tenant_id`, which `AzureADTokenVerifier` to use --
    built from that tenant's real `(provider_type, provider_settings)`
    if `lookup` finds one, else `default_verifier`."""

    def __init__(
        self,
        default_verifier: AzureADTokenVerifier,
        *,
        lookup: TenantProviderLookup | None = None,
        cache_ttl_seconds: float = 300.0,
    ) -> None:
        self._default_verifier = default_verifier
        self._lookup = lookup
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cache: dict[str, tuple[AzureADTokenVerifier, float]] = {}

    async def resolve(self, tenant_id: str) -> AzureADTokenVerifier:
        now = time.monotonic()
        cached = self._cache.get(tenant_id)
        if cached is not None and now < cached[1]:
            return cached[0]

        verifier = self._default_verifier
        if self._lookup is not None:
            try:
                found = self._lookup(tenant_id)
            except Exception:  # noqa: BLE001 -- deliberately blind, see module docstring's "FAILS SAFE, NOT CLOSED"
                found = None
            if found is not None:
                provider_type, provider_settings = found
                try:
                    verifier = build_verifier(provider_type, provider_settings)
                except Exception:  # noqa: BLE001 -- same "fails safe" reasoning as above
                    verifier = self._default_verifier

        self._cache[tenant_id] = (verifier, now + self._cache_ttl_seconds)
        return verifier
