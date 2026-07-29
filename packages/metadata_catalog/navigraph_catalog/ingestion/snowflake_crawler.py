"""Crawl a data source's schema and store it in the catalog.

Despite the filename, this module contains no Snowflake-specific logic --
it is written entirely against the `navigraph_connectors.base.Connector`
ABC, proving the catalog/connector decoupling actually holds. The
"snowflake" in the filename just reflects that this is the crawler used for
the Snowflake data source today; `crawl_and_store` takes any `Connector`
implementation, present or future.
"""

from __future__ import annotations

import uuid

from navigraph_connectors.base import Connector
from sqlalchemy.orm import Session

from navigraph_catalog.api import upsert_schema_tree


def crawl_and_store(
    session: Session,
    *,
    data_source_id: uuid.UUID,
    connector: Connector,
) -> int:
    """Introspect `connector`'s schema and upsert it into the catalog.

    Returns the number of tables upserted across every schema returned by
    `connector.introspect_schema()`.
    """

    schemas = connector.introspect_schema()
    upsert_schema_tree(session, data_source_id=data_source_id, schemas=schemas)
    return sum(len(schema.tables) for schema in schemas)
