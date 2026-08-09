"""Real tests for `navigraph_kg.ontology` -- no mocks needed.

`SCHEMA_CONSTRAINTS` is plain Python data, so these tests assert directly
on its real content rather than mocking anything out. `RELATIONSHIP_CONCEPTS`
(the hand-curated seed list this file used to test directly) has been
retired -- real relationship data now lives in a per-tenant
`navigraph_semantic_model.SemanticModel`, compiled into the graph by
`navigraph_kg.ingestion.pipeline._sync_relationship_concepts` (see
`packages/knowledge_graph/tests/ingestion/test_pipeline.py` for coverage
of that compilation) and read back via `navigraph_kg.api
.list_relationship_concepts`.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from navigraph_kg.ontology import SCHEMA_CONSTRAINTS, apply_constraints


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
