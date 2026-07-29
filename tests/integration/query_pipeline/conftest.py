"""Local marker registration for this integration test directory.

Registered here (rather than a repo-root pytest config, which does not
exist) so `pytest tests/integration/query_pipeline` doesn't emit an "unknown
marker" warning -- mirrors `tests/integration/understanding_pipeline/conftest.py`'s
pattern exactly, extended with the two new backends this domain's real
execution actually touches: a real Snowflake account (via the direct
connector route) and a real Redis instance (the Caching agent).
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
        "reasoning as postgres_integration above -- this test IS the real "
        "Snowflake execution proof for the Query domain, not an optional extra.",
    )
    config.addinivalue_line(
        "markers",
        "redis_integration: tests that require a real, reachable Redis (the "
        "docker-compose `redis` service). Not skipped gracefully, same "
        "reasoning as postgres_integration above.",
    )
