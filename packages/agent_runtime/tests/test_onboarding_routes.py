"""Tests for the self-service data source onboarding routes.

Follows `test_healthz.py`'s `with TestClient(app) as client:` pattern (the
real `lifespan()` runs, but every external client it constructs -- Neo4j,
Trino, Redis, the Postgres engine -- is lazy and never actually connects
just from being constructed). Every catalog/connector-registry function
this feature touches is patched at the point `onboarding_routes` imports
it, mirroring this repo's established "patch where imported" convention,
so no test here requires a live Postgres/Key Vault.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from navigraph_connectors.base import ConnectionTestResult

from navigraph_agents import onboarding_routes
from navigraph_agents.main import app
from navigraph_semantic_model.loader import SemanticModelValidationError


def _install_fake_secrets_provider(client: TestClient, **kwargs):
    """`lifespan()` (re)constructs `app.state.secrets_provider` every time
    a `TestClient(app)` context is entered -- same real gotcha
    `test_lineage_proxy.py` documents for `app.state.http_client`. Must be
    called AFTER entering the `with TestClient(app) as client:` block, not
    before, or this gets silently clobbered."""

    from navigraph_shared.secrets import FakeSecretsProvider

    provider = FakeSecretsProvider(**kwargs)
    app.state.secrets_provider = provider
    return provider


def test_list_connector_types_returns_real_registered_manifests() -> None:
    with TestClient(app) as client:
        response = client.get("/onboarding/connector-types")

    assert response.status_code == 200
    body = response.json()
    source_types = {info["source_type"] for info in body["source_types"]}
    assert {"snowflake", "postgres", "databricks"}.issubset(source_types)
    snowflake_info = next(i for i in body["source_types"] if i["source_type"] == "snowflake")
    assert len(snowflake_info["required_settings"]) > 0
    assert "capabilities" in snowflake_info


def test_test_connection_never_writes_to_the_app_secrets_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_result = ConnectionTestResult(success=True, message="ok", latency_ms=12.0)
    fake_connector = MagicMock()
    fake_connector.test_connection.return_value = fake_result
    monkeypatch.setattr(
        onboarding_routes, "get_connector_class", lambda source_type: MagicMock(return_value=fake_connector)
    )

    with TestClient(app) as client:
        fake_secrets = _install_fake_secrets_provider(client)

        response = client.post(
            "/onboarding/data-sources/test-connection",
            json={"source_type": "snowflake", "credential_fields": {"account": "acct-1"}},
        )

    assert response.status_code == 200
    assert response.json()["success"] is True
    # The dry run must never touch the shared, app-wide secrets provider --
    # only the throwaway in-memory one `_build_settings` constructs itself.
    assert fake_secrets.calls == []


def test_test_connection_reports_unreachable_source_as_ordinary_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raising_connector_cls(source_type: str):
        raise ValueError(f"No connector registered for source_type={source_type!r}")

    monkeypatch.setattr(onboarding_routes, "get_connector_class", _raising_connector_cls)

    with TestClient(app) as client:
        response = client.post(
            "/onboarding/data-sources/test-connection",
            json={"source_type": "bogus", "credential_fields": {}},
        )

    assert response.status_code == 400


def test_register_data_source_writes_credentials_before_touching_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111",
        tenant_id="acme-corp",
        name="acme-snowflake",
        source_type="snowflake",
        is_default=False,
    )
    register_calls = []

    def _fake_register(session, **kwargs):
        register_calls.append(kwargs)
        return created

    monkeypatch.setattr(onboarding_routes, "get_connector_class", lambda source_type: object)
    monkeypatch.setattr(onboarding_routes, "register_data_source", _fake_register)

    with TestClient(app) as client:
        fake_secrets = _install_fake_secrets_provider(client)

        response = client.post(
            "/onboarding/data-sources",
            json={
                "tenant_id": "acme-corp",
                "name": "acme-snowflake",
                "source_type": "snowflake",
                "credential_fields": {"account": "acct-1", "user": "svc"},
            },
        )

    assert response.status_code == 200
    assert response.json()["id"] == created.id
    # Both credential fields landed in the secrets provider under the same
    # computed scope, and register_data_source received a connection_ref
    # pointing at that scope, never the raw values.
    assert fake_secrets.get(scope="acme-corp__acme-snowflake", field="account") == "acct-1"
    assert fake_secrets.get(scope="acme-corp__acme-snowflake", field="user") == "svc"
    assert register_calls[0]["connection_ref"] == {"secret_scope": "acme-corp__acme-snowflake"}


def test_register_data_source_aborts_before_catalog_write_on_partial_secret_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    register_calls = []
    monkeypatch.setattr(onboarding_routes, "get_connector_class", lambda source_type: object)
    monkeypatch.setattr(
        onboarding_routes,
        "register_data_source",
        lambda session, **kwargs: register_calls.append(kwargs),
    )

    with TestClient(app) as client:
        _install_fake_secrets_provider(client, raise_exc=RuntimeError("vault unreachable"))

        response = client.post(
            "/onboarding/data-sources",
            json={
                "tenant_id": "acme-corp",
                "name": "acme-snowflake",
                "source_type": "snowflake",
                "credential_fields": {"account": "acct-1"},
            },
        )

    assert response.status_code == 502
    assert register_calls == []


def test_crawl_uses_settings_factory_when_secret_scope_present_not_zero_arg_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The regression test for the bug fix: a DataSource with a
    connection_ref.secret_scope MUST resolve its connector via
    settings_factory(connection_ref, secrets_provider), never via a bare
    zero-arg connector_cls()."""

    fake_data_source = SimpleNamespace(
        id="22222222-2222-2222-2222-222222222222",
        tenant_id="acme-corp",
        name="acme-snowflake",
        source_type="snowflake",
        connection_ref={"secret_scope": "acme-corp__acme-snowflake"},
        is_default=False,
        last_crawled_at=None,
    )
    monkeypatch.setattr(
        onboarding_routes, "list_data_sources", lambda session, **kw: [fake_data_source]
    )

    settings_factory_calls = []

    def _fake_settings_factory(connection_ref, secrets_provider):
        settings_factory_calls.append((connection_ref, secrets_provider))
        return "built-settings-sentinel"

    connector_cls_calls = []

    class _FakeConnectorCls:
        def __init__(self, settings=None):
            connector_cls_calls.append(settings)

    monkeypatch.setattr(onboarding_routes, "get_connector_class", lambda st: _FakeConnectorCls)
    monkeypatch.setattr(
        onboarding_routes, "get_settings_factory", lambda st: _fake_settings_factory
    )
    fake_crawl_result = SimpleNamespace(tables_synced=3, new_table_names=["FOO"])
    monkeypatch.setattr(
        onboarding_routes, "crawl_and_store", lambda session, **kw: fake_crawl_result
    )

    with TestClient(app) as client:
        _install_fake_secrets_provider(client)

        response = client.post(
            "/onboarding/data-sources/22222222-2222-2222-2222-222222222222/crawl",
            json={"tenant_id": "acme-corp"},
        )

    assert response.status_code == 200
    assert response.json()["tables_synced"] == 3
    # The real regression assertion: settings_factory was actually called
    # with this DataSource's own connection_ref + the shared secrets
    # provider, and the connector was constructed WITH settings, not via a
    # bare connector_cls() that would silently read global env vars.
    assert len(settings_factory_calls) == 1
    assert settings_factory_calls[0][0] == {"secret_scope": "acme-corp__acme-snowflake"}
    assert connector_cls_calls == ["built-settings-sentinel"]


