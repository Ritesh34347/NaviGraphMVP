"""Unit tests for `navigraph_kg.ingestion.pipeline`, Neo4j- and Snowflake-free.

Mocks `navigraph_catalog.api`'s `list_tables`/`list_columns`/`list_glossary`
directly (patched where `pipeline.py` imports them), a `MagicMock`-based
fake `Neo4jClient` that just records every `run()` call, and a fake
`Connector` returning canned `QueryResult`s keyed by which reference-data
query string was run. Asserts each of the four stages issues MERGE-shaped
Cypher with the right params, and -- the specifically-called-out case --
that a null `sector`/`industry` correctly produces NO `IN_SECTOR`/
`IN_INDUSTRY` edge.

Stages 3 (the four simple lookups)/4 (relationship concepts) now compile
from a `SemanticModel` passed into `run_ingestion` instead of hardcoded
Python -- `_semantic_model()` below builds one carrying the exact same
real values `navigraph_kg.ontology.RELATIONSHIP_CONCEPTS`/four
`reference_data_queries.py` constants used to hardcode, so every count
assertion in this file stays unchanged: this is the "identical output,
now from config" proof for the unit tier (see `LIMITATIONS.md`/
`BUILD_LOG.md` for the live-Snowflake golden-set re-run this sandbox
cannot perform).
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
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
    MARKETS_QUERY,
)
from navigraph_semantic_model import (
    Entity,
    EntityBinding,
    ReferenceLookup,
    Relationship,
    RelationshipBinding,
    SemanticModel,
)

_TENANT_ID = "tenant-a"
_DATA_SOURCE_ID = uuid.uuid4()
_DATA_SOURCE_NAME = "fidelity_poc_snowflake_v2"


def _semantic_model() -> SemanticModel:
    """The exact real FIDELITY_POC values `navigraph_kg.ontology
    .RELATIONSHIP_CONCEPTS` and the four simple-lookup
    `reference_data_queries.py` constants used to hardcode, now expressed
    as a `SemanticModel` -- see this module's docstring."""

    return SemanticModel(
        tenant_id=_TENANT_ID,
        version=1,
        entities=[
            Entity(
                name="Customer",
                bindings=[
                    EntityBinding(
                        data_source=_DATA_SOURCE_NAME,
                        table="STAGING.CUSTOMER_INFORMATION",
                        key="CUSTOMERID",
                    )
                ],
            ),
        ],
        relationships=[
            Relationship(
                name="Customer holds Asset",
                subject="Customer",
                predicate="HOLDS",
                object="Asset",
                via=RelationshipBinding(
                    data_source=_DATA_SOURCE_NAME,
                    table="FAR_TRANS.CUSTOMER_ASSET_AGG",
                    subject_key="CUSTOMERID",
                    object_key="ISIN",
                ),
            ),
            Relationship(
                name="Customer uses Channel",
                subject="Customer",
                predicate="USES",
                object="Channel",
                via=RelationshipBinding(
                    data_source=_DATA_SOURCE_NAME,
                    table="FAR_TRANS.TRANSACTIONS",
                    subject_key="CUSTOMERID",
                    object_key="CHANNEL",
                ),
            ),
            Relationship(
                name="Customer has RiskLevel",
                subject="Customer",
                predicate="HAS",
                object="RiskLevel",
                via=RelationshipBinding(
                    data_source=_DATA_SOURCE_NAME,
                    table="FAR_TRANS.CUSTOMER_INFORMATION",
                    subject_key="CUSTOMERID",
                    object_key="RISKLEVEL",
                ),
            ),
            Relationship(
                # Real bug found live in Phase 9's real HTTP smoke test of
                # the Request Orchestrator -- see
                # `navigraph_semantic_model.Relationship`'s docstring and
                # `LIMITATIONS.md`'s item 15 for the full root cause.
                name="Transaction happens in Market",
                subject="Transaction",
                predicate="HAPPENS_IN",
                object="Market",
                via=RelationshipBinding(
                    data_source=_DATA_SOURCE_NAME,
                    table="FAR_TRANS.TRANSACTIONS",
                    subject_key="MARKETID",
                    object_key="MARKETID",
                ),
            ),
        ],
        reference_lookups=[
            ReferenceLookup(
                node_label="Channel",
                data_source=_DATA_SOURCE_NAME,
                table="FAR_TRANS.TRANSACTIONS",
                column="CHANNEL",
            ),
            ReferenceLookup(
                node_label="CustomerType",
                data_source=_DATA_SOURCE_NAME,
                table="FAR_TRANS.CUSTOMER_INFORMATION",
                column="CUSTOMERTYPE",
            ),
            ReferenceLookup(
                node_label="RiskLevel",
                data_source=_DATA_SOURCE_NAME,
                table="FAR_TRANS.CUSTOMER_INFORMATION",
                column="RISKLEVEL",
            ),
            ReferenceLookup(
                node_label="InvestmentCapacityBand",
                data_source=_DATA_SOURCE_NAME,
                table="FAR_TRANS.CUSTOMER_INFORMATION",
                column="INVESTMENTCAPACITY",
            ),
        ],
    )


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
            "SELECT DISTINCT CHANNEL FROM FAR_TRANS.TRANSACTIONS WHERE CHANNEL IS NOT NULL": (
                _simple_lookup_result("CHANNEL", ["Internet Banking", "Branch"])
            ),
            (
                "SELECT DISTINCT CUSTOMERTYPE FROM FAR_TRANS.CUSTOMER_INFORMATION "
                "WHERE CUSTOMERTYPE IS NOT NULL"
            ): _simple_lookup_result("CUSTOMERTYPE", ["Mass", "Premium"]),
            (
                "SELECT DISTINCT RISKLEVEL FROM FAR_TRANS.CUSTOMER_INFORMATION "
                "WHERE RISKLEVEL IS NOT NULL"
            ): _simple_lookup_result("RISKLEVEL", ["Aggressive", "Balanced"]),
            (
                "SELECT DISTINCT INVESTMENTCAPACITY FROM FAR_TRANS.CUSTOMER_INFORMATION "
                "WHERE INVESTMENTCAPACITY IS NOT NULL"
            ): _simple_lookup_result("INVESTMENTCAPACITY", ["CAP_80K_300K"]),
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
            _semantic_model(),
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
    def test_merges_all_four_seed_concepts_with_realizes_and_key_edges(self) -> None:
        client, _summary, *_ = _run_pipeline()

        concept_calls = [
            call
            for call in client.run.call_args_list
            if "MERGE (rc:RelationshipConcept" in call.args[0]
        ]
        assert len(concept_calls) == 4
        names = {call.kwargs["name"] for call in concept_calls}
        assert names == {
            "Customer holds Asset",
            "Customer uses Channel",
            "Customer has RiskLevel",
            "Transaction happens in Market",
        }
        for call in concept_calls:
            assert "REALIZES" in call.args[0]
            assert "SUBJECT_KEY" in call.args[0]
            assert "OBJECT_KEY" in call.args[0]

    def test_realizing_table_is_the_bare_table_name_schema_stripped(self) -> None:
        """`Relationship.via.table` is `"SCHEMA.TABLE"`; the `Table` node
        this stage MERGEs must match stage 1's `Table.name` property
        (`CatalogTable.name`, never schema-qualified) -- so only the bare
        table name is ever used here, not the full `via.table` string."""

        client, _summary, *_ = _run_pipeline()

        concept_calls = [
            call
            for call in client.run.call_args_list
            if "MERGE (rc:RelationshipConcept" in call.args[0]
            and call.kwargs["name"] == "Transaction happens in Market"
        ]
        assert len(concept_calls) == 1
        assert concept_calls[0].kwargs["realizing_table"] == "TRANSACTIONS"


