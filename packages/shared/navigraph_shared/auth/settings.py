"""Settings for real Azure AD (Entra ID) token verification.

Mirrors `navigraph_shared.opa.settings.OpaSettings`'s exact shape: a
handful of fields with safe (empty-string) defaults, subclassing
`NaviGraphSettings` so `AzureAdAuthSettings()` never crashes even with a
completely empty environment -- real values are supplied via env vars in
every real deployment. An empty `azure_ad_tenant_id` is the signal callers
use to decide whether real verification can even be constructed (see
`navigraph_gateway.main`'s lifespan) -- this settings class itself never
guesses or defaults to something insecure.
"""

from __future__ import annotations

from navigraph_shared.config import NaviGraphSettings


class AzureAdAuthSettings(NaviGraphSettings):
    """Connection settings for `AzureAdTokenVerifier`.

    `azure_ad_tenant_id` is the Entra tenant (directory) ID the JWKS
    endpoint and token issuer are both derived from
    (`https://login.microsoftonline.com/{tenant_id}/...`) -- NOT
    NaviGraph's own business `tenant_id` (e.g. `"navikenz-poc"`), which is
    a separate, application-level identifier. `azure_ad_audience` is the
    real app registration's Application ID URI / client ID that verified
    tokens must have been issued for (the JWT `aud` claim) -- accepting
    tokens issued for a different application would let any other Entra
    app's token impersonate a NaviGraph caller.
    """

    azure_ad_tenant_id: str = ""
    azure_ad_audience: str = ""