def test_crawl_falls_back_to_zero_arg_construction_when_no_secret_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A DataSource with no secret_scope (pre-existing, global-env-var-based
    registration) must keep working exactly as before -- additive-only."""

    fake_data_source = SimpleNamespace(
        id="33333333-3333-3333-3333-333333333333",
        tenant_id="acme-corp",
        name="legacy-snowflake",
        source_type="snowflake",
        connection_ref={},
        is_default=True,
        last_crawled_at=None,
    )
    monkeypatch.setattr(
        onboarding_routes, "list_data_sources", lambda session, **kw: [fake_data_source]
    )
    monkeypatch.setattr(onboarding_routes, "get_settings_factory", lambda st: None)

    connector_cls_calls = []

    class _FakeConnectorCls:
        def __init__(self):
            connector_cls_calls.append("zero-arg")

    monkeypatch.setattr(onboarding_routes, "get_connector_class", lambda st: _FakeConnectorCls)
    fake_crawl_result = SimpleNamespace(tables_synced=1, new_table_names=[])
    monkeypatch.setattr(
        onboarding_routes, "crawl_and_store", lambda session, **kw: fake_crawl_result
    )

    with TestClient(app) as client:
        response = client.post(
            "/onboarding/data-sources/33333333-3333-3333-3333-333333333333/crawl",
            json={"tenant_id": "acme-corp"},
        )

    assert response.status_code == 200
    assert connector_cls_calls == ["zero-arg"]


def test_crawl_returns_404_for_unknown_data_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(onboarding_routes, "list_data_sources", lambda session, **kw: [])

    with TestClient(app) as client:
        response = client.post(
            "/onboarding/data-sources/44444444-4444-4444-4444-444444444444/crawl",
            json={"tenant_id": "acme-corp"},
        )

    assert response.status_code == 404


def test_compile_and_activate_returns_422_with_issues_on_validation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_model = MagicMock()
    monkeypatch.setattr(
        onboarding_routes,
        "compile_draft_to_semantic_model",
        lambda draft, **kw: (fake_model, []),
    )

    async def _raising_activate(model, session, opa_client):
        raise SemanticModelValidationError(["entity 'Customer' has no bindings"])

    monkeypatch.setattr(onboarding_routes, "activate_semantic_model", _raising_activate)

    with TestClient(app) as client:
        response = client.post(
            "/onboarding/semantic-models/compile-and-activate",
            json={
                "tenant_id": "acme-corp",
                "data_source_name": "acme-snowflake",
                "draft": {"entities": [], "relationships": [], "metrics": [], "sensitive_columns": []},
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"]["issues"] == ["entity 'Customer' has no bindings"]


def test_compile_and_activate_returns_422_for_a_malformed_draft() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/onboarding/semantic-models/compile-and-activate",
            json={
                "tenant_id": "acme-corp",
                "data_source_name": "acme-snowflake",
                # missing "bindings" key on the entity -- a real hand-edit mistake
                "draft": {"entities": [{"name": "Customer"}]},
            },
        )

    assert response.status_code == 422
    assert "malformed" in response.json()["detail"]["issues"][0]


def test_compile_and_activate_succeeds_and_returns_compile_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_model = MagicMock()
    monkeypatch.setattr(
        onboarding_routes,
        "compile_draft_to_semantic_model",
        lambda draft, **kw: (fake_model, ["metric 'x': entity not found -- dropped"]),
    )

    async def _fake_activate(model, session, opa_client):
        return SimpleNamespace(tagged_pii_columns=2)

    monkeypatch.setattr(onboarding_routes, "activate_semantic_model", _fake_activate)

    with TestClient(app) as client:
        response = client.post(
            "/onboarding/semantic-models/compile-and-activate",
            json={
                "tenant_id": "acme-corp",
                "data_source_name": "acme-snowflake",
                "draft": {"entities": [], "relationships": [], "metrics": [], "sensitive_columns": []},
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["tagged_pii_columns"] == 2
    assert body["compile_warnings"] == ["metric 'x': entity not found -- dropped"]
