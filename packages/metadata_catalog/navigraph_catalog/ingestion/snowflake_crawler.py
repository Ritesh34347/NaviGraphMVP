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

from navigraph_catalog.api import mark_data_source_crawled, upsert_schema_tree
from navigraph_catalog.drift import CrawlResult


def crawl_and_store(
    session: Session,
    *,
    data_source_id: uuid.UUID,
    connector: Connector,
) -> CrawlResult:
    """Introspect `connector`'s schema, upsert it into the catalog, and
    stamp `data_source_id.last_crawled_at` with the real current time.

    Returns the number of tables upserted across every schema returned by
    `connector.introspect_schema()`, plus a real, per-table `SchemaDriftEvent`
    (see `navigraph_catalog.drift`'s module docstring) -- the "did anything
    actually change since last time" signal a re-crawl scheduler needs.
    """

    schemas = connector.introspect_schema()
    drift_events = upsert_schema_tree(session, data_source_id=data_source_id, schemas=schemas)
    mark_data_source_crawled(session, data_source_id=data_source_id)
    tables_synced = sum(len(schema.tables) for schema in schemas)
    return CrawlResult(tables_synced=tables_synced, drift_events=drift_events)
