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
    get_connector_class,
    list_registered_source_types,
    register_connector,
)


class _FakeConnector(Connector):
    """Trivial `Connector` implementation used only to exercise the registry."""

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
    """Snapshot and restore `_REGISTRY` so tests don't leak state into each other."""

    original = dict(_REGISTRY)
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()
    _REGISTRY.update(original)


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
