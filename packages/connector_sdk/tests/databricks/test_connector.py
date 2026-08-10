"""Unit tests for `DatabricksConnector`.

`databricks.sql.connect` is mocked throughout via `unittest.mock.patch` so
these tests never touch a real Databricks workspace. The production code
imports `databricks.sql` lazily inside `DatabricksConnector._connect()`;
tests aren't bound by that discipline.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from navigraph_connectors.base import ConnectorCapabilities
from navigraph_connectors.databricks.connector import (
    DatabricksConnector,
    _to_named_paramstyle,
)
from navigraph_connectors.databricks.settings import DatabricksSettings


def _settings(**overrides: object) -> DatabricksSettings:
    defaults = {
        "databricks_server_hostname": "adb-123.4.azuredatabricks.net",
        "databricks_http_path": "/sql/1.0/warehouses/abc123",
        "databricks_access_token": "dapi-secret",
        "databricks_catalog": "main",
    }
    defaults.update(overrides)
    return DatabricksSettings(**defaults)  # type: ignore[arg-type]


def _mock_cursor(fetch_results: list[tuple], description: list[tuple] | None = None) -> MagicMock:
    cursor = MagicMock()
    cursor.fetchall.return_value = fetch_results
    cursor.description = description
    return cursor


class TestToNamedParamstyle:
    """Pure-function tests for the real cross-driver paramstyle transform
    -- see the module docstring's "PARAMSTYLE GOTCHA" for why this exists."""

    def test_rewrites_pyformat_placeholders_to_named_placeholders(self) -> None:
        sql = "SELECT * FROM t WHERE a = %(x)s AND b = %(y)s"

        assert _to_named_paramstyle(sql) == "SELECT * FROM t WHERE a = :x AND b = :y"

    def test_leaves_sql_with_no_placeholders_unchanged(self) -> None:
        sql = "SELECT * FROM t"

        assert _to_named_paramstyle(sql) == sql


def test_connect_passes_settings_through_to_databricks_sql() -> None:
    connector = DatabricksConnector(settings=_settings())
    mock_conn = MagicMock()

    with patch("databricks.sql.connect", return_value=mock_conn) as mock_connect:
        connector._connect()

    mock_connect.assert_called_once_with(
        server_hostname="adb-123.4.azuredatabricks.net",
        http_path="/sql/1.0/warehouses/abc123",
        access_token="dapi-secret",
    )


def test_test_connection_success() -> None:
    connector = DatabricksConnector(settings=_settings())
    mock_cursor = _mock_cursor([(1,)])
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch("databricks.sql.connect", return_value=mock_conn):
        result = connector.test_connection()

    assert result.success is True
    assert result.latency_ms is not None
    mock_conn.close.assert_called_once()


def test_test_connection_failure_returns_result_not_exception() -> None:
    connector = DatabricksConnector(settings=_settings())

    with patch("databricks.sql.connect", side_effect=RuntimeError("workspace unreachable")):
        result = connector.test_connection()

    assert result.success is False
    assert "workspace unreachable" in result.message


def test_introspect_schema_requires_catalog() -> None:
    connector = DatabricksConnector(settings=_settings(databricks_catalog=""))

    with pytest.raises(ValueError, match="databricks_catalog"):
        connector.introspect_schema()


def test_introspect_schema_queries_the_catalog_scoped_information_schema() -> None:
    connector = DatabricksConnector(settings=_settings())

    tables_cursor_rows = [("default", "revenue"), ("default", "customers")]
    columns_cursor_rows = [
        ("default", "revenue", "id", "int", "NO", 1),
        ("default", "revenue", "amount", "double", "YES", 2),
        ("default", "customers", "id", "int", "NO", 1),
    ]

    mock_cursor = MagicMock()
    mock_cursor.fetchall.side_effect = [tables_cursor_rows, columns_cursor_rows]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch("databricks.sql.connect", return_value=mock_conn):
        schemas = connector.introspect_schema()

    assert len(schemas) == 1
    assert schemas[0].name == "default"
    assert {t.name for t in schemas[0].tables} == {"revenue", "customers"}

    revenue = next(t for t in schemas[0].tables if t.name == "revenue")
    assert [c.name for c in revenue.columns] == ["id", "amount"]
    assert revenue.columns[1].nullable is True

    tables_query = mock_cursor.execute.call_args_list[0].args[0]
    assert "main.information_schema.tables" in tables_query


def test_introspect_schema_filters_to_one_schema_when_configured() -> None:
    connector = DatabricksConnector(settings=_settings(databricks_schema="sales"))

    mock_cursor = MagicMock()
    mock_cursor.fetchall.side_effect = [[], []]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch("databricks.sql.connect", return_value=mock_conn):
        connector.introspect_schema()

    tables_call = mock_cursor.execute.call_args_list[0]
    assert "table_schema = :schema_filter" in tables_call.args[0]
    assert tables_call.args[1] == {"schema_filter": "sales"}


def test_execute_query_rewrites_pyformat_params_and_returns_rows_as_dicts() -> None:
    connector = DatabricksConnector(settings=_settings())

    mock_cursor = _mock_cursor(
        [("Acme", 100)],
        description=[("name", None), ("amount", None)],
    )
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch("databricks.sql.connect", return_value=mock_conn):
        result = connector.execute_query(
            "SELECT name, amount FROM revenue WHERE amount > %(min_amount)s",
            {"min_amount": 50},
        )

    assert result.columns == ["name", "amount"]
    assert result.rows == [{"name": "Acme", "amount": 100}]
    mock_cursor.execute.assert_called_once_with(
        "SELECT name, amount FROM revenue WHERE amount > :min_amount",
        {"min_amount": 50},
    )


def test_capabilities_reflect_real_unity_catalog_support() -> None:
    connector = DatabricksConnector(settings=_settings())

    assert connector.capabilities() == ConnectorCapabilities(
        supports_row_level_security=True,
        supports_column_masking=True,
        supports_query_pushdown=True,
    )


def test_required_settings_declares_the_real_fields() -> None:
    settings = {s.field: s for s in DatabricksConnector.required_settings()}

    assert settings["databricks_server_hostname"].required is True
    assert settings["databricks_http_path"].required is True
    assert settings["databricks_access_token"].required is True
    assert settings["databricks_catalog"].required is True
    assert settings["databricks_schema"].required is False


def test_required_settings_env_var_is_the_uppercased_field_name() -> None:
    setting = next(
        s
        for s in DatabricksConnector.required_settings()
        if s.field == "databricks_server_hostname"
    )

    assert setting.env_var == "DATABRICKS_SERVER_HOSTNAME"
