"""Unit tests for `navigraph_shared.auth.registry`.

Real registrations (`azure_ad`/`oidc`, self-registered as an import side
effect of importing `navigraph_shared.auth`) are asserted directly rather
than re-registered here, plus a throwaway third entry to prove
`register_verifier`/`get_verifier_registration` work for any caller, not
just the two built-in providers.
"""

from __future__ import annotations

import pytest

from navigraph_shared.auth.azure_ad import AzureADSettings, HttpAzureADTokenVerifier
from navigraph_shared.auth.oidc import HttpOidcTokenVerifier, OidcSettings
from navigraph_shared.auth.registry import (
    build_verifier,
    get_verifier_registration,
    list_registered_provider_types,
    register_verifier,
)


class TestBuiltInRegistrations:
    def test_azure_ad_and_oidc_are_registered_by_importing_the_package(self) -> None:
        assert "azure_ad" in list_registered_provider_types()
        assert "oidc" in list_registered_provider_types()

    def test_azure_ad_registration_resolves_to_the_real_classes(self) -> None:
        registration = get_verifier_registration("azure_ad")

        assert registration.verifier_cls is HttpAzureADTokenVerifier
        assert registration.settings_cls is AzureADSettings

    def test_oidc_registration_resolves_to_the_real_classes(self) -> None:
        registration = get_verifier_registration("oidc")

        assert registration.verifier_cls is HttpOidcTokenVerifier
        assert registration.settings_cls is OidcSettings


class TestGetVerifierRegistration:
    def test_unknown_provider_type_raises_with_the_registered_list(self) -> None:
        with pytest.raises(ValueError, match="No identity verifier registered"):
            get_verifier_registration("not-a-real-provider")


class TestRegisterVerifier:
    def test_a_new_provider_type_can_be_registered_and_looked_up(self) -> None:
        register_verifier("throwaway-test-provider", HttpAzureADTokenVerifier, AzureADSettings)

        assert "throwaway-test-provider" in list_registered_provider_types()
        registration = get_verifier_registration("throwaway-test-provider")
        assert registration.verifier_cls is HttpAzureADTokenVerifier

    def test_reregistering_the_same_provider_type_overwrites_it(self) -> None:
        register_verifier("throwaway-overwrite-test", HttpAzureADTokenVerifier, AzureADSettings)
        register_verifier("throwaway-overwrite-test", HttpOidcTokenVerifier, OidcSettings)

        registration = get_verifier_registration("throwaway-overwrite-test")
        assert registration.verifier_cls is HttpOidcTokenVerifier
        assert registration.settings_cls is OidcSettings


class TestBuildVerifier:
    def test_validates_settings_and_constructs_the_registered_verifier(self) -> None:
        verifier = build_verifier(
            "azure_ad",
            {"azure_ad_tenant_id": "tenant-abc", "azure_ad_client_id": "client-xyz"},
        )

        assert isinstance(verifier, HttpAzureADTokenVerifier)
        assert verifier._settings.azure_ad_tenant_id == "tenant-abc"
        assert verifier._settings.azure_ad_client_id == "client-xyz"

    def test_builds_an_oidc_verifier_too(self) -> None:
        verifier = build_verifier(
            "oidc", {"oidc_issuer": "https://idp.example.com", "oidc_audience": "navigraph"}
        )

        assert isinstance(verifier, HttpOidcTokenVerifier)
        assert verifier._settings.oidc_issuer == "https://idp.example.com"

    def test_unknown_provider_type_raises(self) -> None:
        with pytest.raises(ValueError, match="No identity verifier registered"):
            build_verifier("not-a-real-provider", {})

    def test_invalid_settings_raise_a_validation_error(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            build_verifier("azure_ad", {"azure_ad_tenant_id": {"not": "a string"}})

    def test_an_unknown_settings_key_raises_rather_than_being_silently_ignored(self) -> None:
        """`NaviGraphSettings` subclasses use `extra="ignore"` (correct
        for reading real env vars) -- `build_verifier` must still catch a
        typo'd key itself, or a human-typed `provider_settings` blob with
        a mistake in it would silently validate and persist, only failing
        later at the next real token verification."""

        with pytest.raises(ValueError, match="Unknown settings.*oidc_issur_typo"):
            build_verifier("oidc", {"oidc_issur_typo": "https://idp.example.com"})
