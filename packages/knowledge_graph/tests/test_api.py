"""Unit tests for `navigraph_kg.api`, Neo4j-free.

Mocks `Neo4jClient.run` to return canned records and asserts each read
function's Cypher shape (a `tenant_id` filter is present in every query) and
result shape, without ever touching a real Neo4j instance.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from navigraph_kg.api import (
    entity_matches_reference_node,
    get_asset,
    get_column_for_concept,
    get_relationship_concept,
    list_assets_by_sector,
    list_business_concepts,
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
                "table_name": "STAGING_TRANSACTIONS",
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

    def test_query_also_optionally_resolves_the_columns_table_name(self) -> None:
        """`OntologyAgent._resolve_relationships` relies on `table_name`
        being present to recognize a relationship's `realizing_table` is
        already implied by a resolved business concept -- see that
        method's docstring for the real e-commerce gap this closes."""

        client = MagicMock()
        client.run.return_value = []

        resolve_business_term(client, tenant_id="tenant-a", term="revenue")

        cypher = client.run.call_args.args[0]
        assert "OPTIONAL MATCH" in cypher
        assert "table_name" in cypher

    def test_returns_empty_list_when_no_match(self) -> None:
        client = MagicMock()
        client.run.return_value = []

        result = resolve_business_term(client, tenant_id="tenant-a", term="nonexistent")

        assert result == []


class TestListBusinessConcepts:
    def test_queries_every_concept_for_the_tenant_with_no_term_filter(self) -> None:
        client = MagicMock()
        client.run.return_value = [
            {
                "business_concept": "Units Traded",
                "synonyms": ["quantity", "shares traded", "volume", "trade quantity"],
                "catalog_column_id": "col-1",
                "column_name": "UNITS",
                "table_name": "STAGING_TRANSACTIONS",
                "preferred": True,
                "source": "schema_enrichment",
            }
        ]

        result = list_business_concepts(client, tenant_id="tenant-a")

        client.run.assert_called_once()
        cypher = client.run.call_args.args[0]
        assert "tenant_id" in cypher
        assert "WHERE" not in cypher
        assert "synonyms" in cypher
        kwargs = client.run.call_args.kwargs
        assert kwargs == {"tenant_id": "tenant-a"}
        assert "term" not in kwargs
        assert result == client.run.return_value

    def test_returns_empty_list_when_tenant_has_no_glossary(self) -> None:
        client = MagicMock()
        client.run.return_value = []

        result = list_business_concepts(client, tenant_id="tenant-a")

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


class TestEntityMatchesReferenceNode:
    """Real bug this closes: a relationship concept's category label (e.g.
    "Market") only ever matched an entity that literally contained the
    word "market" -- naming a specific real market ("Athens Exchange")
    never matched. This checks the free-text entity against real
    reference-node values instead."""

    def test_queries_by_default_name_property_and_filters_by_tenant(self) -> None:
        client = MagicMock()
        client.run.return_value = [{"n": {"name": "Athens Exchange S.A. Cash Market"}}]

        result = entity_matches_reference_node(
            client, tenant_id="tenant-a", label="Market", entity="Athens Exchange"
        )

        assert result is True
        cypher = client.run.call_args.args[0]
        assert "tenant_id" in cypher
        assert ":Market" in cypher
        assert "n.name" in cypher
        kwargs = client.run.call_args.kwargs
        assert kwargs["tenant_id"] == "tenant-a"
        assert kwargs["entity"] == "Athens Exchange"

    def test_returns_false_when_no_real_node_matches(self) -> None:
        client = MagicMock()
        client.run.return_value = []

        result = entity_matches_reference_node(
            client, tenant_id="tenant-a", label="Market", entity="not a real market"
        )

        assert result is False

    def test_returns_false_for_blank_entity_without_querying(self) -> None:
        client = MagicMock()

        result = entity_matches_reference_node(
            client, tenant_id="tenant-a", label="Market", entity="   "
        )

        assert result is False
        client.run.assert_not_called()

    def test_asset_label_queries_asset_name_short_name_and_isin(self) -> None:
        client = MagicMock()
        client.run.return_value = [{"n": {"asset_name": "Acme Corp"}}]

        result = entity_matches_reference_node(
            client, tenant_id="tenant-a", label="Asset", entity="Acme"
        )

        assert result is True
        cypher = client.run.call_args.args[0]
        assert "n.asset_name" in cypher
        assert "n.asset_short_name" in cypher
        assert "n.isin" in cypher
