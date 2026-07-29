"""Unit tests for `navigraph_kg.api`, Neo4j-free.

Mocks `Neo4jClient.run` to return canned records and asserts each read
function's Cypher shape (a `tenant_id` filter is present in every query) and
result shape, without ever touching a real Neo4j instance.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from navigraph_kg.api import (
    get_asset,
    get_column_for_concept,
    get_relationship_concept,
    list_assets_by_sector,
    list_markets_for_exchange,
    resolve_business_term,
)


class TestResolveBusinessTerm:
    def test_queries_by_name_or_synonym_and_filters_by_tenant(self) -> None:
        client = MagicMock()
        client.run.return_value = [
            {
                "business_concept": "Trade Value",
                "catalog_column_id": "col-1",
                "column_name": "AMOUNT",
                "preferred": True,
                "source": "schema_enrichment",
            }
        ]

        result = resolve_business_term(client, tenant_id="tenant-a", term="trade value")

        client.run.assert_called_once()
        cypher = client.run.call_args.args[0]
        assert "tenant_id" in cypher
        assert "toLower" in cypher
        assert "synonyms" in cypher
        kwargs = client.run.call_args.kwargs
        assert kwargs["tenant_id"] == "tenant-a"
        assert kwargs["term"] == "trade value"
        assert result == client.run.return_value

    def test_returns_empty_list_when_no_match(self) -> None:
        client = MagicMock()
        client.run.return_value = []

        result = resolve_business_term(client, tenant_id="tenant-a", term="nonexistent")

        assert result == []


class TestGetColumnForConcept:
    def test_returns_first_record_when_present(self) -> None:
        client = MagicMock()
        client.run.return_value = [
            {"catalog_column_id": "col-1", "column_name": "AMOUNT", "preferred": True}
        ]

        result = get_column_for_concept(client, tenant_id="tenant-a", concept_name="Trade Value")

        cypher = client.run.call_args.args[0]
        assert "tenant_id" in cypher
        assert result == {"catalog_column_id": "col-1", "column_name": "AMOUNT", "preferred": True}

    def test_returns_none_when_absent(self) -> None:
        client = MagicMock()
        client.run.return_value = []

        result = get_column_for_concept(client, tenant_id="tenant-a", concept_name="Nope")

        assert result is None


class TestGetRelationshipConcept:
    def test_queries_subject_predicate_object_and_filters_by_tenant(self) -> None:
        client = MagicMock()
        client.run.return_value = [
            {
                "name": "Customer holds Asset",
                "realizing_table": "CUSTOMER_ASSET_AGG",
                "subject_key_column": "CUSTOMERID",
                "object_key_column": "ISIN",
            }
        ]

        result = get_relationship_concept(
            client,
            tenant_id="tenant-a",
            subject_label="Customer",
            predicate="HOLDS",
            object_label="Asset",
        )

        cypher = client.run.call_args.args[0]
        assert "tenant_id" in cypher
        kwargs = client.run.call_args.kwargs
        assert kwargs["subject_label"] == "Customer"
        assert kwargs["predicate"] == "HOLDS"
        assert kwargs["object_label"] == "Asset"
        assert result == client.run.return_value[0]

    def test_returns_none_when_absent(self) -> None:
        client = MagicMock()
        client.run.return_value = []

        result = get_relationship_concept(
            client,
            tenant_id="tenant-a",
            subject_label="Customer",
            predicate="BOUGHT",
            object_label="Asset",
        )

        assert result is None


class TestListAssetsBySector:
    def test_filters_by_tenant_and_sector_name(self) -> None:
        client = MagicMock()
        client.run.return_value = [
            {"isin": "GR001", "asset_name": "Acme Corp", "asset_category": "Equity"}
        ]

        result = list_assets_by_sector(client, tenant_id="tenant-a", sector_name="Technology")

        cypher = client.run.call_args.args[0]
        assert "tenant_id" in cypher
        kwargs = client.run.call_args.kwargs
        assert kwargs["sector_name"] == "Technology"
        assert result == client.run.return_value


class TestGetAsset:
    def test_returns_first_record_when_present(self) -> None:
        client = MagicMock()
        client.run.return_value = [{"isin": "GR001", "asset_name": "Acme Corp"}]

        result = get_asset(client, tenant_id="tenant-a", isin="GR001")

        cypher = client.run.call_args.args[0]
        assert "tenant_id" in cypher
        assert result == {"isin": "GR001", "asset_name": "Acme Corp"}

    def test_returns_none_when_absent(self) -> None:
        client = MagicMock()
        client.run.return_value = []

        result = get_asset(client, tenant_id="tenant-a", isin="NOPE")

        assert result is None


class TestListMarketsForExchange:
    def test_filters_by_tenant_and_exchange_id(self) -> None:
        client = MagicMock()
        client.run.return_value = [
            {"market_id": "EBB", "name": "Electronic Bulletin Board", "country": "GR"},
            {"market_id": "XATH", "name": "Athens Exchange", "country": "GR"},
            {"market_id": "ENAX", "name": "Athens Alternative Market", "country": "GR"},
        ]

        result = list_markets_for_exchange(client, tenant_id="tenant-a", exchange_id="ATHEX")

        cypher = client.run.call_args.args[0]
        assert "tenant_id" in cypher
        kwargs = client.run.call_args.kwargs
        assert kwargs["exchange_id"] == "ATHEX"
        assert len(result) == 3
        assert {m["market_id"] for m in result} == {"EBB", "XATH", "ENAX"}
