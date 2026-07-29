"""Real unit tests for the Ontology agent.

Mocks `Neo4jClient` entirely (a plain `MagicMock`, patched at the
`navigraph_kg.api` call sites via `unittest.mock.patch`) -- no real Neo4j
instance is ever touched. `asyncio_mode = "auto"` is set at the workspace
root `packages/pyproject.toml`, so `async def test_...` functions run
without an explicit `@pytest.mark.asyncio` decorator.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from navigraph_shared.contracts import RequestContext

from navigraph_agents.understanding.ontology.agent import OntologyAgent
from navigraph_agents.understanding.ontology.contracts import (
    OntologyInput,
    OntologyPayload,
)


def _make_input(entities: list[str], intent: str = "metric_lookup") -> OntologyInput:
    return OntologyInput(
        request_context=RequestContext(
            tenant_id="tenant-acme",
            user_id="user-1",
            trace_id="trace-1",
            roles=["analyst"],
        ),
        payload=OntologyPayload(entities=entities, intent=intent),  # type: ignore[arg-type]
    )


class TestConceptResolution:
    async def test_all_entities_resolve(self) -> None:
        client = MagicMock()
        agent = OntologyAgent(client=client)

        def fake_resolve(_client, *, tenant_id, term):
            return [
                {
                    "business_concept": f"Concept for {term}",
                    "catalog_column_id": f"col-{term}",
                    "column_name": term.upper(),
                    "preferred": True,
                    "source": "schema_enrichment",
                }
            ]

        with (
            patch(
                "navigraph_agents.understanding.ontology.agent.resolve_business_term",
                side_effect=fake_resolve,
            ),
            patch(
                "navigraph_agents.understanding.ontology.agent.get_relationship_concept",
                return_value=None,
            ),
        ):
            output = await agent.run(_make_input(["revenue", "quarter"]))

        assert len(output.result.concept_resolutions) == 2
        assert all(cr.resolved for cr in output.result.concept_resolutions)
        assert output.result.unresolved_terms == []
        assert output.errors == []
        assert output.confidence == 1.0
        assert len(output.lineage_events) == 1
        assert output.metadata.latency_ms >= 0
        # No LLM involvement -- metadata carries only latency.
        assert output.metadata.model_version is None
        assert output.metadata.prompt_version is None

    async def test_some_entities_resolve(self) -> None:
        client = MagicMock()
        agent = OntologyAgent(client=client)

        def fake_resolve(_client, *, tenant_id, term):
            if term == "revenue":
                return [
                    {
                        "business_concept": "Total Transaction Value",
                        "catalog_column_id": "col-1",
                        "column_name": "TOTALVALUE",
                        "preferred": True,
                        "source": "schema_enrichment",
                    }
                ]
            return []

        with (
            patch(
                "navigraph_agents.understanding.ontology.agent.resolve_business_term",
                side_effect=fake_resolve,
            ),
            patch(
                "navigraph_agents.understanding.ontology.agent.get_relationship_concept",
                return_value=None,
            ),
        ):
            output = await agent.run(_make_input(["revenue", "gibberish_term"]))

        resolutions = {cr.term: cr for cr in output.result.concept_resolutions}
        assert resolutions["revenue"].resolved is True
        assert resolutions["revenue"].catalog_column_id == "col-1"
        assert resolutions["gibberish_term"].resolved is False
        assert output.result.unresolved_terms == ["gibberish_term"]
        assert output.confidence == 0.5

    async def test_no_entities_resolve(self) -> None:
        client = MagicMock()
        agent = OntologyAgent(client=client)

        with (
            patch(
                "navigraph_agents.understanding.ontology.agent.resolve_business_term",
                return_value=[],
            ),
            patch(
                "navigraph_agents.understanding.ontology.agent.get_relationship_concept",
                return_value=None,
            ),
        ):
            output = await agent.run(_make_input(["nonsense_a", "nonsense_b"]))

        assert all(not cr.resolved for cr in output.result.concept_resolutions)
        assert set(output.result.unresolved_terms) == {"nonsense_a", "nonsense_b"}
        assert output.confidence == 0.5
        assert output.errors == []

    async def test_preferred_record_wins_over_non_preferred(self) -> None:
        client = MagicMock()
        agent = OntologyAgent(client=client)

        records = [
            {
                "business_concept": "Legacy Concept",
                "catalog_column_id": "col-legacy",
                "column_name": "OLDCOL",
                "preferred": False,
                "source": "manual",
            },
            {
                "business_concept": "Total Transaction Value",
                "catalog_column_id": "col-preferred",
                "column_name": "TOTALVALUE",
                "preferred": True,
                "source": "schema_enrichment",
            },
        ]

        with (
            patch(
                "navigraph_agents.understanding.ontology.agent.resolve_business_term",
                return_value=records,
            ),
            patch(
                "navigraph_agents.understanding.ontology.agent.get_relationship_concept",
                return_value=None,
            ),
        ):
            output = await agent.run(_make_input(["revenue"]))

        resolution = output.result.concept_resolutions[0]
        assert resolution.catalog_column_id == "col-preferred"
        assert resolution.preferred is True

    async def test_falls_back_to_first_record_when_none_preferred(self) -> None:
        client = MagicMock()
        agent = OntologyAgent(client=client)

        records = [
            {
                "business_concept": "Concept A",
                "catalog_column_id": "col-a",
                "column_name": "COLA",
                "preferred": False,
                "source": "manual",
            },
            {
                "business_concept": "Concept B",
                "catalog_column_id": "col-b",
                "column_name": "COLB",
                "preferred": False,
                "source": "manual",
            },
        ]

        with (
            patch(
                "navigraph_agents.understanding.ontology.agent.resolve_business_term",
                return_value=records,
            ),
            patch(
                "navigraph_agents.understanding.ontology.agent.get_relationship_concept",
                return_value=None,
            ),
        ):
            output = await agent.run(_make_input(["ambiguous"]))

        resolution = output.result.concept_resolutions[0]
        assert resolution.catalog_column_id == "col-a"


class TestRelationshipResolution:
    async def test_relationship_fires_when_both_labels_present(self) -> None:
        client = MagicMock()
        agent = OntologyAgent(client=client)

        relationship_record = {
            "name": "Customer has RiskLevel",
            "realizing_table": "CUSTOMER_INFORMATION",
            "subject_key_column": "CUSTOMERID",
            "object_key_column": "RISKLEVEL",
        }

        with (
            patch(
                "navigraph_agents.understanding.ontology.agent.resolve_business_term",
                return_value=[],
            ),
            patch(
                "navigraph_agents.understanding.ontology.agent.get_relationship_concept",
                return_value=relationship_record,
            ) as mock_get_rel,
        ):
            output = await agent.run(_make_input(["Customer", "RiskLevel"]))

        assert len(output.result.relationship_resolutions) == 1
        relationship = output.result.relationship_resolutions[0]
        assert relationship.subject_label == "Customer"
        assert relationship.object_label == "RiskLevel"
        assert relationship.realizing_table == "CUSTOMER_INFORMATION"
        assert relationship.subject_key_column == "CUSTOMERID"
        assert relationship.object_key_column == "RISKLEVEL"
        # Only the one seed concept whose labels both matched should ever
        # reach get_relationship_concept.
        assert mock_get_rel.call_count == 1

    async def test_relationship_does_not_fire_with_only_one_label_present(self) -> None:
        client = MagicMock()
        agent = OntologyAgent(client=client)

        with (
            patch(
                "navigraph_agents.understanding.ontology.agent.resolve_business_term",
                return_value=[],
            ),
            patch(
                "navigraph_agents.understanding.ontology.agent.get_relationship_concept",
            ) as mock_get_rel,
        ):
            output = await agent.run(_make_input(["Customer", "revenue"]))

        assert output.result.relationship_resolutions == []
        mock_get_rel.assert_not_called()

    async def test_relationship_concept_lookup_returning_none_is_skipped(self) -> None:
        client = MagicMock()
        agent = OntologyAgent(client=client)

        with (
            patch(
                "navigraph_agents.understanding.ontology.agent.resolve_business_term",
                return_value=[],
            ),
            patch(
                "navigraph_agents.understanding.ontology.agent.get_relationship_concept",
                return_value=None,
            ),
        ):
            output = await agent.run(_make_input(["Customer", "RiskLevel"]))

        assert output.result.relationship_resolutions == []


class TestNeo4jFailurePath:
    async def test_query_failure_marks_all_entities_unresolved(self) -> None:
        client = MagicMock()
        agent = OntologyAgent(client=client)

        with patch(
            "navigraph_agents.understanding.ontology.agent.resolve_business_term",
            side_effect=RuntimeError("connection refused"),
        ):
            output = await agent.run(_make_input(["revenue", "quarter"]))

        assert len(output.errors) == 1
        assert output.errors[0].code == "knowledge_graph_query_failed"
        assert output.errors[0].recoverable is True
        assert all(not cr.resolved for cr in output.result.concept_resolutions)
        assert set(output.result.unresolved_terms) == {"revenue", "quarter"}
        assert output.result.relationship_resolutions == []
        assert output.confidence == 0.0
        # Must not raise -- lineage/metadata are still produced on the
        # fallback path.
        assert len(output.lineage_events) == 1
        assert output.metadata.latency_ms >= 0
