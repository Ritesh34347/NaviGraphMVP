"""Local marker registration for this integration test directory.

Mirrors `tests/integration/query_pipeline/conftest.py`'s pattern exactly.
"""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "postgres_integration: tests that require a real, reachable Postgres. "
        "Not skipped gracefully -- this tier is documented as running against "
        "real Postgres instances in a separate CI job, so a hard dependency on "
        "Postgres being up is expected and correct here.",
    )
