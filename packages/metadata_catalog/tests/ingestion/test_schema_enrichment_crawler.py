"""Tests for `navigraph_catalog.ingestion.schema_enrichment_crawler.crawl_and_store_glossary`.

Uses a small FAKE `Connector` subclass defined right here (same approach as
`test_snowflake_crawler.py`) whose `execute_query` returns a canned
`QueryResult` of glossary rows -- NOT a real Snowflake connector and NOT a
real Postgres connection. `upsert_glossary` and the catalog-column lookup are
exercised against a mocked `Session`, consistent with `test_api.py`'s
DB-free approach for this unit tier.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from navigraph_catalog.ingestion.schema_enrichment_crawler import (
    crawl_and_store_glossary,
)
from navigraph_catalog.models import CatalogColumn
from navigraph_connectors.base import (
    ConnectionTestResult,
    Connector,
    ConnectorCapabilities,
    QueryResult,
    SchemaDescriptor,
)


class FakeConnector(Connector):
    """A minimal, fully-fake `Connector` implementation for testing.

    Deliberately unrelated to any real data source -- it exists only to
    prove `crawl_and_store_glossary` is written against the `Connector` ABC,
    returning whatever canned `QueryResult` a test configures.
    """

    def __init__(self, query_result: QueryResult) -> None:
        self._query_result = query_result
        self.last_sql: str | None = None

    def test_connection(self) -> ConnectionTestResult:
        return ConnectionTestResult(success=True, message="ok")

    def introspect_schema(self) -> list[SchemaDescriptor]:
        return []

    def execute_query(self, sql: str, params: dict[str, Any] | None = None) -> QueryResult:
        self.last_sql = sql
        return self._query_result

    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            supports_row_level_security=False,
            supports_column_masking=False,
            supports_query_pushdown=False,
        )


def _glossary_query_result() -> QueryResult:
    # Real Snowflake returns column names in whatever case Snowflake actually
    # stores them -- uppercase by default for unquoted identifiers -- which
    # is independent of the case used to write the SQL query itself. Using
    # uppercase keys here (not the lowercase the query text happens to use)
    # is deliberate: a fixture using lowercase keys masked a real bug where
    # the crawler assumed lowercase keys and got a KeyError against a real
    # account.
    return QueryResult(
        columns=["TABLE_NAME", "COLUMN_NAME", "BUSINESS_NAME", "SYNONYMS", "DESCRIPTION"],
        rows=[
            {
                "TABLE_NAME": "staging_transactions",
                "COLUMN_NAME": "totalvalue",
                "BUSINESS_NAME": "Total Transaction Value",
                "SYNONYMS": "trade value,order value,transaction amount,gross value",
                "DESCRIPTION": "The total monetary value of the transaction.",
            },
            {
                "TABLE_NAME": "staging_customer_information",
                "COLUMN_NAME": "customerid",
                "BUSINESS_NAME": "Customer ID",
                "SYNONYMS": None,
                "DESCRIPTION": None,
            },
            {
                "TABLE_NAME": "staging_does_not_exist",
                "COLUMN_NAME": "nope",
                "BUSINESS_NAME": "Unmatched Row",
                "SYNONYMS": "a,b",
                "DESCRIPTION": "This row has no matching catalog column.",
            },
        ],
        row_count=3,
    )


def test_crawl_and_store_glossary_matches_splits_synonyms_and_skips_unmatched(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = MagicMock()
    data_source_id = uuid.uuid4()
    connector = FakeConnector(_glossary_query_result())

    matched_column_1 = MagicMock(spec=CatalogColumn)
    matched_column_1.id = uuid.uuid4()
    matched_column_2 = MagicMock(spec=CatalogColumn)
    matched_column_2.id = uuid.uuid4()

    with (
        patch(
            "navigraph_catalog.ingestion.schema_enrichment_crawler._find_catalog_column",
            side_effect=[matched_column_1, matched_column_2, None],
        ) as mock_find_column,
        patch(
            "navigraph_catalog.ingestion.schema_enrichment_crawler.upsert_glossary"
        ) as mock_upsert_glossary,
        caplog.at_level(logging.WARNING),
    ):
        result = crawl_and_store_glossary(
            session,
            data_source_id=data_source_id,
            connector=connector,
        )

    assert connector.last_sql == (
        "SELECT table_name, column_name, business_name, synonyms, description "
        "FROM STAGING.SCHEMA_ENRICHMENT"
    )

    assert mock_find_column.call_count == 3
    mock_find_column.assert_any_call(
        session,
        data_source_id=data_source_id,
        table_name="staging_transactions",
        column_name="totalvalue",
    )
    mock_find_column.assert_any_call(
        session,
        data_source_id=data_source_id,
        table_name="staging_does_not_exist",
        column_name="nope",
    )

    # Two matched rows upserted; the unmatched third row skipped.
    assert mock_upsert_glossary.call_count == 2
    assert result == 2

    first_call_kwargs = mock_upsert_glossary.call_args_list[0].kwargs
    assert first_call_kwargs["column_id"] == matched_column_1.id
    assert first_call_kwargs["business_name"] == "Total Transaction Value"
    assert first_call_kwargs["synonyms"] == [
        "trade value",
        "order value",
        "transaction amount",
        "gross value",
    ]
    assert first_call_kwargs["description"] == "The total monetary value of the transaction."
    assert first_call_kwargs["source"] == "schema_enrichment"

    second_call_kwargs = mock_upsert_glossary.call_args_list[1].kwargs
    assert second_call_kwargs["column_id"] == matched_column_2.id
    assert second_call_kwargs["synonyms"] == []
    assert second_call_kwargs["description"] is None

    assert any(
        "staging_does_not_exist" in record.message and "nope" in record.message
        for record in caplog.records
    )


def test_crawl_and_store_glossary_returns_zero_for_no_rows() -> None:
    session = MagicMock()
    connector = FakeConnector(QueryResult(columns=[], rows=[], row_count=0))

    with patch(
        "navigraph_catalog.ingestion.schema_enrichment_crawler.upsert_glossary"
    ) as mock_upsert_glossary:
        result = crawl_and_store_glossary(
            session,
            data_source_id=uuid.uuid4(),
            connector=connector,
        )

    mock_upsert_glossary.assert_not_called()
    assert result == 0
