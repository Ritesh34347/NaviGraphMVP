"""Unit tests for `navigraph_kg.client.Neo4jClient`, driver-free.

Mocks `neo4j.GraphDatabase.driver` (the real driver package IS installed in
this repo's dev environment, since `neo4j` is a hard dependency of this
package -- but these tests never open a real socket) to verify:
`test_connection` never raises even on failure (matching
`ConnectionTestResult`'s established contract), and `run`/`session_scope`
issue the expected calls against the (mocked) driver/session.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from navigraph_kg.client import Neo4jClient
from navigraph_kg.settings import KnowledgeGraphSettings


def _client_with_mock_driver() -> tuple[Neo4jClient, MagicMock]:
    client = Neo4jClient(KnowledgeGraphSettings())
    mock_driver = MagicMock()
    client._driver = mock_driver  # bypass lazy _get_driver() for these tests
    return client, mock_driver


class TestTestConnection:
    def test_never_raises_and_reports_failure_as_data(self) -> None:
        client, _mock_driver = _client_with_mock_driver()

        with patch("neo4j.GraphDatabase.driver") as mock_driver_factory:
            mock_driver_factory.side_effect = RuntimeError("connection refused")
            client._driver = None  # force _get_driver() to hit the patched factory

            result = client.test_connection()

        assert result.success is False
        assert "connection refused" in result.message

    def test_reports_success_and_runs_return_1(self) -> None:
        client, mock_driver = _client_with_mock_driver()
        mock_session = MagicMock()
        mock_driver.session.return_value.__enter__.return_value = mock_session

        result = client.test_connection()

        assert result.success is True
        assert result.latency_ms is not None
        mock_driver.verify_connectivity.assert_called_once()
        mock_session.run.assert_called_once_with("RETURN 1")
        mock_session.run.return_value.consume.assert_called_once()

    def test_catches_broad_exceptions_from_verify_connectivity(self) -> None:
        client, mock_driver = _client_with_mock_driver()
        mock_driver.verify_connectivity.side_effect = ValueError("bad credentials")

        result = client.test_connection()

        assert result.success is False
        assert "bad credentials" in result.message


class TestRun:
    def test_run_executes_cypher_with_params_and_returns_plain_dicts(self) -> None:
        client, mock_driver = _client_with_mock_driver()
        mock_session = MagicMock()
        mock_driver.session.return_value.__enter__.return_value = mock_session
        mock_session.run.return_value = [{"a": 1}, {"a": 2}]

        result = client.run("RETURN $a AS a", a=1)

        mock_session.run.assert_called_once_with("RETURN $a AS a", a=1)
        assert result == [{"a": 1}, {"a": 2}]

    def test_run_with_no_params(self) -> None:
        client, mock_driver = _client_with_mock_driver()
        mock_session = MagicMock()
        mock_driver.session.return_value.__enter__.return_value = mock_session
        mock_session.run.return_value = []

        result = client.run("MATCH (n) RETURN n")

        mock_session.run.assert_called_once_with("MATCH (n) RETURN n")
        assert result == []


class TestSessionScope:
    def test_yields_a_session_and_closes_it_on_exit(self) -> None:
        client, mock_driver = _client_with_mock_driver()
        mock_session = MagicMock()
        mock_driver.session.return_value = mock_session

        with client.session_scope() as session:
            assert session is mock_session

        mock_session.close.assert_called_once()

    def test_closes_the_session_even_if_the_body_raises(self) -> None:
        client, mock_driver = _client_with_mock_driver()
        mock_session = MagicMock()
        mock_driver.session.return_value = mock_session

        try:
            with client.session_scope():
                raise RuntimeError("boom")
        except RuntimeError:
            pass

        mock_session.close.assert_called_once()


class TestClose:
    def test_close_closes_the_driver_and_resets_it(self) -> None:
        client, mock_driver = _client_with_mock_driver()

        client.close()

        mock_driver.close.assert_called_once()
        assert client._driver is None

    def test_close_is_a_no_op_if_driver_was_never_created(self) -> None:
        client = Neo4jClient(KnowledgeGraphSettings())

        client.close()  # should not raise
