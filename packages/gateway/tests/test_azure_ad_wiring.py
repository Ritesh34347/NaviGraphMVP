"""Tests for the gateway's identity-verification wiring
(`_extract_bearer_token` + `_verify_identity_for_tenant`), and its Phase 4
per-tenant verifier resolution (`_verifier_resolver`).

Tested directly as the async functions they are, monkeypatching the
module-level `_azure_ad_settings`/`_verifier_resolver` (the same objects
the real `/ask`/`/lineage` routes depend on) rather than going through a
live TestClient HTTP round-trip -- `/ask` itself always makes a real HTTP
call to agent-runtime via `app.state.http_client`, which has no fake/mock
injection point in this module (mirrors why `test_mcp_tools.py` builds
its own independent `FastMCP` instance instead of exercising `/mcp`
through the shared `app`). This still proves the exact real code path
`/ask`/`/lineage` invoke.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from navigraph_shared.auth import (
    AzureADTokenError,
    FakeAzureADTokenVerifier,
    VerifiedIdentity,
)

from navigraph_gateway import main as gateway_main
from navigraph_gateway.identity import TenantVerifierResolver


class TestExtractBearerToken:
    async def test_disabled_by_default_returns_none(self) -> None:
        assert gateway_main._azure_ad_settings.azure_ad_enabled is False

        result = await gateway_main._extract_bearer_token(authorization="Bearer whatever")

        assert result is None

    async def test_disabled_ignores_missing_header_too(self) -> None:
        result = await gateway_main._extract_bearer_token(authorization=None)

        assert result is None

    async def test_enabled_missing_header_raises_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(gateway_main._azure_ad_settings, "azure_ad_enabled", True)

        with pytest.raises(HTTPException) as exc_info:
            await gateway_main._extract_bearer_token(authorization=None)

        assert exc_info.value.status_code == 401

    async def test_enabled_non_bearer_header_raises_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(gateway_main._azure_ad_settings, "azure_ad_enabled", True)

        with pytest.raises(HTTPException) as exc_info:
            await gateway_main._extract_bearer_token(authorization="Basic dXNlcjpwYXNz")

        assert exc_info.value.status_code == 401

    async def test_enabled_valid_header_returns_raw_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(gateway_main._azure_ad_settings, "azure_ad_enabled", True)

        result = await gateway_main._extract_bearer_token(authorization="Bearer real-token")

        assert result == "real-token"


class TestVerifyIdentityForTenant:
    async def test_no_token_returns_none(self) -> None:
        result = await gateway_main._verify_identity_for_tenant("tenant-a", None)

        assert result is None

    async def test_valid_token_returns_verified_identity(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        identity = VerifiedIdentity(subject="user-1", tenant_id="tenant-a", roles=["admin"])
        fake_verifier = FakeAzureADTokenVerifier(identity=identity)
        monkeypatch.setattr(
            gateway_main, "_verifier_resolver", TenantVerifierResolver(fake_verifier)
        )

        result = await gateway_main._verify_identity_for_tenant("tenant-a", "real-token")

        assert result == identity
        assert fake_verifier.calls == ["real-token"]

    async def test_rejected_token_raises_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_verifier = FakeAzureADTokenVerifier(raise_exc=AzureADTokenError("expired"))
        monkeypatch.setattr(
            gateway_main, "_verifier_resolver", TenantVerifierResolver(fake_verifier)
        )

        with pytest.raises(HTTPException) as exc_info:
            await gateway_main._verify_identity_for_tenant("tenant-a", "bad-token")

        assert exc_info.value.status_code == 401
        assert "expired" in exc_info.value.detail

    async def test_mismatched_verified_tenant_raises_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A token that verifies successfully but asserts a DIFFERENT
        tenant than the caller declared must still be rejected -- the
        same tenant-isolation property `authz.rego`'s
        `tenant_claim_matches` enforces downstream, enforced again here
        at the edge (Phase 4's real addition: this check didn't exist
        before per-tenant verifier resolution existed)."""

        identity = VerifiedIdentity(subject="user-1", tenant_id="tenant-b", roles=["admin"])
        fake_verifier = FakeAzureADTokenVerifier(identity=identity)
        monkeypatch.setattr(
            gateway_main, "_verifier_resolver", TenantVerifierResolver(fake_verifier)
        )

        with pytest.raises(HTTPException) as exc_info:
            await gateway_main._verify_identity_for_tenant("tenant-a", "real-token")

        assert exc_info.value.status_code == 401
        assert "tenant-a" in exc_info.value.detail
        assert "tenant-b" in exc_info.value.detail

    async def test_uses_the_tenant_specific_verifier_when_one_is_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Proves per-tenant resolution actually selects a DIFFERENT
        verifier than the global default when `tenant_id` has a
        configured provider -- the real point of Phase 4, not just that
        the default keeps working."""

        default_identity = VerifiedIdentity(subject="default-user", tenant_id="tenant-a")
        default_verifier = FakeAzureADTokenVerifier(identity=default_identity)

        tenant_identity = VerifiedIdentity(subject="tenant-b-user", tenant_id="tenant-b")
        tenant_verifier = FakeAzureADTokenVerifier(identity=tenant_identity)

        def lookup(tenant_id: str) -> tuple[str, dict] | None:
            if tenant_id == "tenant-b":
                return "azure_ad", {"azure_ad_tenant_id": "t", "azure_ad_client_id": "c"}
            return None

        resolver = TenantVerifierResolver(default_verifier, lookup=lookup)
        monkeypatch.setattr(gateway_main, "_verifier_resolver", resolver)
        # The lookup above returns a real azure_ad provider registration,
        # but building it would construct a real HttpAzureADTokenVerifier
        # (not the fake one) -- patch the registry's build step instead so
        # this test proves RESOLUTION routes to a different verifier per
        # tenant, without needing to fake JWKS/JWT machinery too.
        monkeypatch.setattr(
            "navigraph_gateway.identity.build_verifier", lambda *_a, **_k: tenant_verifier
        )

        result_a = await gateway_main._verify_identity_for_tenant("tenant-a", "token-a")
        result_b = await gateway_main._verify_identity_for_tenant("tenant-b", "token-b")

        assert result_a == default_identity
        assert result_b == tenant_identity
        assert default_verifier.calls == ["token-a"]
        assert tenant_verifier.calls == ["token-b"]
