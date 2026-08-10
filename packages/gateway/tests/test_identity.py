"""Unit tests for `navigraph_gateway.identity.TenantVerifierResolver`,
DB- and network-free.

`lookup` is a plain fake callable, never a real `metadata_catalog`
session -- see `identity.py`'s own docstring for why that decoupling is
deliberate (this class's caching/fallback logic should be testable
without any DB/session-factory mocking gymnastics; `main.py` wires the
real, catalog-backed lookup separately).
"""

from __future__ import annotations

from unittest.mock import patch

from navigraph_shared.auth import FakeAzureADTokenVerifier

from navigraph_gateway.identity import TenantVerifierResolver


class TestTenantVerifierResolver:
    async def test_no_lookup_configured_always_returns_the_default(self) -> None:
        default_verifier = FakeAzureADTokenVerifier()
        resolver = TenantVerifierResolver(default_verifier)

        result = await resolver.resolve("tenant-a")

        assert result is default_verifier

    async def test_lookup_returning_none_falls_back_to_the_default(self) -> None:
        default_verifier = FakeAzureADTokenVerifier()
        resolver = TenantVerifierResolver(default_verifier, lookup=lambda _tenant_id: None)

        result = await resolver.resolve("tenant-a")

        assert result is default_verifier

    async def test_a_raising_lookup_falls_back_to_the_default_not_an_exception(self) -> None:
        default_verifier = FakeAzureADTokenVerifier()

        def raising_lookup(_tenant_id: str) -> None:
            raise ConnectionError("catalog unreachable")

        resolver = TenantVerifierResolver(default_verifier, lookup=raising_lookup)

        result = await resolver.resolve("tenant-a")

        assert result is default_verifier

    async def test_an_unregistered_provider_type_falls_back_to_the_default(self) -> None:
        default_verifier = FakeAzureADTokenVerifier()
        resolver = TenantVerifierResolver(
            default_verifier, lookup=lambda _tenant_id: ("not-a-real-provider", {})
        )

        result = await resolver.resolve("tenant-a")

        assert result is default_verifier

    async def test_a_configured_tenant_gets_a_verifier_built_from_its_own_config(self) -> None:
        default_verifier = FakeAzureADTokenVerifier()
        tenant_verifier = FakeAzureADTokenVerifier()
        resolver = TenantVerifierResolver(
            default_verifier,
            lookup=lambda _tenant_id: ("azure_ad", {"azure_ad_tenant_id": "t"}),
        )

        with patch(
            "navigraph_gateway.identity.build_verifier", return_value=tenant_verifier
        ) as mock_build:
            result = await resolver.resolve("tenant-a")

        mock_build.assert_called_once_with("azure_ad", {"azure_ad_tenant_id": "t"})
        assert result is tenant_verifier

    async def test_result_is_cached_the_lookup_only_runs_once(self) -> None:
        call_count = 0

        def lookup(_tenant_id: str) -> None:
            nonlocal call_count
            call_count += 1

        resolver = TenantVerifierResolver(FakeAzureADTokenVerifier(), lookup=lookup)

        await resolver.resolve("tenant-a")
        await resolver.resolve("tenant-a")
        await resolver.resolve("tenant-a")

        assert call_count == 1

    async def test_different_tenants_are_cached_independently(self) -> None:
        default_verifier = FakeAzureADTokenVerifier()
        tenant_b_verifier = FakeAzureADTokenVerifier()

        def lookup(tenant_id: str) -> tuple[str, dict] | None:
            if tenant_id == "tenant-b":
                return "azure_ad", {}
            return None

        resolver = TenantVerifierResolver(default_verifier, lookup=lookup)

        with patch("navigraph_gateway.identity.build_verifier", return_value=tenant_b_verifier):
            result_a = await resolver.resolve("tenant-a")
            result_b = await resolver.resolve("tenant-b")

        assert result_a is default_verifier
        assert result_b is tenant_b_verifier

    async def test_cache_expires_after_the_configured_ttl(self) -> None:
        import navigraph_gateway.identity as identity_module

        call_count = 0

        def lookup(_tenant_id: str) -> None:
            nonlocal call_count
            call_count += 1

        fake_time = [1000.0]
        with patch.object(identity_module.time, "monotonic", lambda: fake_time[0]):
            resolver = TenantVerifierResolver(
                FakeAzureADTokenVerifier(), lookup=lookup, cache_ttl_seconds=60.0
            )

            await resolver.resolve("tenant-a")
            fake_time[0] += 30.0
            await resolver.resolve("tenant-a")
            assert call_count == 1

            fake_time[0] += 40.0  # now 70s after the first call -- past the 60s TTL
            await resolver.resolve("tenant-a")
            assert call_count == 2