class TestRunIngestionValidation:
    def test_mismatched_tenant_id_raises(self) -> None:
        neo4j_client = MagicMock()
        catalog_session = MagicMock()
        connector = _make_connector()

        with (
            patch("navigraph_kg.ingestion.pipeline.list_tables", return_value=[]),
            patch("navigraph_kg.ingestion.pipeline.list_columns", return_value=[]),
            patch("navigraph_kg.ingestion.pipeline.list_glossary", return_value=[]),
            pytest.raises(ValueError, match="does not match"),
        ):
            run_ingestion(
                catalog_session,
                neo4j_client,
                connector,
                _semantic_model(),  # tenant_id="tenant-a"
                data_source_id=_DATA_SOURCE_ID,
                tenant_id="a-different-tenant",
            )

    def test_reference_lookup_with_an_unconstrained_node_label_raises(self) -> None:
        neo4j_client = MagicMock()
        catalog_session = MagicMock()
        connector = _make_connector()

        bad_model = _semantic_model().model_copy(
            update={
                "reference_lookups": [
                    ReferenceLookup(
                        node_label="NotARealTier1Label",
                        data_source=_DATA_SOURCE_NAME,
                        table="FAR_TRANS.TRANSACTIONS",
                        column="CHANNEL",
                    )
                ]
            }
        )

        with (
            patch("navigraph_kg.ingestion.pipeline.list_tables", return_value=[]),
            patch("navigraph_kg.ingestion.pipeline.list_columns", return_value=[]),
            patch("navigraph_kg.ingestion.pipeline.list_glossary", return_value=[]),
            pytest.raises(ValueError, match="not one of the schema-constrained"),
        ):
            run_ingestion(
                catalog_session,
                neo4j_client,
                connector,
                bad_model,
                data_source_id=_DATA_SOURCE_ID,
                tenant_id=_TENANT_ID,
            )


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
        assert summary.relationship_concepts_synced == 4
