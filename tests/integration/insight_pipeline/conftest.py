"""Local marker registration for this integration test directory.

Registered here (rather than a repo-root pytest config, which does not
exist) so `pytest tests/integration/insight_pipeline` doesn't emit an
"unknown marker" warning -- mirrors
`tests/integration/guardrail_pipeline/conftest.py`'s pattern exactly. This
chain touches the same live dependencies guardrail_pipeline does (Postgres
catalog, Neo4j, real Snowflake, real OPA), plus real Data Federation
execution this time (guardrail_pipeline deliberately stopped short of it).
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
