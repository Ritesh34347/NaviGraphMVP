"""Runtime registry mapping a `provider_type` string to an
`AzureADTokenVerifier` subclass, plus the `NaviGraphSettings` subclass its
constructor validates raw provider settings against.

Mirrors `navigraph_connectors.registry`'s `register_/get_/list_` pattern
exactly (module-level dict, overwrite-on-reregister, `ValueError` with the
sorted registered list on a miss) -- extended with a settings class
because, unlike a `Connector` (constructed with no arguments, reading its
own global env vars), each verifier needs a real `Settings` type to
validate an arbitrary `provider_settings` dict (e.g. a
`TenantIdentityConfig.provider_settings` JSONB blob) against before
construction.

Concrete verifiers self-register as an import side effect, the same
pattern `navigraph_connectors.snowflake`/`.postgres`/`.databricks` already
use (see `azure_ad.py`'s and `oidc.py`'s own `register_verifier(...)`
calls at module level) -- importing `navigraph_shared.auth` (this
package's `__init__.py` already imports both modules for re-export)
triggers both registrations, so no caller needs to remember a separate
side-effect import (unlike the real bug Phase 2 found and fixed in
`onboard_data_source.py` for connectors, which never imported its
connector submodules at all).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from navigraph_shared.auth.azure_ad import AzureADTokenVerifier
    from navigraph_shared.config import NaviGraphSettings


@dataclass(frozen=True)
class VerifierRegistration:
    verifier_cls: type[AzureADTokenVerifier]
    settings_cls: type[NaviGraphSettings]


_REGISTRY: dict[str, VerifierRegistration] = {}


def register_verifier(
    provider_type: str,
    verifier_cls: type[AzureADTokenVerifier],
    settings_cls: type[NaviGraphSettings],
) -> None:
    """Register `verifier_cls`/`settings_cls` as the implementation for
    `provider_type`. Re-registering the same `provider_type` overwrites
    the previous entry (useful for tests that register a fake verifier
    under a throwaway name)."""

    _REGISTRY[provider_type] = VerifierRegistration(verifier_cls, settings_cls)


def get_verifier_registration(provider_type: str) -> VerifierRegistration:
    """Look up the registered verifier/settings class pair for
    `provider_type`.

    Raises:
        ValueError: if no verifier has been registered for `provider_type`.
    """

    try:
        return _REGISTRY[provider_type]
    except KeyError as exc:
        raise ValueError(
            f"No identity verifier registered for provider_type={provider_type!r}. "
            f"Registered types: {sorted(_REGISTRY)}"
        ) from exc


def list_registered_provider_types() -> list[str]:
    """Return every currently-registered `provider_type`, sorted."""

    return sorted(_REGISTRY)


def build_verifier(provider_type: str, provider_settings: dict) -> AzureADTokenVerifier:
    """Validate `provider_settings` against `provider_type`'s registered
    `Settings` class, then construct and return its verifier -- the one
    real entrypoint callers (the gateway, tests) should use instead of
    calling `get_verifier_registration` and constructing by hand.

    REAL GAP, found live exercising `navigraph_admin.py identity
    set-provider` by hand: every `NaviGraphSettings` subclass sets
    `extra="ignore"` (correct for its usual job -- reading real OS env
    vars, where unrelated vars are common and expected) -- so
    `model_validate` alone silently drops a typo'd key
    (`oidc_issur_typo`) instead of rejecting it, defeating the whole
    point of validating a human-typed `provider_settings` blob before
    persisting it. Checked explicitly here instead, since this call site
    -- not the base settings class's real env-var-reading job -- is the
    one that actually needs `extra="forbid"`-shaped strictness.
    """

    registration = get_verifier_registration(provider_type)
    known_fields = set(registration.settings_cls.model_fields)
    unknown_keys = set(provider_settings) - known_fields
    if unknown_keys:
        raise ValueError(
            f"Unknown settings for provider_type={provider_type!r}: "
            f"{sorted(unknown_keys)}. Valid fields: {sorted(known_fields)}"
        )

    settings = registration.settings_cls.model_validate(provider_settings)
    # The ABC itself declares no constructor (per the build plan, it needs
    # no change) -- every REGISTERED concrete subclass shares the same
    # `__init__(self, settings, *, transport=None, ...)` shape by
    # convention (see `HttpAzureADTokenVerifier`/`HttpOidcTokenVerifier`),
    # which mypy can't see through `type[AzureADTokenVerifier]` alone.
    return registration.verifier_cls(settings)  # type: ignore[call-arg]
