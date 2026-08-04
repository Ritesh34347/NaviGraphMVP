"""Real tests for `navigraph_kg.ontology` -- no mocks needed.

`SCHEMA_CONSTRAINTS` and `RELATIONSHIP_CONCEPTS` are plain Python data, so
these tests assert directly on their real content rather than mocking
anything out.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from navigraph_kg.ontology import (
    RELATIONSHIP_CONCEPTS,
    SCHEMA_CONSTRAINTS,
    apply_constraints,
)


class TestSchemaConstraints:
    def test_is_non_empty(self) -> None:
        assert len(SCHEMA_CONSTRAINTS) > 0

    def test_every_entry_is_a_constraint_statement(self) -> None:
        for statement in SCHEMA_CONSTRAINTS:
            assert isinstance(statement, str)
            assert "CONSTRAINT" in statement

    def test_every_entry_is_idempotent_via_if_not_exists(self) -> None:
        for statement in SCHEMA_CONSTRAINTS:
            assert "IF NOT EXISTS" in statement

    def test_covers_every_node_label_with_a_natural_key(self) -> None:
        expected_labels = [
            "Asset",
            "Market",
            "Exchange",
            "Sector",
            "Industry",
            "Channel",
            "CustomerType",
            "RiskLevel",
            "InvestmentCapacityBand",
            "BusinessConcept",
            "Table",
            "Column",
            "RelationshipConcept",
        ]
        for label in expected_labels:
            assert any(f":{label})" in statement for statement in SCHEMA_CONSTRAINTS), (
                f"no constraint found for label {label!r}"
            )


class TestApplyConstraints:
    def test_runs_every_constraint_statement_against_the_client(self) -> None:
        client = MagicMock()

        apply_constraints(client)

        assert client.run.call_count == len(SCHEMA_CONSTRAINTS)
        run_statements = [call.args[0] for call in client.run.call_args_list]
        assert run_statements == SCHEMA_CONSTRAINTS


class TestRelationshipConcepts:
    def test_has_exactly_the_six_seed_entries(self) -> None:
        assert len(RELATIONSHIP_CONCEPTS) == 6

    def test_every_entry_has_the_expected_keys(self) -> None:
        expected_keys = {
            "name",
            "subject_label",
            "predicate",
            "object_label",
            "realizing_table",
            "subject_key_column",
            "object_key_column",
        }
        for concept in RELATIONSHIP_CONCEPTS:
            assert set(concept.keys()) == expected_keys

    def test_contains_customer_holds_asset(self) -> None:
        names = {c["name"] for c in RELATIONSHIP_CONCEPTS}
        assert "Customer holds Asset" in names

    def test_contains_customer_uses_channel(self) -> None:
        names = {c["name"] for c in RELATIONSHIP_CONCEPTS}
        assert "Customer uses Channel" in names

    def test_contains_customer_has_risklevel(self) -> None:
        names = {c["name"] for c in RELATIONSHIP_CONCEPTS}
        assert "Customer has RiskLevel" in names

    def test_contains_transaction_happens_in_market(self) -> None:
        names = {c["name"] for c in RELATIONSHIP_CONCEPTS}
        assert "Transaction happens in Market" in names

    def test_transaction_happens_in_market_uses_marketid_as_the_shared_join_key(self) -> None:
        concept = next(
            c for c in RELATIONSHIP_CONCEPTS if c["name"] == "Transaction happens in Market"
        )
        assert concept["realizing_table"] == "TRANSACTIONS"
        assert concept["subject_key_column"] == "MARKETID"
        assert concept["object_key_column"] == "MARKETID"

    def test_contains_asset_traded_in_market(self) -> None:
        names = {c["name"] for c in RELATIONSHIP_CONCEPTS}
        assert "Asset traded in Market" in names

    def test_asset_traded_in_market_uses_marketid_as_the_shared_join_key(self) -> None:
        concept = next(
            c for c in RELATIONSHIP_CONCEPTS if c["name"] == "Asset traded in Market"
        )
        assert concept["realizing_table"] == "ASSET_INFORMATION"
        assert concept["subject_key_column"] == "MARKETID"
        assert concept["object_key_column"] == "MARKETID"

    def test_contains_transaction_involves_asset(self) -> None:
        names = {c["name"] for c in RELATIONSHIP_CONCEPTS}
        assert "Transaction involves Asset" in names

    def test_transaction_involves_asset_uses_isin_as_the_shared_join_key(self) -> None:
        concept = next(
            c for c in RELATIONSHIP_CONCEPTS if c["name"] == "Transaction involves Asset"
        )
        assert concept["realizing_table"] == "TRANSACTIONS"
        assert concept["subject_key_column"] == "ISIN"
        assert concept["object_key_column"] == "ISIN"
