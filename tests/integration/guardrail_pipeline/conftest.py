"""Local marker registration for this integration test directory.

Registered here (rather than a repo-root pytest config, which does not
exist) so `pytest tests/integration/guardrail_pipeline` doesn't emit an
"unknown marker" warning -- mirrors
`tests/integration/query_pipeline/conftest.py`'s pattern exactly, extended
with the `opa_integration` marker this domain's real Policy Authorization
agent actually touches.
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
    config.addinivalue_line(
        "markers",
        "snowflake_integration: tests that connect to a real Snowflake account "
        "(requires real SNOWFLAKE_* env vars). Not skipped gracefully, same "
        "reasoning as postgres_integration above.",
    )
    config.addinivalue_line(
        "markers",
        "opa_integration: tests that require a real, reachable OPA (the "
        "docker-compose `opa` service) running the real, non-allow-all "
        "authz.rego policy. Not skipped gracefully, same reasoning as "
        "postgres_integration above.",
    )
