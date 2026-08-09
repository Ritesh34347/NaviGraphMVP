"""Unit tests for `PostgresConnector`.

`psycopg2.connect` is mocked throughout via `unittest.mock.patch` so these
tests never touch a real Postgres instance. `patch()` imports `psycopg2`
itself as needed to resolve the dotted path, so no explicit import of it is
required here. The production code imports `psycopg2` lazily inside
`PostgresConnector._connect()`; tests aren't bound by that discipline.
Mirrors `packages/connector_sdk/tests/snowflake/test_connector.py`'s
structure and coverage exactly, adjusted for this connector's own real,
verified introspection query shape.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from navigraph_connectors.base import ConnectorCapabilities
from navigraph_connectors.postgres.connector import PostgresConnector
from navigraph_connectors.postgres.settings import PostgresSettings


def _settings() -> PostgresSettings:
    return PostgresSettings(
        customer_postgres_host="db.example.com",
        customer_postgres_port=5432,
        customer_postgres_database="sample",
        customer_postgres_user="user-1",
        customer_postgres_password="hunter2",
    )


def _mock_cursor(fetch_results: list[tuple], description: list[tuple] | None = None) -> MagicMock:
    cursor = MagicMock()
    cursor.fetchall.return_value = fetch_results
    cursor.description = description
    return cursor


def test_test_connection_success() -> None:
    connector = PostgresConnector(settings=_settings())
    mock_cursor = _mock_cursor([(1,)])
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch("psycopg2.connect", return_value=mock_conn) as mock_connect:
        result = connector.test_connection()

    mock_connect.assert_called_once_with(
        host="db.example.com",
        port=5432,
        dbname="sample",
        user="user-1",
        password="hunter2",
        sslmode="prefer",
    )
    assert result.success is True
    assert result.latency_ms is not None
    assert result.latency_ms >= 0
    mock_conn.close.assert_called_once()


def test_test_connection_failure_returns_result_not_exception() -> None:
    connector = PostgresConnector(settings=_settings())

    with patch("psycopg2.connect", side_effect=RuntimeError("connection refused")):
        result = connector.test_connection()

    assert result.success is False
    assert "connection refused" in result.message


def test_test_connection_failure_when_cursor_execute_raises() -> None:
    connector = PostgresConnector(settings=_settings())
    mock_cursor = MagicMock()
    mock_cursor.execute.side_effect = RuntimeError("network unreachable")
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch("psycopg2.connect", return_value=mock_conn):
        result = connector.test_connection()

    assert result.success is False
    assert "network unreachable" in result.message


def test_introspect_schema_assembles_descriptors_from_cursor_rows() -> None:
    connector = PostgresConnector(settings=_settings())

    tables_cursor_rows = [
        ("sales", "customers", 2),
        ("sales", "orders", 3),
    ]
    columns_cursor_rows = [
        ("sales", "customers", "customer_id", "integer", False, 1, None),
        (
            "sales",
            "customers",
            "region",
            "character varying(100)",
            True,
            2,
            "Sales region for this customer",
        ),
        ("sales", "orders", "order_id", "integer", False, 1, None),
    ]

    mock_cursor = MagicMock()
    # execute() is called twice (tables query, then columns query);
    # fetchall() must return the matching result set each time.
    mock_cursor.fetchall.side_effect = [tables_cursor_rows, columns_cursor_rows]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch("psycopg2.connect", return_value=mock_conn):
        schemas = connector.introspect_schema()

    assert len(schemas) == 1
    schema = schemas[0]
    assert schema.name == "sales"
    assert {t.name for t in schema.tables} == {"customers", "orders"}

    customers = next(t for t in schema.tables if t.name == "customers")
    assert customers.row_count_estimate == 2
    assert [c.name for c in customers.columns] == ["customer_id", "region"]
    assert customers.columns[0].nullable is False
    assert customers.columns[1].nullable is True
    assert customers.columns[1].description == "Sales region for this customer"
    assert customers.columns[1].data_type == "character varying(100)"

    orders = next(t for t in schema.tables if t.name == "orders")
    assert orders.row_count_estimate == 3
    assert [c.name for c in orders.columns] == ["order_id"]

    mock_conn.close.assert_called_once()


def test_execute_query_returns_rows_as_dicts() -> None:
    connector = PostgresConnector(settings=_settings())

    mock_cursor = _mock_cursor(
        [("Acme", 100), ("Globex", 200)],
        description=[("customer_name", None), ("order_total", None)],
    )
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch("psycopg2.connect", return_value=mock_conn):
        result = connector.execute_query(
            "SELECT customer_name, order_total FROM sales.orders WHERE region = %(region)s",
            {"region": "EAST"},
        )

    assert result.columns == ["customer_name", "order_total"]
    assert result.rows == [
        {"customer_name": "Acme", "order_total": 100},
        {"customer_name": "Globex", "order_total": 200},
    ]
    assert result.row_count == 2
    mock_cursor.execute.assert_called_once_with(
        "SELECT customer_name, order_total FROM sales.orders WHERE region = %(region)s",
        {"region": "EAST"},
    )


def test_capabilities_reflect_real_postgres_support() -> None:
    connector = PostgresConnector(settings=_settings())

    assert connector.capabilities() == ConnectorCapabilities(
        supports_row_level_security=True,
        supports_column_masking=False,
        supports_query_pushdown=True,
    )
