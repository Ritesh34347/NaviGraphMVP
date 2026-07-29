"""Local marker registration for this integration test directory.

Registered here (rather than a repo-root pytest config, which does not
exist) so `pytest tests/integration/understanding_pipeline` doesn't emit an
"unknown marker" warning for `postgres_integration`/`neo4j_integration` --
mirrors `tests/integration/metadata_catalog/conftest.py` and
`tests/integration/knowledge_graph/conftest.py`'s pattern exactly.
"""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "postgres_integration: tests that require a real, reachable Postgres "
        "(the docker-compose `postgres` service). Not skipped gracefully -- "
        "this tier is documented as running against the actual docker-compose "
        "stack in a separate CI job, so a hard dependency on Postgres being up "
        "is expected and correct here.",
    )
    config.addinivalue_line(
        "markers",
        "neo4j_integration: tests that require a real, reachable Neo4j "
        "(the docker-compose `neo4j` service). Not skipped gracefully, same "
        "reasoning as postgres_integration above.",
    )
