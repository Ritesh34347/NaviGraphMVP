"""Unit tests for `TrinoClient`.

`trino.dbapi.connect` is mocked throughout via `unittest.mock.patch` so
these tests never touch a real Trino coordinator -- mirroring
`packages/connector_sdk/tests/snowflake/test_connector.py`'s mocking
pattern for `snowflake.connector.connect`. The production code imports
`trino.dbapi` lazily inside `TrinoClient._connect()`; tests aren't bound by
that discipline.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from navigraph_federation.settings import FederationSettings
from navigraph_federation.trino_client import TrinoClient


def _settings() -> FederationSettings:
    return FederationSettings(
        trino_host="trino-test-host",
        trino_port=9999,
        trino_user="test-user",
        trino_catalog="snowflake",
    )


def _mock_cursor(fetch_results: list[tuple], description: list[tuple] | None = None) -> MagicMock:
    cursor = MagicMock()
    cursor.fetchall.return_value = fetch_results
    cursor.description = description
    return cursor


def test_connect_is_lazy_and_reused_across_calls() -> None:
    client = TrinoClient(settings=_settings())
    mock_cursor = _mock_cursor([(1,)], description=[("_col1", None)])
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch("trino.dbapi.connect", return_value=mock_conn) as mock_connect:
        # Constructing the client must not have connected yet.
        mock_connect.assert_not_called()

        client.test_connection()
        client.execute_query("SELECT 1")

    # A single connection is created and reused, not one per call --
    # mirroring Neo4jClient's lazy-driver, reused-across-calls pattern.
    mock_connect.assert_called_once_with(
        host="trino-test-host",
        port=9999,
        user="test-user",
        catalog="snowflake",
    )


def test_test_connection_success() -> None:
    client = TrinoClient(settings=_settings())
    mock_cursor = _mock_cursor([(1,)])
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch("trino.dbapi.connect", return_value=mock_conn):
        result = client.test_connection()

    assert result.success is True
    assert result.latency_ms is not None
    assert result.latency_ms >= 0


def test_test_connection_failure_returns_result_not_exception() -> None:
    client = TrinoClient(settings=_settings())

    with patch("trino.dbapi.connect", side_effect=RuntimeError("coordinator unreachable")):
        result = client.test_connection()

    assert result.success is False
    assert "coordinator unreachable" in result.message


def test_execute_query_returns_rows_as_dicts() -> None:
    client = TrinoClient(settings=_settings())
    mock_cursor = _mock_cursor(
        [("Acme", 100), ("Globex", 200)],
        description=[("NAME", None), ("AMOUNT", None)],
    )
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch("trino.dbapi.connect", return_value=mock_conn):
        result = client.execute_query("SELECT name, amount FROM snowflake.analytics.revenue")

    assert result.columns == ["NAME", "AMOUNT"]
    assert result.rows == [
        {"NAME": "Acme", "AMOUNT": 100},
        {"NAME": "Globex", "AMOUNT": 200},
    ]
    assert result.row_count == 2


def test_execute_query_raises_on_cursor_failure() -> None:
    """`execute_query` MAY raise -- matching `Connector.execute_query`'s
    documented contract that this client's shape mirrors."""

    client = TrinoClient(settings=_settings())
    mock_cursor = MagicMock()
    mock_cursor.execute.side_effect = RuntimeError("query failed")
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch("trino.dbapi.connect", return_value=mock_conn):
        try:
            client.execute_query("SELECT 1")
        except RuntimeError as exc:
            assert "query failed" in str(exc)
        else:
            raise AssertionError("expected execute_query to raise")


def test_close_closes_and_clears_connection() -> None:
    client = TrinoClient(settings=_settings())
    mock_cursor = _mock_cursor([(1,)])
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch("trino.dbapi.connect", return_value=mock_conn):
        client.test_connection()
        client.close()

    mock_conn.close.assert_called_once()

    # Closing again without a live connection must be a no-op, not a crash.
    client.close()
