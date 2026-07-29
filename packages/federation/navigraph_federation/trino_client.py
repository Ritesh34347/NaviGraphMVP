"""Real Trino DB-API client wrapper.

`TrinoClient` is the sole boundary between `navigraph_federation` and the
real `trino` Python package -- callers take a `TrinoClient` instance rather
than touching `trino.dbapi` directly, so tests can substitute a mock without
ever importing the real driver.

LAZY VS. EAGER IMPORT: `trino` is imported lazily inside `_connect()` rather
than at module top, mirroring the established lazy-import convention used
elsewhere in this codebase for driver-style dependencies (see
`navigraph_connectors.snowflake.connector.SnowflakeConnector._connect` and
`navigraph_kg.client.Neo4jClient._get_driver`, whose docstring lays out the
rationale in full). The connection itself is also LAZY, not eager --
`__init__` never opens a socket; a real `trino.dbapi.Connection` is only
created the first time `execute_query`/`test_connection` actually needs one,
mirroring `Neo4jClient.__init__`/`_get_driver`'s "construct now, connect
later" split exactly (rather than a fresh connection per call, the way
`SnowflakeConnector` does it -- `Neo4jClient`'s lazy-*driver*, reused-across-
calls pattern is the one this module was asked to mirror).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from navigraph_connectors.base import ConnectionTestResult, QueryResult

from navigraph_federation.settings import FederationSettings

if TYPE_CHECKING:
    # Only needed for type annotations below; importing under TYPE_CHECKING
    # keeps `trino` unnecessary at import time for anything that only reads
    # this module's types (e.g. a type checker), consistent with the lazy
    # runtime import in `_connect`.
    from trino.dbapi import Connection


class TrinoClient:
    """Thin wrapper around the official `trino` Python DB-API client.

    Reuses `navigraph_connectors.base.QueryResult` / `ConnectionTestResult`
    directly for `execute_query()` / `test_connection()`'s return shapes
    (rather than defining parallel types), so a `TrinoClient` result is
    interchangeable with a `Connector.execute_query()` result for any
    downstream caller (e.g. the Data Federation agent) that just wants
    `columns`/`rows`/`row_count`. Follows `Connector`'s documented contract
    for the two methods it mirrors: `test_connection` must never raise;
    `execute_query` may raise a real exception on failure (callers -- see
    `navigraph_agents.query.data_federation.agent.DataFederationAgent` --
    are expected to catch it).
    """

    def __init__(self, settings: FederationSettings | None = None) -> None:
        self._settings = settings or FederationSettings()
        self._connection: Connection | None = None

    @property
    def catalog(self) -> str:
        """The Trino catalog this client is configured to query -- exposed
        so a caller building/rewriting SQL for this client (see
        `dialect.rewrite_sql_for_trino` and
        `navigraph_agents.query.data_federation.agent.DataFederationAgent`)
        doesn't need to reach into `_settings` directly."""

        return self._settings.trino_catalog

    def _connect(self) -> Connection:
        if self._connection is None:
            # Imported lazily so importing this module never requires the
            # `trino` package to succeed at import time in every code path
            # -- e.g. unit tests that mock the connection entirely.
            import trino.dbapi

            self._connection = trino.dbapi.connect(
                host=self._settings.trino_host,
                port=self._settings.trino_port,
                user=self._settings.trino_user,
                catalog=self._settings.trino_catalog,
            )
        return self._connection

    def test_connection(self) -> ConnectionTestResult:
        # Per the `Connector.test_connection` contract this mirrors, this
        # method must never raise -- any failure (unreachable coordinator,
        # missing driver, bad catalog, etc.) is reported as data via
        # `ConnectionTestResult(success=False, ...)`.
        start = time.monotonic()
        try:
            conn = self._connect()
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT 1")
                cursor.fetchall()
            finally:
                cursor.close()
        except Exception as exc:  # noqa: BLE001 - contract requires catching everything
            return ConnectionTestResult(success=False, message=str(exc))

        latency_ms = (time.monotonic() - start) * 1000
        return ConnectionTestResult(
            success=True,
            message="Connected successfully",
            latency_ms=latency_ms,
        )

    def execute_query(self, sql: str, params: dict[str, Any] | None = None) -> QueryResult:
        """Run `sql` against Trino and return its results.

        May raise a real exception on failure -- see the class docstring's
        contract. `params` is accepted for interface parity with
        `Connector.execute_query`/`SnowflakeConnector.execute_query`, but
        the Trino DB-API driver's `cursor.execute` takes a positional
        parameter sequence, not a named-parameter mapping, so a non-empty
        `params` is passed through as a single-element `(params,)` sequence
        only if the driver's paramstyle expects it; for NaviGraph's actual
        usage (SQL generation always inlines literal values -- see
        `dialect.rewrite_sql_for_trino`'s docstring) `params` is normally
        `None`.
        """

        conn = self._connect()
        cursor = conn.cursor()
        try:
            cursor.execute(sql, params)
            fetched_rows = cursor.fetchall()
            columns = (
                [col[0] for col in cursor.description] if cursor.description else []
            )
        finally:
            cursor.close()

        rows = [dict(zip(columns, row, strict=True)) for row in fetched_rows]
        return QueryResult(columns=columns, rows=rows, row_count=len(rows))

    def close(self) -> None:
        """Close the underlying connection, if one was ever created."""

        if self._connection is not None:
            self._connection.close()
            self._connection = None
