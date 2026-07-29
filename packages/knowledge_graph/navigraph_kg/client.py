"""Neo4j driver wrapper for the knowledge graph.

`Neo4jClient` is the sole boundary between `navigraph_kg` and the real
`neo4j` Python driver -- every other module in this package (`ontology.py`,
`ingestion/pipeline.py`, `api.py`) takes a `Neo4jClient` instance rather than
touching `neo4j.GraphDatabase` directly, so tests can substitute a mock
without ever importing the real driver.

LAZY VS. EAGER IMPORT: `neo4j` is imported lazily inside `_get_driver()`
rather than at module top, mirroring the established lazy-import convention
used elsewhere in this codebase for driver-style dependencies (see
`navigraph_connectors.snowflake.connector.SnowflakeConnector._connect` and
`navigraph_shared.llm.client.AnthropicLLMClient.__init__`). This is a
judgment call worth noting explicitly: unlike `snowflake-connector-python`
or `anthropic` in those other modules, `neo4j` is not an "optional-ish"
dependency here -- it's a hard, always-required dependency of this very
package (see `pyproject.toml`), so an eager top-level import would be
equally defensible and would never fail in a correctly-installed
environment. Lazy import was chosen anyway, for two concrete reasons: (1) it
keeps this module importable (e.g. for type-checking or for code that only
ever constructs a `Neo4jClient` inside a `unittest.mock.patch` block against
`neo4j.GraphDatabase.driver`) without eagerly opening a socket or paying the
driver's import cost at `import navigraph_kg.client` time, and (2) it keeps
every driver-style dependency in this codebase behaving identically, which
is one less thing to remember when reading any of these modules side by
side.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from navigraph_connectors.base import ConnectionTestResult

from navigraph_kg.settings import KnowledgeGraphSettings

if TYPE_CHECKING:
    # Only needed for type annotations below; importing under TYPE_CHECKING
    # keeps `neo4j` unnecessary at import time for anything that only reads
    # this module's types (e.g. a type checker), consistent with the lazy
    # runtime import in `_get_driver`.
    from neo4j import Driver, Session


class Neo4jClient:
    """Thin wrapper around the official `neo4j` Python driver.

    Reuses `navigraph_connectors.base.ConnectionTestResult` directly for
    `test_connection()`'s return shape (rather than defining a parallel
    type) and follows that same class's never-raise contract: no failure
    mode of `test_connection()` should ever propagate as an exception.
    """

    def __init__(self, settings: KnowledgeGraphSettings | None = None) -> None:
        self._settings = settings or KnowledgeGraphSettings()
        self._driver: Driver | None = None

    def _get_driver(self) -> Driver:
        if self._driver is None:
            import neo4j

            self._driver = neo4j.GraphDatabase.driver(
                self._settings.neo4j_uri,
                auth=(self._settings.neo4j_user, self._settings.neo4j_password),
            )
        return self._driver

    def test_connection(self) -> ConnectionTestResult:
        # Per the `Connector.test_connection` contract this mirrors, this
        # method must never raise -- any failure (bad credentials,
        # unreachable server, missing driver, etc.) is reported as data via
        # `ConnectionTestResult(success=False, ...)`.
        start = time.monotonic()
        try:
            driver = self._get_driver()
            driver.verify_connectivity()
            with driver.session() as session:
                session.run("RETURN 1").consume()
        except Exception as exc:  # noqa: BLE001 - contract requires catching everything
            return ConnectionTestResult(success=False, message=str(exc))

        latency_ms = (time.monotonic() - start) * 1000
        return ConnectionTestResult(
            success=True,
            message="Connected successfully",
            latency_ms=latency_ms,
        )

    def run(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        """Run `cypher` with `params` and return every record as a plain dict.

        Opens and closes a session per call rather than reusing one across
        calls -- simple and correct for this package's usage pattern (a
        handful of discrete statements per ingestion stage or read-API call,
        never a long-running transaction), matching `session_scope`'s
        equivalent one-session-per-call-site pattern below.
        """

        driver = self._get_driver()
        with driver.session() as session:
            result = session.run(cypher, **params)
            return [dict(record) for record in result]

    @contextmanager
    def session_scope(self) -> Iterator[Session]:
        """Yield a real Neo4j session.

        Naming mirrors `navigraph_catalog.db.session_scope` for consistency
        across packages, even though the underlying session object here is a
        `neo4j.Session`, not a SQLAlchemy `Session` -- there is no
        commit/rollback semantics to manage (Cypher writes inside a
        `session.run()` auto-commit by default), so this exists purely to
        give callers that want direct driver access (e.g. a multi-statement
        transaction) the same context-manager ergonomics as the catalog
        package, with the driver/session lifecycle still owned here.
        """

        driver = self._get_driver()
        session = driver.session()
        try:
            yield session
        finally:
            session.close()

    def close(self) -> None:
        """Close the underlying driver, if one was ever created."""

        if self._driver is not None:
            self._driver.close()
            self._driver = None
