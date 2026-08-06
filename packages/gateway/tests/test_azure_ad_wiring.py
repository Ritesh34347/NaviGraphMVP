"""Tests for the gateway's `/ask` Azure AD wiring (`_verify_identity`).

`_verify_identity` is tested directly as the async dependency function it
is, monkeypatching the module-level `_azure_ad_settings`/`_azure_ad_verifier`
(the same objects the real `/ask` route depends on) rather than going
through a live TestClient HTTP round-trip -- `/ask` itself always makes a
real HTTP call to agent-runtime via `app.state.http_client`, which has no
fake/mock injection point in this module (mirrors why `test_mcp_tools.py`
builds its own independent `FastMCP` instance instead of exercising `/mcp`
through the shared `app`). This still proves the exact real code path
`/ask` invokes via `Depends(_verify_identity)`.
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


async def test_disabled_by_default_returns_none() -> None:
    assert gateway_main._azure_ad_settings.azure_ad_enabled is False

    result = await gateway_main._verify_identity(authorization="Bearer whatever")

    assert result is None


async def test_disabled_ignores_missing_header_too() -> None:
    result = await gateway_main._verify_identity(authorization=None)

    assert result is None


async def test_enabled_missing_header_raises_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gateway_main._azure_ad_settings, "azure_ad_enabled", True)

    with pytest.raises(HTTPException) as exc_info:
        await gateway_main._verify_identity(authorization=None)

    assert exc_info.value.status_code == 401


async def test_enabled_non_bearer_header_raises_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gateway_main._azure_ad_settings, "azure_ad_enabled", True)

    with pytest.raises(HTTPException) as exc_info:
        await gateway_main._verify_identity(authorization="Basic dXNlcjpwYXNz")

    assert exc_info.value.status_code == 401


async def test_enabled_valid_token_returns_verified_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = VerifiedIdentity(subject="user-1", tenant_id="tenant-a", roles=["admin"])
    fake_verifier = FakeAzureADTokenVerifier(identity=identity)
    monkeypatch.setattr(gateway_main._azure_ad_settings, "azure_ad_enabled", True)
    monkeypatch.setattr(gateway_main, "_azure_ad_verifier", fake_verifier)

    result = await gateway_main._verify_identity(authorization="Bearer real-token")

    assert result == identity
    assert fake_verifier.calls == ["real-token"]


async def test_enabled_rejected_token_raises_401(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_verifier = FakeAzureADTokenVerifier(raise_exc=AzureADTokenError("expired"))
    monkeypatch.setattr(gateway_main._azure_ad_settings, "azure_ad_enabled", True)
    monkeypatch.setattr(gateway_main, "_azure_ad_verifier", fake_verifier)

    with pytest.raises(HTTPException) as exc_info:
        await gateway_main._verify_identity(authorization="Bearer bad-token")

    assert exc_info.value.status_code == 401
    assert "expired" in exc_info.value.detail
