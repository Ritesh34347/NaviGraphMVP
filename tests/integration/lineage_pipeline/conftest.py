"""Local marker registration for this integration test directory.

Registered here (rather than a repo-root pytest config, which does not
exist) so `pytest tests/integration/lineage_pipeline` doesn't emit an
"unknown marker" warning -- mirrors every other integration directory's
identical pattern. This chain only needs live Postgres (Conversation and
Intent Understanding use `FakeLLMClient`, Metadata Discovery reads the
already-crawled catalog) -- no Neo4j/Snowflake/OPA dependency, unlike the
Understanding/Query/Guardrail/Insight pipeline tests.
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
