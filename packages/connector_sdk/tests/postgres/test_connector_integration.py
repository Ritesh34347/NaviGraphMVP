"""Real integration test for `PostgresConnector` against an actual Postgres
data source.

Marked `postgres_integration` (registered in
`packages/connector_sdk/pyproject.toml` under
`[tool.pytest.ini_options].markers`). A plain `pytest` run never executes
this file's assertions against a real instance: the test is guarded by
`@pytest.mark.skipif` on `SOURCE_POSTGRES_HOST` being unset, so it *skips*
cleanly (not an error, not a failure) when no credentials are present --
same precedent as `snowflake`'s own integration test. No real Postgres
data source has been registered against this connector yet (see
LIMITATIONS.md); this test exists so the moment one is, verifying it is
a one-line `pytest -m postgres_integration` away.

    SOURCE_POSTGRES_HOST=... SOURCE_POSTGRES_DATABASE=... \\
        SOURCE_POSTGRES_USER=... SOURCE_POSTGRES_PASSWORD=... \\
        pytest -m postgres_integration
"""

from __future__ import annotations

import os

import pytest
from navigraph_connectors.postgres.connector import PostgresConnector
from navigraph_connectors.postgres.settings import PostgresSettings

pytestmark = pytest.mark.postgres_integration


@pytest.mark.skipif(
    not os.environ.get("SOURCE_POSTGRES_HOST"),
    reason="requires real SOURCE_POSTGRES_* env vars",
)
def test_connector_reaches_a_real_postgres_instance_and_introspects_schema() -> None:
    connector = PostgresConnector(PostgresSettings())

    result = connector.test_connection()
    assert result.success is True

    schemas = connector.introspect_schema()
    assert len(schemas) > 0
