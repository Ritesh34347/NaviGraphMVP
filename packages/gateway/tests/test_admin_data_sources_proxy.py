"""Tests for the gateway's `/admin/data-sources/*` and
`/admin/semantic-models/compile-and-activate` real proxy routes.

Follows `test_lineage_proxy.py`'s exact pattern: `app.state.http_client` is
overridden AFTER entering the `TestClient` context (the same object every
proxy route reads at request time) to fake the agent-runtime hop without a
real network call, and Azure AD gating is exercised by monkeypatching the
same module-level objects `test_lineage_proxy.py`/`test_azure_ad_wiring.py`
already do.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from navigraph_shared.auth import AzureADTokenError, FakeAzureADTokenVerifier, VerifiedIdentity

from navigraph_gateway import main as gateway_main
from navigraph_gateway.identity import TenantVerifierResolver
from navigraph_gateway.main import app


def _mock_agent_runtime(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="http://agent-runtime-test", transport=httpx.MockTransport(handler)
    )


def test_list_connector_types_proxies_with_no_tenant_gate() -> None:
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"source_types": [{"source_type": "snowflake"}]})

    with TestClient(app) as client:
        app.state.http_client = _mock_agent_runtime(handler)

        response = client.get("/admin/data-sources/connector-types")

    assert response.status_code == 200
    assert response.json()["source_types"][0]["source_type"] == "snowflake"
    assert captured[0].url.path == "/onboarding/connector-types"


def test_test_connection_proxies_request_body_verbatim() -> None:
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"success": True, "message": "ok", "latency_ms": 5.0})

    with TestClient(app) as client:
        app.state.http_client = _mock_agent_runtime(handler)

        response = client.post(
            "/admin/data-sources/test-connection",
            json={"source_type": "snowflake", "credential_fields": {"account": "acct-1"}},
        )

    assert response.status_code == 200
    assert response.json()["success"] is True
    import json as _json

    assert _json.loads(captured[0].content) == {
        "source_type": "snowflake",
        "credential_fields": {"account": "acct-1"},
    }


def test_list_admin_data_sources_with_azure_ad_disabled_proxies() -> None:
    assert gateway_main._azure_ad_settings.azure_ad_enabled is False
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200, json={"tenant_id": "acme-corp", "semantic_model_active_version": None, "data_sources": []}
        )

    with TestClient(app) as client:
        app.state.http_client = _mock_agent_runtime(handler)

        response = client.get("/admin/data-sources", params={"tenant_id": "acme-corp"})

    assert response.status_code == 200
    assert captured[0].url.path == "/onboarding/data-sources"
    assert dict(captured[0].url.params) == {"tenant_id": "acme-corp"}


def test_list_admin_data_sources_requires_bearer_token_when_azure_ad_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gateway_main._azure_ad_settings, "azure_ad_enabled", True)

    with TestClient(app) as client:
        response = client.get("/admin/data-sources", params={"tenant_id": "acme-corp"})

    assert response.status_code == 401


def test_register_admin_data_source_proxies_body_and_returns_created_source() -> None:
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "id": "11111111-1111-1111-1111-111111111111",
                "tenant_id": "acme-corp",
                "name": "acme-snowflake",
                "source_type": "snowflake",
                "is_default": False,
            },
        )

    with TestClient(app) as client:
        app.state.http_client = _mock_agent_runtime(handler)

        response = client.post(
            "/admin/data-sources",
            json={
                "tenant_id": "acme-corp",
                "name": "acme-snowflake",
                "source_type": "snowflake",
                "credential_fields": {"account": "acct-1"},
            },
        )

    assert response.status_code == 200
    assert response.json()["id"] == "11111111-1111-1111-1111-111111111111"
    assert captured[0].url.path == "/onboarding/data-sources"


def test_crawl_admin_data_source_proxies_to_the_right_path() -> None:
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"data_source_id": "ds-1", "tables_synced": 3, "new_table_names": []})

    with TestClient(app) as client:
        app.state.http_client = _mock_agent_runtime(handler)

        response = client.post(
            "/admin/data-sources/ds-1/crawl", json={"tenant_id": "acme-corp"}
        )

    assert response.status_code == 200
    assert response.json()["tables_synced"] == 3
    assert captured[0].url.path == "/onboarding/data-sources/ds-1/crawl"


def test_draft_ontology_builds_request_context_and_proxies_to_existing_agent_route() -> None:
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "result": {"data_source_id": "ds-1", "entities": [], "relationships": [], "sensitive_columns": [], "metrics": []},
                "metadata": {"latency_ms": 10.0},
            },
        )

    with TestClient(app) as client:
        app.state.http_client = _mock_agent_runtime(handler)

        response = client.post(
            "/admin/data-sources/ds-1/draft-ontology",
            json={"tenant_id": "acme-corp", "user_id": "alice"},
        )

    assert response.status_code == 200
    assert captured[0].url.path == "/agents/understanding/ontology_drafting/invoke"
    import json as _json

    body = _json.loads(captured[0].content)
    # Proxies straight to the EXISTING agent invoke route -- not a new,
    # second copy of the ontology drafting agent's own wiring.
    assert body["payload"] == {"data_source_id": "ds-1"}
    assert body["request_context"]["tenant_id"] == "acme-corp"
    assert body["request_context"]["user_id"] == "alice"
    assert body["request_context"]["trace_id"]  # a real uuid4 was generated


def test_compile_and_activate_forwards_422_issues_verbatim() -> None:
    """The one behavioral difference from every other proxy route in this
    file: a 422 must be forwarded as a 422 with the structured `issues`
    body intact, not folded into a generic 502."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": {"issues": ["entity 'Customer' has no bindings"]}})

    with TestClient(app) as client:
        app.state.http_client = _mock_agent_runtime(handler)

        response = client.post(
            "/admin/semantic-models/compile-and-activate",
            json={
                "tenant_id": "acme-corp",
                "data_source_name": "acme-snowflake",
                "draft": {"entities": []},
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"] == {"issues": ["entity 'Customer' has no bindings"]}


def test_compile_and_activate_succeeds_and_returns_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"tenant_id": "acme-corp", "version": 1, "tagged_pii_columns": 2, "compile_warnings": []}
        )

    with TestClient(app) as client:
        app.state.http_client = _mock_agent_runtime(handler)

        response = client.post(
            "/admin/semantic-models/compile-and-activate",
            json={
                "tenant_id": "acme-corp",
                "data_source_name": "acme-snowflake",
                "draft": {"entities": []},
            },
        )

    assert response.status_code == 200
    assert response.json()["tagged_pii_columns"] == 2


def test_compile_and_activate_returns_502_when_agent_runtime_is_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with TestClient(app) as client:
        app.state.http_client = _mock_agent_runtime(handler)

        response = client.post(
            "/admin/semantic-models/compile-and-activate",
            json={
                "tenant_id": "acme-corp",
                "data_source_name": "acme-snowflake",
                "draft": {"entities": []},
            },
        )

    assert response.status_code == 502


def test_crawl_returns_502_for_non_422_agent_runtime_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "internal error"})

    with TestClient(app) as client:
        app.state.http_client = _mock_agent_runtime(handler)

        response = client.post("/admin/data-sources/ds-1/crawl", json={"tenant_id": "acme-corp"})

    assert response.status_code == 502
