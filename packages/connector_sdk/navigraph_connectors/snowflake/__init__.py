"""Snowflake connector plugin.

Side effect: importing this module registers `SnowflakeConnector` under the
`"snowflake"` source_type in `navigraph_connectors.registry`, so that
`navigraph_connectors.registry.get_connector_class("snowflake")` resolves
once anyone (a startup script, a test, another package) has imported
`navigraph_connectors.snowflake`. Nothing about the interface in
`navigraph_connectors.base` requires this -- it's purely how this specific
connector implementation makes itself discoverable at runtime.
"""

from __future__ import annotations

from navigraph_connectors.registry import register_connector
from navigraph_connectors.snowflake.connector import SnowflakeConnector

register_connector("snowflake", SnowflakeConnector)

__all__ = ["SnowflakeConnector"]
