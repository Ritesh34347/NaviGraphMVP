"""Postgres connector plugin.

Side effect: importing this module registers `PostgresConnector` under the
`"postgres"` source_type in `navigraph_connectors.registry`, so that
`navigraph_connectors.registry.get_connector_class("postgres")` resolves
once anyone (a startup script, a test, another package) has imported
`navigraph_connectors.postgres`. Nothing about the interface in
`navigraph_connectors.base` requires this -- it's purely how this specific
connector implementation makes itself discoverable at runtime -- exactly
mirroring `navigraph_connectors.snowflake`'s identical pattern.

This is the second real `Connector` implementation in this SDK (LIMITATIONS.md
item 1) -- see `connector.py`'s module docstring for what pressure-testing
the source-agnostic `Connector` interface against a genuinely different
driver/dialect actually found.
"""

from __future__ import annotations

from navigraph_connectors.postgres.connector import PostgresConnector
from navigraph_connectors.registry import register_connector

register_connector("postgres", PostgresConnector)

__all__ = ["PostgresConnector"]
