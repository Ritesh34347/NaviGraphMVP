"""Local marker registration for this integration test directory.

Registered here (rather than a repo-root pytest config, which does not
exist) so `pytest tests/integration/metadata_catalog` doesn't emit an
"unknown marker" warning for `postgres_integration`.
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
