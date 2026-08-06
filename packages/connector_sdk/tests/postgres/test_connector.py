"""Unit tests for `PostgresConnector`.

`psycopg.connect` is mocked throughout via `unittest.mock.patch` so these
tests never touch a real Postgres instance. The production code imports
`psycopg` lazily inside `PostgresConnector._connect()`; tests aren't bound
by that discipline.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from navigraph_connectors.base import ConnectorCapabilities
from navigraph_connectors.postgres.connector import PostgresConnector
from navigraph_connectors.postgres.settings import PostgresSettings


def _settings() -> PostgresSettings:
    return PostgresSettings(
        source_postgres_host="db.example.com",
        source_postgres_port=5432,
        source_postgres_database="analytics",
        source_postgres_user="reader",
        source_postgres_password="hunter2",
    )


def _mock_cursor(fetch_results: list[tuple], description: list[tuple] | None = None) -> MagicMock:
    cursor = MagicMock()
    cursor.fetchall.return_value = fetch_results
    cursor.description = description
    return cursor


def test_connect_passes_settings_through_to_psycopg() -> None:
    connector = PostgresConnector(settings=_settings())
    mock_conn = MagicMock()

    with patch("psycopg.connect", return_value=mock_conn) as mock_connect:
        connector._connect()

    mock_connect.assert_called_once_with(
        host="db.example.com",
        port=5432,
        dbname="analytics",
        user="reader",
        password="hunter2",
        sslmode="prefer",
    )


def test_test_connection_success() -> None:
    connector = PostgresConnector(settings=_settings())
    mock_cursor = _mock_cursor([(1,)])
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch("psycopg.connect", return_value=mock_conn):
        result = connector.test_connection()

    assert result.success is True
    assert result.latency_ms is not None
    assert result.latency_ms >= 0
    mock_conn.close.assert_called_once()


def test_test_connection_failure_returns_result_not_exception() -> None:
    connector = PostgresConnector(settings=_settings())

    with patch("psycopg.connect", side_effect=RuntimeError("connection refused")):
        result = connector.test_connection()

    assert result.success is False
    assert "connection refused" in result.message


def test_introspect_schema_excludes_system_schemas_and_assembles_descriptors() -> None:
    connector = PostgresConnector(settings=_settings())

    tables_cursor_rows = [
        ("public", "revenue"),
        ("public", "customers"),
    ]
    columns_cursor_rows = [
        ("public", "revenue", "id", "integer", "NO", 1),
        ("public", "revenue", "amount", "numeric", "YES", 2),
        ("public", "customers", "id", "integer", "NO", 1),
    ]

    mock_cursor = MagicMock()
    mock_cursor.fetchall.side_effect = [tables_cursor_rows, columns_cursor_rows]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch("psycopg.connect", return_value=mock_conn):
        schemas = connector.introspect_schema()

    assert len(schemas) == 1
    schema = schemas[0]
    assert schema.name == "public"
    assert {t.name for t in schema.tables} == {"revenue", "customers"}

    revenue = next(t for t in schema.tables if t.name == "revenue")
    assert [c.name for c in revenue.columns] == ["id", "amount"]
    assert revenue.columns[0].nullable is False
    assert revenue.columns[1].nullable is True
    assert revenue.columns[1].data_type == "numeric"

    # `information_schema` carries no per-column comment for Postgres --
    # description is honestly None, never fabricated.
    assert revenue.columns[0].description is None

    # The real query text must exclude Postgres's own system schemas.
    tables_query = mock_cursor.execute.call_args_list[0].args[0]
    assert "pg_catalog" in tables_query
    assert "information_schema" in tables_query


def test_execute_query_returns_rows_as_dicts() -> None:
    connector = PostgresConnector(settings=_settings())

    mock_cursor = _mock_cursor(
        [("Acme", 100), ("Globex", 200)],
        description=[("name", None), ("amount", None)],
    )
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch("psycopg.connect", return_value=mock_conn):
        result = connector.execute_query(
            "SELECT name, amount FROM revenue WHERE amount > %(min_amount)s",
            {"min_amount": 50},
        )

    assert result.columns == ["name", "amount"]
    assert result.rows == [
        {"name": "Acme", "amount": 100},
        {"name": "Globex", "amount": 200},
    ]
    assert result.row_count == 2
    mock_cursor.execute.assert_called_once_with(
        "SELECT name, amount FROM revenue WHERE amount > %(min_amount)s",
        {"min_amount": 50},
    )


def test_capabilities_reflect_real_postgres_support() -> None:
    connector = PostgresConnector(settings=_settings())

    assert connector.capabilities() == ConnectorCapabilities(
        supports_row_level_security=True,
        supports_column_masking=False,
        supports_query_pushdown=True,
    )
