"""Databricks connector plugin.

Side effect: importing this module registers `DatabricksConnector` under
the `"databricks"` source_type in `navigraph_connectors.registry`, so that
`navigraph_connectors.registry.get_connector_class("databricks")` resolves
once anyone (a startup script, a test, another package) has imported
`navigraph_connectors.databricks` -- same registration pattern as
`navigraph_connectors.snowflake`/`navigraph_connectors.postgres`.
"""

from __future__ import annotations

from navigraph_connectors.databricks.connector import DatabricksConnector
from navigraph_connectors.databricks.settings_factory import build_databricks_settings
from navigraph_connectors.registry import register_connector

register_connector("databricks", DatabricksConnector, build_databricks_settings)

__all__ = ["DatabricksConnector"]
