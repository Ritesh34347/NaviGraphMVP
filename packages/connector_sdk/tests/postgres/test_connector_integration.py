"""Real integration test for `PostgresConnector` against an actual Postgres instance.

Marked `postgres_integration` (registered in
`packages/connector_sdk/pyproject.toml` under
`[tool.pytest.ini_options].markers`), mirroring
`tests/snowflake/test_connector_integration.py` exactly. A plain `pytest`
run never executes this file's assertions against a real instance: the test
is guarded by `@pytest.mark.skipif` on `CUSTOMER_POSTGRES_HOST` being unset,
so it *skips* cleanly (not an error, not a failure) when no real instance is
configured. To actually exercise it:

    CUSTOMER_POSTGRES_HOST=... CUSTOMER_POSTGRES_DATABASE=... \\
        CUSTOMER_POSTGRES_USER=... CUSTOMER_POSTGRES_PASSWORD=... \\
        pytest -m postgres_integration

This exact test was run for real during this connector's construction
against a real local Postgres 16 instance with a sample `sales` schema
(`customers`/`orders` tables, a column comment, a nullable column, and real
bind-parameterized `%(name)s`-style query execution) -- see `connector.py`'s
module docstring for what that real run found.
"""

from __future__ import annotations

import os

import pytest
from navigraph_connectors.postgres.connector import PostgresConnector
from navigraph_connectors.postgres.settings import PostgresSettings

pytestmark = pytest.mark.postgres_integration


@pytest.mark.skipif(
    not os.environ.get("CUSTOMER_POSTGRES_HOST"),
    reason="requires real CUSTOMER_POSTGRES_* env vars",
)
def test_connector_reaches_a_real_postgres_instance_and_introspects_schema() -> None:
    connector = PostgresConnector(PostgresSettings())

    result = connector.test_connection()
    assert result.success is True

    schemas = connector.introspect_schema()
    assert len(schemas) > 0


@pytest.mark.skipif(
    not os.environ.get("CUSTOMER_POSTGRES_HOST"),
    reason="requires real CUSTOMER_POSTGRES_* env vars",
)
def test_connector_executes_a_real_bind_parameterized_query() -> None:
    """Proves the real, live cross-source paramstyle compatibility finding
    documented in `connector.py`'s module docstring: SQL Generation's
    `%(name)s`-style bind parameters work against this connector with zero
    dialect translation."""

    connector = PostgresConnector(PostgresSettings())

    schemas = connector.introspect_schema()
    schema = schemas[0]
    table = schema.tables[0]

    result = connector.execute_query(
        f'SELECT * FROM "{schema.name}"."{table.name}" LIMIT %(row_limit)s',
        {"row_limit": 1},
    )
    assert result.row_count <= 1
