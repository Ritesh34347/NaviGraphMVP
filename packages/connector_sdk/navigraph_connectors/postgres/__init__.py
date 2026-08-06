"""Postgres connector plugin.

Side effect: importing this module registers `PostgresConnector` under the
`"postgres"` source_type in `navigraph_connectors.registry`, so that
`navigraph_connectors.registry.get_connector_class("postgres")` resolves
once anyone (a startup script, a test, another package) has imported
`navigraph_connectors.postgres` -- same registration pattern as
`navigraph_connectors.snowflake`.
"""

from __future__ import annotations

from navigraph_connectors.postgres.connector import PostgresConnector
from navigraph_connectors.registry import register_connector

register_connector("postgres", PostgresConnector)

__all__ = ["PostgresConnector"]
