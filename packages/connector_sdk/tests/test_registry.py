"""Tests for the connector registry mechanism itself.

Deliberately uses a small fake `Connector` subclass rather than
`SnowflakeConnector`, so these tests stay about the registry (register /
look up / error on unknown / list registered) and don't depend on the
Snowflake driver at all.
"""

from __future__ import annotations

from typing import Any

import pytest
from navigraph_connectors.base import (
    ConnectionTestResult,
    Connector,
    ConnectorCapabilities,
    QueryResult,
    SchemaDescriptor,
)
from navigraph_connectors.registry import (
    _REGISTRY,
    _SETTINGS_FACTORIES,
    build_connector,
    get_connector_class,
    list_registered_source_types,
    register_connector,
)
from navigraph_shared.secrets import FakeSecretsProvider


class _FakeConnector(Connector):
    """Trivial `Connector` implementation used only to exercise the registry.

    Accepts an optional `settings` kwarg (recorded on the instance) so
    `build_connector` tests can assert on exactly what a settings factory
    resolved and passed through, mirroring how every real connector
    (`SnowflakeConnector`, `PostgresConnector`) accepts its own `Settings`.
    """

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings

    def test_connection(self) -> ConnectionTestResult:
        return ConnectionTestResult(success=True, message="ok")

    def introspect_schema(self) -> list[SchemaDescriptor]:
        return []

    def execute_query(self, sql: str, params: dict[str, Any] | None = None) -> QueryResult:
        return QueryResult(columns=[], rows=[], row_count=0)

    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            supports_row_level_security=False,
            supports_column_masking=False,
            supports_query_pushdown=False,
        )


@pytest.fixture(autouse=True)
def _clean_registry():
    """Snapshot and restore `_REGISTRY`/`_SETTINGS_FACTORIES` so tests don't
    leak state into each other."""

    original_registry = dict(_REGISTRY)
    original_factories = dict(_SETTINGS_FACTORIES)
    _REGISTRY.clear()
    _SETTINGS_FACTORIES.clear()
    yield
    _REGISTRY.clear()
    _REGISTRY.update(original_registry)
    _SETTINGS_FACTORIES.clear()
    _SETTINGS_FACTORIES.update(original_factories)


def test_register_and_get_connector_class() -> None:
    register_connector("fake_source", _FakeConnector)

    assert get_connector_class("fake_source") is _FakeConnector


def test_get_connector_class_raises_clear_error_for_unregistered_type() -> None:
    register_connector("fake_source", _FakeConnector)

    with pytest.raises(ValueError) as exc_info:
        get_connector_class("unknown_source")

    message = str(exc_info.value)
    assert "unknown_source" in message
    assert "fake_source" in message


def test_list_registered_source_types_reflects_registrations() -> None:
    assert list_registered_source_types() == []

    register_connector("fake_source", _FakeConnector)
    register_connector("another_source", _FakeConnector)

    assert list_registered_source_types() == ["another_source", "fake_source"]


def test_reregistering_same_source_type_overwrites() -> None:
    class _OtherFakeConnector(_FakeConnector):
        pass

    register_connector("fake_source", _FakeConnector)
    register_connector("fake_source", _OtherFakeConnector)

    assert get_connector_class("fake_source") is _OtherFakeConnector


def test_build_connector_without_a_settings_factory_falls_back_to_zero_arg_construction() -> None:
    """A source_type registered without a settings_factory keeps this SDK's
    pre-item-21 behavior exactly -- no settings passed, connector reads its
    own global defaults."""

    register_connector("fake_source", _FakeConnector)

    connector = build_connector(
        "fake_source", connection_ref={}, secrets=FakeSecretsProvider()
    )

    assert isinstance(connector, _FakeConnector)
    assert connector.settings is None


def test_build_connector_resolves_real_per_data_source_settings_via_factory() -> None:
    """The actual item-21 fix: connection_ref + a SecretsProvider resolve a
    real, connector-specific Settings instance, not a global env read."""

    captured_calls: list[tuple[dict[str, Any], Any]] = []

    def _settings_factory(connection_ref: dict[str, Any], secrets: Any) -> dict[str, Any]:
        captured_calls.append((connection_ref, secrets))
        return {
            "account": secrets.get(scope=connection_ref["secret_scope"], field="account"),
        }

    register_connector("fake_source", _FakeConnector, _settings_factory)
    secrets = FakeSecretsProvider({("tenant_a_fake", "account"): "acct-a"})

    connector = build_connector(
        "fake_source",
        connection_ref={"secret_scope": "tenant_a_fake"},
        secrets=secrets,
    )

    assert connector.settings == {"account": "acct-a"}
    assert captured_calls == [({"secret_scope": "tenant_a_fake"}, secrets)]


def test_build_connector_two_data_sources_of_the_same_source_type_get_distinct_settings() -> None:
    """The real scenario item 21 exists to fix: two DataSource rows of the
    same source_type must resolve to genuinely distinct credentials."""

    def _settings_factory(connection_ref: dict[str, Any], secrets: Any) -> dict[str, Any]:
        scope = connection_ref["secret_scope"]
        return {"account": secrets.get(scope=scope, field="account")}

    register_connector("fake_source", _FakeConnector, _settings_factory)
    secrets = FakeSecretsProvider(
        {("tenant_a_fake", "account"): "acct-a", ("tenant_b_fake", "account"): "acct-b"}
    )

    connector_a = build_connector(
        "fake_source", connection_ref={"secret_scope": "tenant_a_fake"}, secrets=secrets
    )
    connector_b = build_connector(
        "fake_source", connection_ref={"secret_scope": "tenant_b_fake"}, secrets=secrets
    )

    assert connector_a.settings == {"account": "acct-a"}
    assert connector_b.settings == {"account": "acct-b"}


def test_build_connector_raises_for_unregistered_source_type() -> None:
    with pytest.raises(ValueError, match="unknown_source"):
        build_connector("unknown_source", connection_ref={}, secrets=FakeSecretsProvider())
