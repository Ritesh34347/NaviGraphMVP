"""Token verification abstractions: provider-agnostic base + real Azure AD client + fake test double."""

from navigraph_shared.auth.client import (
    AzureAdTokenVerifier,
    FakeTokenVerifier,
    TokenClaims,
    TokenVerificationError,
    TokenVerifier,
)
from navigraph_shared.auth.settings import AzureAdAuthSettings

__all__ = [
    "AzureAdAuthSettings",
    "AzureAdTokenVerifier",
    "FakeTokenVerifier",
    "TokenClaims",
    "TokenVerificationError",
    "TokenVerifier",
]
