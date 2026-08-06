"""Azure AD token verification -- see `azure_ad.py` for the real
implementation and why it exists (LIMITATIONS.md's Azure AD token
verification item)."""

from __future__ import annotations

from navigraph_shared.auth.azure_ad import (
    AzureADSettings,
    AzureADTokenError,
    AzureADTokenVerifier,
    FakeAzureADTokenVerifier,
    HttpAzureADTokenVerifier,
    VerifiedIdentity,
)

__all__ = [
    "AzureADSettings",
    "AzureADTokenError",
    "AzureADTokenVerifier",
    "FakeAzureADTokenVerifier",
    "HttpAzureADTokenVerifier",
    "VerifiedIdentity",
]
