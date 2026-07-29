"""Local marker registration for this adversarial test directory.

Registered here (rather than a repo-root pytest config, which does not
exist) so `pytest tests/security` doesn't emit an "unknown marker" warning
-- mirrors `tests/integration/query_pipeline/conftest.py`'s pattern
exactly.
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
        "opa_integration: tests that require a real, reachable OPA (the "
        "docker-compose `opa` service) running the real, non-allow-all "
        "authz.rego policy. Not skipped gracefully, same reasoning as "
        "postgres_integration above -- these tests ARE the real adversarial "
        "proof this policy denies correctly, not an optional extra.",
    )
