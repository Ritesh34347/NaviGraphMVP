"""Tests for `navigraph_catalog.ingestion.snowflake_crawler.crawl_and_store`.

Uses a small FAKE `Connector` subclass defined right here -- NOT a real
Snowflake connector and NOT a real Postgres connection -- to prove
`crawl_and_store` correctly translates a connector's `introspect_schema()`
output into the right `upsert_schema_tree` call and returns the correct
table count, with `upsert_schema_tree` itself mocked out.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock, patch

from navigraph_catalog.ingestion.snowflake_crawler import crawl_and_store
from navigraph_connectors.base import (
    ColumnDescriptor,
    ConnectionTestResult,
    Connector,
    ConnectorCapabilities,
    QueryResult,
    SchemaDescriptor,
    TableDescriptor,
)


class FakeConnector(Connector):
    """A minimal, fully-fake `Connector` implementation for testing.

    Deliberately unrelated to any real data source -- it exists only to
    prove `crawl_and_store` is written against the `Connector` ABC and not
    against any Snowflake-specific behavior.
    """

    def __init__(self, schemas: list[SchemaDescriptor]) -> None:
        self._schemas = schemas

    def test_connection(self) -> ConnectionTestResult:
        return ConnectionTestResult(success=True, message="ok")

    def introspect_schema(self) -> list[SchemaDescriptor]:
        return self._schemas

    def execute_query(self, sql: str, params: dict[str, Any] | None = None) -> QueryResult:
        return QueryResult(columns=[], rows=[], row_count=0)

    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            supports_row_level_security=False,
            supports_column_masking=False,
            supports_query_pushdown=False,
        )


def _make_schemas() -> list[SchemaDescriptor]:
    return [
        SchemaDescriptor(
            name="public",
            tables=[
                TableDescriptor(
                    name="orders",
                    row_count_estimate=10,
                    columns=[
                        ColumnDescriptor(
                            name="id", data_type="INTEGER", nullable=False, ordinal_position=1
                        ),
                        ColumnDescriptor(
                            name="total", data_type="NUMBER", nullable=True, ordinal_position=2
                        ),
                    ],
                ),
                TableDescriptor(
                    name="customers",
                    row_count_estimate=5,
                    columns=[
                        ColumnDescriptor(
                            name="id", data_type="INTEGER", nullable=False, ordinal_position=1
                        ),
                    ],
                ),
            ],
        ),
        SchemaDescriptor(
            name="analytics",
            tables=[
                TableDescriptor(
                    name="daily_revenue",
                    row_count_estimate=365,
                    columns=[
                        ColumnDescriptor(
                            name="day", data_type="DATE", nullable=False, ordinal_position=1
                        ),
                    ],
                ),
            ],
        ),
    ]


def test_crawl_and_store_passes_introspected_schemas_to_upsert() -> None:
    session = MagicMock()
    data_source_id = uuid.uuid4()
    schemas = _make_schemas()
    connector = FakeConnector(schemas)

    with patch("navigraph_catalog.ingestion.snowflake_crawler.upsert_schema_tree") as mock_upsert:
        result = crawl_and_store(session, data_source_id=data_source_id, connector=connector)

    mock_upsert.assert_called_once_with(session, data_source_id=data_source_id, schemas=schemas)
    # 2 tables in "public" + 1 table in "analytics" = 3.
    assert result == 3


def test_crawl_and_store_returns_zero_for_empty_schema() -> None:
    session = MagicMock()
    connector = FakeConnector([])

    with patch("navigraph_catalog.ingestion.snowflake_crawler.upsert_schema_tree"):
        result = crawl_and_store(session, data_source_id=uuid.uuid4(), connector=connector)

    assert result == 0
