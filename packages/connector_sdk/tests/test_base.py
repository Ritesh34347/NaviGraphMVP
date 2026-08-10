"""Unit tests for `navigraph_connectors.base`'s `Connector.required_settings()`
default (Phase 6 of the configurable-platform build plan).

Deliberately NOT `@abstractmethod` -- see `base.py`'s own docstring for
why (adding a genuinely required abstract method would break every
existing `Connector` subclass across the repo, including test doubles
that have no reason to know about this manifest). These tests confirm
the safe, empty-list default a bare subclass gets for free.
"""

from __future__ import annotations

from navigraph_connectors.base import (
    ConnectionTestResult,
    Connector,
    ConnectorCapabilities,
    QueryResult,
    RequiredSetting,
    SchemaDescriptor,
)


class _BareConnector(Connector):
    """Implements only the four ORIGINAL abstract methods -- no
    `required_settings()` override at all, exactly like every pre-Phase-6
    test double elsewhere in this repo."""

    def test_connection(self) -> ConnectionTestResult:
        return ConnectionTestResult(success=True, message="ok")

    def introspect_schema(self) -> list[SchemaDescriptor]:
        return []

    def execute_query(self, sql: str, params: dict | None = None) -> QueryResult:
        return QueryResult(columns=[], rows=[], row_count=0)

    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            supports_row_level_security=False,
            supports_column_masking=False,
            supports_query_pushdown=False,
        )


def test_a_bare_subclass_with_no_override_declares_nothing() -> None:
    assert _BareConnector.required_settings() == []


def test_required_setting_env_var_is_derived_from_field() -> None:
    setting = RequiredSetting(field="my_custom_field", description="a test field")

    assert setting.env_var == "MY_CUSTOM_FIELD"


def test_required_setting_defaults_to_required_true() -> None:
    setting = RequiredSetting(field="x", description="d")

    assert setting.required is True
    assert setting.condition is None
