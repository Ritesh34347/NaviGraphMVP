"""Tests for the gateway's `/lineage` and `/lineage/{trace_id}` real proxy
routes.

Follows `test_azure_ad_wiring.py`'s established pattern of monkeypatching
the module-level `_azure_ad_settings`/`_verifier_resolver` (the same
objects `Depends(_extract_bearer_token)`/`_verify_identity_for_tenant`
read) rather than a real JWKS round trip -- see that module's docstring
for the full rationale, which applies identically here since these
routes are gated by the exact same check `/ask` uses. `app.state
.http_client` is overridden after entering the `TestClient` context (the
same object `search_lineage_traces`/`get_lineage_trace` read at request
time) to fake the agent-runtime hop without a real network call.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from navigraph_shared.auth import (
    AzureADTokenError,
    FakeAzureADTokenVerifier,
    VerifiedIdentity,
)

from navigraph_gateway import main as gateway_main
from navigraph_gateway.identity import TenantVerifierResolver
from navigraph_gateway.main import app


def _mock_agent_runtime(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="http://agent-runtime-test", transport=httpx.MockTransport(handler)
    )


def test_lineage_search_with_azure_ad_disabled_proxies_to_agent_runtime() -> None:
    assert gateway_main._azure_ad_settings.azure_ad_enabled is False
    captured_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(200, json={"tenant_id": "tenant-a", "traces": []})

    with TestClient(app) as client:
        app.state.http_client = _mock_agent_runtime(handler)

        response = client.get("/lineage", params={"tenant_id": "tenant-a"})

    assert response.status_code == 200
    assert response.json() == {"tenant_id": "tenant-a", "traces": []}
    assert len(captured_requests) == 1
    assert captured_requests[0].url.path == "/lineage"
    assert dict(captured_requests[0].url.params) == {
        "tenant_id": "tenant-a",
        "limit": "50",
        "offset": "0",
    }


def test_lineage_search_forwards_optional_filters_only_when_given() -> None:
    captured_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(200, json={"tenant_id": "tenant-a", "traces": []})

    with TestClient(app) as client:
        app.state.http_client = _mock_agent_runtime(handler)

        response = client.get(
            "/lineage",
            params={
                "tenant_id": "tenant-a",
                "agent_name": "query.sql_generation",
                "search_text": "revenue",
                "limit": 10,
                "offset": 5,
            },
        )

    assert response.status_code == 200
    forwarded = dict(captured_requests[0].url.params)
    assert forwarded["agent_name"] == "query.sql_generation"
    assert forwarded["search_text"] == "revenue"
    assert forwarded["limit"] == "10"
    assert forwarded["offset"] == "5"
    assert "since" not in forwarded
    assert "until" not in forwarded


def test_lineage_trace_detail_with_azure_ad_disabled_proxies_to_agent_runtime() -> None:
    captured_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(
            200, json={"trace_id": "trace-1", "tenant_id": "tenant-a", "events": []}
        )

    with TestClient(app) as client:
        app.state.http_client = _mock_agent_runtime(handler)

        response = client.get("/lineage/trace-1", params={"tenant_id": "tenant-a"})

    assert response.status_code == 200
    assert response.json()["trace_id"] == "trace-1"
    assert captured_requests[0].url.path == "/lineage/trace-1"
    assert dict(captured_requests[0].url.params) == {"tenant_id": "tenant-a"}


def test_lineage_search_requires_a_bearer_token_when_azure_ad_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gateway_main._azure_ad_settings, "azure_ad_enabled", True)

    with TestClient(app) as client:
        response = client.get("/lineage", params={"tenant_id": "tenant-a"})

    assert response.status_code == 401


def test_lineage_trace_detail_requires_a_bearer_token_when_azure_ad_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gateway_main._azure_ad_settings, "azure_ad_enabled", True)

    with TestClient(app) as client:
        response = client.get("/lineage/trace-1", params={"tenant_id": "tenant-a"})

    assert response.status_code == 401


def test_lineage_search_with_a_rejected_token_is_401(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_verifier = FakeAzureADTokenVerifier(raise_exc=AzureADTokenError("expired"))
    monkeypatch.setattr(gateway_main._azure_ad_settings, "azure_ad_enabled", True)
    monkeypatch.setattr(gateway_main, "_verifier_resolver", TenantVerifierResolver(fake_verifier))

    with TestClient(app) as client:
        response = client.get(
            "/lineage",
            params={"tenant_id": "tenant-a"},
            headers={"Authorization": "Bearer bad-token"},
        )

    assert response.status_code == 401


def test_lineage_search_with_a_verified_identity_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    identity = VerifiedIdentity(subject="user-1", tenant_id="tenant-a", roles=["admin"])
    fake_verifier = FakeAzureADTokenVerifier(identity=identity)
    monkeypatch.setattr(gateway_main._azure_ad_settings, "azure_ad_enabled", True)
    monkeypatch.setattr(gateway_main, "_verifier_resolver", TenantVerifierResolver(fake_verifier))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"tenant_id": "tenant-a", "traces": []})

    with TestClient(app) as client:
        app.state.http_client = _mock_agent_runtime(handler)

        response = client.get(
            "/lineage",
            params={"tenant_id": "tenant-a"},
            headers={"Authorization": "Bearer real-token"},
        )

    assert response.status_code == 200
    assert fake_verifier.calls == ["real-token"]


def test_lineage_search_returns_502_when_agent_runtime_is_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with TestClient(app) as client:
        app.state.http_client = _mock_agent_runtime(handler)

        response = client.get("/lineage", params={"tenant_id": "tenant-a"})

    assert response.status_code == 502
