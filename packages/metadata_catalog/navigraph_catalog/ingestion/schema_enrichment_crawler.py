"""Crawl a business glossary (e.g. Snowflake's `SCHEMA_ENRICHMENT` staging
table) and store it as `ColumnGlossary` rows attached to already-crawled
`CatalogColumn`s.

Written against the same `navigraph_connectors.base.Connector` ABC as
`snowflake_crawler.py` -- `crawl_and_store_glossary` takes any `Connector`
implementation and a plain SQL table reference, not anything
Snowflake-specific. The matching helper (`_find_catalog_column`) is a
separate function specifically so a future glossary source can reuse it
without depending on this module's SQL-shaped ingestion loop.
"""

from __future__ import annotations

import logging
import uuid

from navigraph_connectors.base import Connector
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from navigraph_catalog.api import upsert_glossary
from navigraph_catalog.models import CatalogColumn, CatalogSchema, CatalogTable

logger = logging.getLogger(__name__)


def _find_catalog_column(
    session: Session,
    *,
    data_source_id: uuid.UUID,
    table_name: str,
    column_name: str,
) -> CatalogColumn | None:
    """Look up a `CatalogColumn` for `data_source_id` by case-insensitive
    `(table_name, column_name)` match.

    Real crawled catalog names may differ in case from a glossary source's
    names (e.g. glossary `staging_transactions` vs. crawled
    `STAGING_TRANSACTIONS`) -- compare `func.lower(...)` on both sides in SQL
    rather than fetching every row and filtering in Python, since this query
    runs once per glossary row.
    """

    return session.execute(
        select(CatalogColumn)
        .join(CatalogTable, CatalogColumn.table_id == CatalogTable.id)
        .join(CatalogSchema, CatalogTable.schema_id == CatalogSchema.id)
        .where(
            CatalogSchema.data_source_id == data_source_id,
            func.lower(CatalogTable.name) == table_name.lower(),
            func.lower(CatalogColumn.name) == column_name.lower(),
        )
    ).scalar_one_or_none()


def crawl_and_store_glossary(
    session: Session,
    *,
    data_source_id: uuid.UUID,
    connector: Connector,
    glossary_table: str = "STAGING.SCHEMA_ENRICHMENT",
) -> int:
    """Read `glossary_table` via `connector` and upsert it as `ColumnGlossary` rows.

    For each glossary row, `synonyms` is split on "," and each piece is
    stripped of whitespace (a `None`/empty value becomes an empty list). The
    matching `CatalogColumn` for `data_source_id` is looked up by
    case-insensitive `(table_name, column_name)` -- a glossary row whose
    table/column can't be matched (e.g. the catalog hasn't crawled that exact
    table/schema yet) is logged as a warning and skipped, not raised, so one
    bad row never aborts the whole crawl.

    Returns the count of glossary rows successfully upserted.
    """

    result = connector.execute_query(
        f"SELECT table_name, column_name, business_name, synonyms, description "
        f"FROM {glossary_table}"
    )

    upserted_count = 0

    for raw_row in result.rows:
        # Snowflake's cursor.description (and thus QueryResult.rows) reports
        # column names in whatever case Snowflake actually stores them --
        # uppercase by default for unquoted identifiers (e.g. "TABLE_NAME"),
        # regardless of the case used when writing the SQL above. Normalize
        # to lowercase keys once per row rather than assuming the query's
        # literal casing survives into the result set.
        row = {key.lower(): value for key, value in raw_row.items()}

        table_name = row["table_name"]
        column_name = row["column_name"]

        raw_synonyms = row.get("synonyms")
        synonyms = (
            [synonym.strip() for synonym in raw_synonyms.split(",") if synonym.strip()]
            if raw_synonyms
            else []
        )

        catalog_column = _find_catalog_column(
            session,
            data_source_id=data_source_id,
            table_name=table_name,
            column_name=column_name,
        )

        if catalog_column is None:
            logger.warning(
                "No catalog column found for glossary row table=%r column=%r "
                "(data_source_id=%s) -- skipping",
                table_name,
                column_name,
                data_source_id,
            )
            continue

        upsert_glossary(
            session,
            column_id=catalog_column.id,
            business_name=row["business_name"],
            synonyms=synonyms,
            description=row.get("description"),
            source="schema_enrichment",
        )
        upserted_count += 1

    return upserted_count
