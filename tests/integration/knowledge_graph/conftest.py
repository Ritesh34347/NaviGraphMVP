"""Local marker registration for this integration test directory.

Registered here (rather than a repo-root pytest config, which does not
exist) so `pytest tests/integration/knowledge_graph` doesn't emit an
"unknown marker" warning for `neo4j_integration` or `snowflake_integration`
-- mirrors `tests/integration/metadata_catalog/conftest.py`'s pattern
exactly. `snowflake_integration` is ALSO registered in
`packages/connector_sdk/pyproject.toml` and `packages/pyproject.toml`, but
neither of those `pyproject.toml` files apply when pytest's rootdir
resolves to somewhere under `tests/integration/` -- there is no root-level
`pyproject.toml`/`pytest.ini` to be found while searching upward from a
test file under here, so `packages/pyproject.toml` (a sibling directory, not
an ancestor of this one) never becomes the active ini file for this
directory's own test runs. It must be re-registered locally, same reasoning
as `postgres_integration` in the sibling `metadata_catalog` integration
test directory.
"""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "neo4j_integration: tests that require a real, reachable Neo4j "
        "(the docker-compose `neo4j` service). Not skipped gracefully -- "
        "this tier is documented as running against the actual docker-compose "
        "stack in a separate CI job, so a hard dependency on Neo4j being up "
        "is expected and correct here.",
    )
    config.addinivalue_line(
        "markers",
        "snowflake_integration: tests that connect to a real Snowflake account "
        "(excluded gracefully via `@pytest.mark.skipif` when SNOWFLAKE_ACCOUNT "
        "isn't set -- see packages/connector_sdk/tests/snowflake/"
        "test_connector_integration.py for the exact pattern this mirrors).",
    )
