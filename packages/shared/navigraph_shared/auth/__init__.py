"""Pluggable, tenant-aware bearer-token verification.

`azure_ad.py` has the real Azure AD implementation and why it exists
(LIMITATIONS.md's Azure AD token verification item); `oidc.py` is a
second, generic OIDC implementation added in Phase 4 of the
configurable-platform build plan, proving `AzureADTokenVerifier` is
genuinely reusable across providers. `registry.py` maps a
`provider_type` string to whichever of these a tenant is configured to
use. Importing this package (as every real caller already does) is what
triggers both concrete verifiers' self-registration -- see `registry.py`'s
own docstring for why that's a deliberate, no-caller-remembers-anything
design, not an accident of import order.
"""

from __future__ import annotations

from navigraph_shared.auth.azure_ad import (
    AzureADSettings,
    AzureADTokenError,
    AzureADTokenVerifier,
    FakeAzureADTokenVerifier,
    HttpAzureADTokenVerifier,
    VerifiedIdentity,
)
from navigraph_shared.auth.oidc import HttpOidcTokenVerifier, OidcSettings
from navigraph_shared.auth.registry import (
    build_verifier,
    get_verifier_registration,
    list_registered_provider_types,
    register_verifier,
)

__all__ = [
    "AzureADSettings",
    "AzureADTokenError",
    "AzureADTokenVerifier",
    "FakeAzureADTokenVerifier",
    "HttpAzureADTokenVerifier",
    "HttpOidcTokenVerifier",
    "OidcSettings",
    "VerifiedIdentity",
    "build_verifier",
    "get_verifier_registration",
    "list_registered_provider_types",
    "register_verifier",
]
