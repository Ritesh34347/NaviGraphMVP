"""Real integration test for `SnowflakeConnector` against an actual Snowflake account.

Marked `snowflake_integration` (registered in
`packages/connector_sdk/pyproject.toml` under
`[tool.pytest.ini_options].markers`). A plain `pytest` run never executes
this file's assertions against a real account: the test is guarded by
`@pytest.mark.skipif` on `SNOWFLAKE_ACCOUNT` being unset, so it *skips*
cleanly (not an error, not a failure) when no credentials are present. To
actually exercise it against a real account:

    SNOWFLAKE_ACCOUNT=... SNOWFLAKE_USER=... SNOWFLAKE_PASSWORD=... \\
        pytest -m snowflake_integration
"""

from __future__ import annotations

import os

import pytest
from navigraph_connectors.snowflake.connector import SnowflakeConnector
from navigraph_connectors.snowflake.settings import SnowflakeSettings

pytestmark = pytest.mark.snowflake_integration


@pytest.mark.skipif(
    not os.environ.get("SNOWFLAKE_ACCOUNT"),
    reason="requires real SNOWFLAKE_* env vars",
)
def test_connector_reaches_a_real_snowflake_account_and_introspects_schema() -> None:
    connector = SnowflakeConnector(SnowflakeSettings())

    result = connector.test_connection()
    assert result.success is True

    schemas = connector.introspect_schema()
    assert len(schemas) > 0
