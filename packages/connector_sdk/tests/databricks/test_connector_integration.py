"""Real integration test for `DatabricksConnector` against an actual
Databricks (Unity Catalog) workspace.

Marked `databricks_integration` (registered in
`packages/connector_sdk/pyproject.toml` under
`[tool.pytest.ini_options].markers`). A plain `pytest` run never executes
this file's assertions against a real workspace: the test is guarded by
`@pytest.mark.skipif` on `DATABRICKS_SERVER_HOSTNAME` being unset, so it
*skips* cleanly (not an error, not a failure) when no credentials are
present -- same precedent as `snowflake`/`postgres`'s own integration
tests. No real Databricks workspace has been registered against this
connector yet (see LIMITATIONS.md); this test exists so the moment one
is, verifying it is a one-line `pytest -m databricks_integration` away.
Requires a Unity-Catalog-enabled workspace (see this connector's own
module docstring).

    DATABRICKS_SERVER_HOSTNAME=... DATABRICKS_HTTP_PATH=... \\
        DATABRICKS_ACCESS_TOKEN=... DATABRICKS_CATALOG=... \\
        pytest -m databricks_integration
"""

from __future__ import annotations

import os

import pytest
from navigraph_connectors.databricks.connector import DatabricksConnector
from navigraph_connectors.databricks.settings import DatabricksSettings

pytestmark = pytest.mark.databricks_integration


@pytest.mark.skipif(
    not os.environ.get("DATABRICKS_SERVER_HOSTNAME"),
    reason="requires real DATABRICKS_* env vars",
)
def test_connector_reaches_a_real_databricks_workspace_and_introspects_schema() -> None:
    connector = DatabricksConnector(DatabricksSettings())

    result = connector.test_connection()
    assert result.success is True

    schemas = connector.introspect_schema()
    assert len(schemas) > 0
