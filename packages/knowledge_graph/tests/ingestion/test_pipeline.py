"""Unit tests for `navigraph_kg.ingestion.pipeline`, Neo4j- and Snowflake-free.

Mocks `navigraph_catalog.api`'s `list_tables`/`list_columns`/`list_glossary`
directly (patched where `pipeline.py` imports them), a `MagicMock`-based
fake `Neo4jClient` that just records every `run()` call, and a fake
`Connector` returning canned `QueryResult`s keyed by which reference-data
query string was run. Asserts each of the four stages issues MERGE-shaped
Cypher with the right params, and -- the specifically-called-out case --
that a null `sector`/`industry` correctly produces NO `IN_SECTOR`/
`IN_INDUSTRY` edge.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from navigraph_catalog.models import CatalogColumn, CatalogTable, ColumnGlossary
from navigraph_connectors.base import (
    ConnectionTestResult,
    Connector,
    ConnectorCapabilities,
    QueryResult,
)
from navigraph_kg.ingestion.pipeline import run_ingestion
from navigraph_kg.ingestion.reference_data_queries import (
    ASSET_INFORMATION_QUERY,
    DISTINCT_CHANNELS_QUERY,
    DISTINCT_CUSTOMER_TYPES_QUERY,
    DISTINCT_INVESTMENT_CAPACITY_QUERY,
    DISTINCT_RISK_LEVELS_QUERY,
    MARKETS_QUERY,
)

_TENANT_ID = "tenant-a"
_DATA_SOURCE_ID = uuid.uuid4()


class FakeConnector(Connector):
    """Fake `Connector` returning canned `QueryResult`s keyed by SQL text."""

    def __init__(self, query_results: dict[str, QueryResult]) -> None:
        self._query_results = query_results

    def test_connection(self) -> ConnectionTestResult:
        return ConnectionTestResult(success=True, message="ok")

    def introspect_schema(self) -> list:  # pragma: no cover - unused by the pipeline
        return []

    def execute_query(self, sql: str, params: dict | None = None) -> QueryResult:
        return self._query_results[sql]

    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            supports_row_level_security=False,
            supports_column_masking=False,
            supports_query_pushdown=False,
        )


def _markets_query_result() -> QueryResult:
    rows = [
        {
            "EXCHANGEID": "ATHEX",
            "MARKETID": "EBB",
            "NAME": "Electronic Bulletin Board",
            "DESCRIPTION": None,
            "COUNTRY": "GR",
            "TRADINGDAYS": "Mon-Fri",
            "TRADINGHOURS": "10:00-17:20",
            "MARKETCLASS": "Main",
        },
        {
            "EXCHANGEID": "ATHEX",
            "MARKETID": "XATH",
            "NAME": "Athens Exchange",
            "DESCRIPTION": None,
            "COUNTRY": "GR",
            "TRADINGDAYS": "Mon-Fri",
            "TRADINGHOURS": "10:00-17:20",
            "MARKETCLASS": "Main",
        },
        {
            "EXCHANGEID": "ATHEX",
            "MARKETID": "ENAX",
            "NAME": "Athens Alternative Market",
            "DESCRIPTION": None,
            "COUNTRY": "GR",
            "TRADINGDAYS": "Mon-Fri",
            "TRADINGHOURS": "10:00-17:20",
            "MARKETCLASS": "Alternative",
        },
    ]
    return QueryResult(columns=list(rows[0]), rows=rows, row_count=len(rows))


def _asset_information_query_result() -> QueryResult:
    rows = [
        {
            "ISIN": "GR0001",
            "ASSETNAME": "Acme Corp",
            "ASSETSHORTNAME": "ACME",
            "ASSETCATEGORY": "Equity",
            "ASSETSUBCATEGORY": "Common Stock",
            "MARKETID": "EBB",
            "SECTOR": "Technology",
            "INDUSTRY": "Software",
        },
        {
            # A real bond-like instrument: legitimately no sector/industry/market.
            "ISIN": "GR0002",
            "ASSETNAME": "Sovereign Bond 2030",
            "ASSETSHORTNAME": None,
            "ASSETCATEGORY": "Bond",
            "ASSETSUBCATEGORY": None,
            "MARKETID": None,
            "SECTOR": None,
            "INDUSTRY": None,
        },
    ]
    return QueryResult(columns=list(rows[0]), rows=rows, row_count=len(rows))


def _simple_lookup_result(column: str, values: list[str]) -> QueryResult:
    rows = [{column: value} for value in values]
    return QueryResult(columns=[column], rows=rows, row_count=len(rows))


def _make_connector() -> FakeConnector:
    return FakeConnector(
        {
            MARKETS_QUERY: _markets_query_result(),
            ASSET_INFORMATION_QUERY: _asset_information_query_result(),
            DISTINCT_CHANNELS_QUERY: _simple_lookup_result(
                "CHANNEL", ["Internet Banking", "Branch"]
            ),
            DISTINCT_CUSTOMER_TYPES_QUERY: _simple_lookup_result(
                "CUSTOMERTYPE", ["Mass", "Premium"]
            ),
            DISTINCT_RISK_LEVELS_QUERY: _simple_lookup_result(
                "RISKLEVEL", ["Aggressive", "Balanced"]
            ),
            DISTINCT_INVESTMENT_CAPACITY_QUERY: _simple_lookup_result(
                "INVESTMENTCAPACITY", ["CAP_80K_300K"]
            ),
        }
    )


def _run_pipeline() -> tuple[MagicMock, object]:
    neo4j_client = MagicMock()
    catalog_session = MagicMock()
    connector = _make_connector()

    table = CatalogTable(id=uuid.uuid4(), name="ASSET_INFORMATION")
    column = CatalogColumn(
        id=uuid.uuid4(),
        table_id=table.id,
        name="ISIN",
        data_type="VARCHAR",
        nullable=False,
        ordinal_position=1,
    )
    glossary_entry = ColumnGlossary(
        column_id=column.id,
        business_name="Trade Value",
        synonyms=["Amount", "Value"],
        description="The monetary value of a trade.",
        source="schema_enrichment",
    )

    with (
        patch("navigraph_kg.ingestion.pipeline.list_tables", return_value=[table]),
        patch("navigraph_kg.ingestion.pipeline.list_columns", return_value=[column]),
        patch("navigraph_kg.ingestion.pipeline.list_glossary", return_value=[glossary_entry]),
    ):
        summary = run_ingestion(
            catalog_session,
            neo4j_client,
            connector,
            data_source_id=_DATA_SOURCE_ID,
            tenant_id=_TENANT_ID,
        )

    return neo4j_client, summary, table, column, glossary_entry


def _cypher_calls(client: MagicMock) -> list[str]:
    return [call.args[0] for call in client.run.call_args_list]


class TestSyncSchemaStructure:
    def test_merges_table_and_column_with_column_of_edge(self) -> None:
        client, _summary, table, column, _glossary = _run_pipeline()

        table_calls = [
            call
            for call in client.run.call_args_list
            if "MERGE (t:Table {catalog_table_id" in call.args[0]
        ]
        assert len(table_calls) == 1
        assert table_calls[0].kwargs["catalog_table_id"] == str(table.id)
        assert table_calls[0].kwargs["tenant_id"] == _TENANT_ID
        assert table_calls[0].kwargs["name"] == "ASSET_INFORMATION"

        column_calls = [
            call for call in client.run.call_args_list if "MERGE (c:Column" in call.args[0]
        ]
        assert len(column_calls) == 1
        assert "COLUMN_OF" in column_calls[0].args[0]
        assert column_calls[0].kwargs["catalog_column_id"] == str(column.id)
        assert column_calls[0].kwargs["catalog_table_id_for_table"] == str(table.id)


class TestSyncBusinessGlossary:
    def test_merges_business_concept_and_maps_to_edge(self) -> None:
        client, _summary, _table, _column, glossary_entry = _run_pipeline()

        concept_calls = [
            call
            for call in client.run.call_args_list
            if "MERGE (bc:BusinessConcept" in call.args[0]
        ]
        assert len(concept_calls) == 1
        call = concept_calls[0]
        assert "MAPS_TO" in call.args[0]
        assert call.kwargs["business_name"] == "Trade Value"
        assert call.kwargs["synonyms"] == ["Amount", "Value"]
        assert call.kwargs["catalog_column_id"] == str(glossary_entry.column_id)
        assert call.kwargs["source"] == "schema_enrichment"


class TestSyncReferenceData:
    def test_merges_exchange_and_three_markets_with_part_of_exchange_edge(self) -> None:
        client, _summary, *_ = _run_pipeline()

        market_calls = [
            call for call in client.run.call_args_list if "MERGE (m:Market" in call.args[0]
        ]
        assert len(market_calls) == 3
        assert {call.kwargs["market_id"] for call in market_calls} == {"EBB", "XATH", "ENAX"}
        for call in market_calls:
            assert call.kwargs["exchange_id"] == "ATHEX"
            assert "PART_OF_EXCHANGE" in call.args[0]

    def test_asset_with_sector_and_industry_gets_both_edges(self) -> None:
        client, _summary, *_ = _run_pipeline()

        sector_calls = [
            call for call in client.run.call_args_list if "MERGE (s:Sector" in call.args[0]
        ]
        assert len(sector_calls) == 1
        assert sector_calls[0].kwargs["sector"] == "Technology"
        assert sector_calls[0].kwargs["isin"] == "GR0001"

        industry_calls = [
            call for call in client.run.call_args_list if "MERGE (i:Industry" in call.args[0]
        ]
        assert len(industry_calls) == 1
        assert industry_calls[0].kwargs["industry"] == "Software"
        assert industry_calls[0].kwargs["isin"] == "GR0001"

    def test_asset_with_null_sector_and_industry_gets_no_edges(self) -> None:
        client, _summary, *_ = _run_pipeline()

        sector_calls = [
            call for call in client.run.call_args_list if "MERGE (s:Sector" in call.args[0]
        ]
        industry_calls = [
            call for call in client.run.call_args_list if "MERGE (i:Industry" in call.args[0]
        ]
        # Only the GR0001 asset (sector="Technology", industry="Software")
        # should ever produce a Sector/Industry merge -- GR0002 has both null
        # and must not appear in either call list.
        assert all(call.kwargs["isin"] != "GR0002" for call in sector_calls)
        assert all(call.kwargs["isin"] != "GR0002" for call in industry_calls)

    def test_asset_with_null_market_id_gets_no_listed_on_edge(self) -> None:
        client, _summary, *_ = _run_pipeline()

        listed_on_calls = [
            call for call in client.run.call_args_list if "LISTED_ON" in call.args[0]
        ]
        assert len(listed_on_calls) == 1
        assert listed_on_calls[0].kwargs["isin"] == "GR0001"
        assert listed_on_calls[0].kwargs["market_id"] == "EBB"

    def test_merges_channel_customer_type_risk_level_and_investment_capacity_band(self) -> None:
        client, _summary, *_ = _run_pipeline()

        channel_calls = [
            call for call in client.run.call_args_list if "MERGE (n:Channel" in call.args[0]
        ]
        assert {call.kwargs["name"] for call in channel_calls} == {
            "Internet Banking",
            "Branch",
        }

        customer_type_calls = [
            call for call in client.run.call_args_list if "MERGE (n:CustomerType" in call.args[0]
        ]
        assert {call.kwargs["name"] for call in customer_type_calls} == {"Mass", "Premium"}

        risk_level_calls = [
            call for call in client.run.call_args_list if "MERGE (n:RiskLevel" in call.args[0]
        ]
        assert {call.kwargs["name"] for call in risk_level_calls} == {
            "Aggressive",
            "Balanced",
        }

        band_calls = [
            call
            for call in client.run.call_args_list
            if "MERGE (n:InvestmentCapacityBand" in call.args[0]
        ]
        assert {call.kwargs["name"] for call in band_calls} == {"CAP_80K_300K"}


class TestSyncRelationshipConcepts:
    def test_merges_all_five_seed_concepts_with_realizes_and_key_edges(self) -> None:
        client, _summary, *_ = _run_pipeline()

        concept_calls = [
            call
            for call in client.run.call_args_list
            if "MERGE (rc:RelationshipConcept" in call.args[0]
        ]
        assert len(concept_calls) == 5
        names = {call.kwargs["name"] for call in concept_calls}
        assert names == {
            "Customer holds Asset",
            "Customer uses Channel",
            "Customer has RiskLevel",
            "Transaction happens in Market",
            "Asset traded in Market",
        }
        for call in concept_calls:
            assert "REALIZES" in call.args[0]
            assert "SUBJECT_KEY" in call.args[0]
            assert "OBJECT_KEY" in call.args[0]


class TestRunIngestionSummary:
    def test_returns_correct_per_stage_counts(self) -> None:
        _client, summary, *_ = _run_pipeline()

        assert summary.tables_synced == 1
        assert summary.columns_synced == 1
        assert summary.business_concepts_synced == 1
        assert summary.concept_mappings_synced == 1
        assert summary.assets_synced == 2
        assert summary.markets_synced == 3
        assert summary.exchanges_synced == 1
        assert summary.sectors_synced == 1
        assert summary.industries_synced == 1
        assert summary.channels_synced == 2
        assert summary.customer_types_synced == 2
        assert summary.risk_levels_synced == 2
        assert summary.investment_capacity_bands_synced == 1
        assert summary.relationship_concepts_synced == 5
