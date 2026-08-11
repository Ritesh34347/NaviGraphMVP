"""Real unit tests for the Ontology agent.

Mocks `Neo4jClient` entirely (a plain `MagicMock`, patched at the
`navigraph_kg.api` call sites via `unittest.mock.patch`) -- no real Neo4j
instance is ever touched. `asyncio_mode = "auto"` is set at the workspace
root `packages/pyproject.toml`, so `async def test_...` functions run
without an explicit `@pytest.mark.asyncio` decorator.
"""

from __future__ import annotations

from typing import ClassVar
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
                "navigraph_agents.understanding.ontology.agent.list_relationship_concepts",
                return_value=[],
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
                "navigraph_agents.understanding.ontology.agent.list_relationship_concepts",
                return_value=[],
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
                "navigraph_agents.understanding.ontology.agent.list_relationship_concepts",
                return_value=[],
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
                "navigraph_agents.understanding.ontology.agent.list_relationship_concepts",
                return_value=[],
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
                "navigraph_agents.understanding.ontology.agent.list_relationship_concepts",
                return_value=[],
            ),
        ):
            output = await agent.run(_make_input(["ambiguous"]))

        resolution = output.result.concept_resolutions[0]
        assert resolution.catalog_column_id == "col-a"


class TestFuzzyConceptResolutionFallback:
    """REAL BUG, found live and logged across items 38/44/96/97:
    `resolve_business_term`'s exact match never fires for a compound
    extracted entity phrase like "total units traded" against the real
    glossary synonym "units traded". These tests cover the new fuzzy
    token-subsequence fallback that recovers exactly this case."""

    _glossary: ClassVar[list[dict]] = [
        {
            "business_concept": "Units Traded",
            "synonyms": ["quantity", "shares traded", "volume", "trade quantity"],
            "catalog_column_id": "col-units",
            "column_name": "UNITS",
            "table_name": "STAGING_TRANSACTIONS",
            "preferred": True,
            "source": "schema_enrichment",
        },
        {
            "business_concept": "State",
            "synonyms": ["region", "state"],
            "catalog_column_id": "col-state",
            "column_name": "STATE",
            "table_name": "DIM_CUSTOMER",
            "preferred": True,
            "source": "manual_ecommerce_poc",
        },
    ]

    async def test_fuzzy_fallback_resolves_a_real_compound_phrase(self) -> None:
        """Live-reproduced: Intent Understanding extracted "total units
        traded" where the real glossary synonym is exactly "units traded"
        -- the exact match finds nothing, but the fuzzy fallback must
        recover it via token-subsequence containment."""

        client = MagicMock()
        agent = OntologyAgent(client=client)

        with (
            patch(
                "navigraph_agents.understanding.ontology.agent.resolve_business_term",
                return_value=[],
            ),
            patch(
                "navigraph_agents.understanding.ontology.agent.list_business_concepts",
                return_value=self._glossary,
            ),
            patch(
                "navigraph_agents.understanding.ontology.agent.list_relationship_concepts",
                return_value=[],
            ),
        ):
            output = await agent.run(_make_input(["total units traded"]))

        resolution = output.result.concept_resolutions[0]
        assert resolution.resolved is True
        assert resolution.catalog_column_id == "col-units"
        assert resolution.table_name == "STAGING_TRANSACTIONS"
        assert output.result.unresolved_terms == []

    async def test_fuzzy_fallback_not_consulted_when_exact_match_succeeds(self) -> None:
        """The fuzzy fallback must never even be queried for an entity
        whose exact match already succeeded -- it only ever recovers a
        term that would otherwise be completely unresolved."""

        client = MagicMock()
        agent = OntologyAgent(client=client)

        exact_record = [
            {
                "business_concept": "Revenue",
                "catalog_column_id": "col-revenue",
                "column_name": "TOTALVALUE",
                "table_name": "STAGING_TRANSACTIONS",
                "preferred": True,
                "source": "schema_enrichment",
            }
        ]

        with (
            patch(
                "navigraph_agents.understanding.ontology.agent.resolve_business_term",
                return_value=exact_record,
            ),
            patch(
                "navigraph_agents.understanding.ontology.agent.list_business_concepts",
            ) as mock_list_concepts,
            patch(
                "navigraph_agents.understanding.ontology.agent.list_relationship_concepts",
                return_value=[],
            ),
        ):
            output = await agent.run(_make_input(["revenue"]))

        assert output.result.concept_resolutions[0].resolved is True
        mock_list_concepts.assert_not_called()

    async def test_fuzzy_fallback_avoids_short_word_substring_collision(self) -> None:
        """Real risk found while designing the fix: the real glossary has
        genuinely short synonyms (e.g. "state", "tax", "date", "isin").
        Naive substring matching (erasing word boundaries) would wrongly
        match the glossary synonym "state" inside an unrelated entity like
        "real estate report" (since "estate" literally contains "state").
        The token-based design must NOT match this."""

        client = MagicMock()
        agent = OntologyAgent(client=client)

        with (
            patch(
                "navigraph_agents.understanding.ontology.agent.resolve_business_term",
                return_value=[],
            ),
            patch(
                "navigraph_agents.understanding.ontology.agent.list_business_concepts",
                return_value=self._glossary,
            ),
            patch(
                "navigraph_agents.understanding.ontology.agent.list_relationship_concepts",
                return_value=[],
            ),
        ):
            output = await agent.run(_make_input(["real estate report"]))

        resolution = output.result.concept_resolutions[0]
        assert resolution.resolved is False
        assert output.result.unresolved_terms == ["real estate report"]

    async def test_fuzzy_fallback_ambiguous_match_stays_unresolved(self) -> None:
        """Two DIFFERENT glossary concepts (mapping to different real
        columns) both match the same entity via the fuzzy fallback --
        which one is actually meant can't be determined, so it must stay
        unresolved rather than guessing, matching every other "never
        guess" guard in this codebase."""

        client = MagicMock()
        agent = OntologyAgent(client=client)

        ambiguous_glossary = [
            {
                "business_concept": "Units Traded",
                "synonyms": ["units traded"],
                "catalog_column_id": "col-units",
                "column_name": "UNITS",
                "table_name": "STAGING_TRANSACTIONS",
                "preferred": True,
                "source": "schema_enrichment",
            },
            {
                "business_concept": "Total Transaction Value",
                "synonyms": ["units traded value"],
                "catalog_column_id": "col-value",
                "column_name": "TOTALVALUE",
                "table_name": "STAGING_TRANSACTIONS",
                "preferred": True,
                "source": "schema_enrichment",
            },
        ]

        with (
            patch(
                "navigraph_agents.understanding.ontology.agent.resolve_business_term",
                return_value=[],
            ),
            patch(
                "navigraph_agents.understanding.ontology.agent.list_business_concepts",
                return_value=ambiguous_glossary,
            ),
            patch(
                "navigraph_agents.understanding.ontology.agent.list_relationship_concepts",
                return_value=[],
            ),
        ):
            output = await agent.run(_make_input(["total units traded value today"]))

        resolution = output.result.concept_resolutions[0]
        assert resolution.resolved is False
        assert output.result.unresolved_terms == ["total units traded value today"]

    async def test_fuzzy_fallback_fetches_glossary_only_once_per_run(self) -> None:
        """Multiple entities needing the fallback in the same request must
        only trigger one `list_business_concepts` call, not one per
        entity -- a small, real efficiency guarantee this test locks in."""

        client = MagicMock()
        agent = OntologyAgent(client=client)

        with (
            patch(
                "navigraph_agents.understanding.ontology.agent.resolve_business_term",
                return_value=[],
            ),
            patch(
                "navigraph_agents.understanding.ontology.agent.list_business_concepts",
                return_value=self._glossary,
            ) as mock_list_concepts,
            patch(
                "navigraph_agents.understanding.ontology.agent.list_relationship_concepts",
                return_value=[],
            ),
        ):
            await agent.run(_make_input(["total units traded", "some other region"]))

        mock_list_concepts.assert_called_once()


class TestRelationshipResolution:
    """Since `_resolve_relationships` now returns every `RelationshipConcept`
    synced for the tenant unconditionally (see that method's own docstring
    for why: Schema Mapping's `_build_joins` is the real correctness gate,
    running downstream after Ontology's and Semantic Retrieval's
    resolutions are merged, which this agent alone can never see), these
    tests cover that it genuinely does return everything the graph has --
    regardless of what entities were extracted -- and still defends
    against a malformed/incomplete `RelationshipConcept` node."""

    async def test_every_synced_relationship_concept_is_returned(self) -> None:
        client = MagicMock()
        agent = OntologyAgent(client=client)

        relationship_records = [
            {
                "name": "Customer has RiskLevel",
                "subject_label": "Customer",
                "predicate": "HAS",
                "object_label": "RiskLevel",
                "realizing_table": "CUSTOMER_INFORMATION",
                "subject_key_column": "CUSTOMERID",
                "object_key_column": "RISKLEVEL",
            },
            {
                "name": "Order occurs on Date",
                "subject_label": "Order",
                "predicate": "OCCURS_ON",
                "object_label": "Date",
                "realizing_table": "FACT_ORDERS",
                "subject_key_column": "DATE_ID",
                "object_key_column": "DATE_ID",
            },
        ]

        with (
            patch(
                "navigraph_agents.understanding.ontology.agent.resolve_business_term",
                return_value=[],
            ),
            patch(
                "navigraph_agents.understanding.ontology.agent.list_relationship_concepts",
                return_value=relationship_records,
            ) as mock_list_rel,
        ):
            # Entities name neither "Customer"/"RiskLevel" nor "Order"/"Date"
            # at all -- REAL BUG this closes: a term like "orders" that only
            # resolves via Semantic Retrieval's separate LLM fallback (which
            # runs AFTER this agent) previously meant NEITHER relationship
            # could ever fire, since this agent had no way to know either
            # table was actually relevant yet.
            output = await agent.run(_make_input(["revenue", "gibberish"]))

        assert len(output.result.relationship_resolutions) == 2
        by_object = {r.object_label: r for r in output.result.relationship_resolutions}
        assert "RiskLevel" in by_object
        assert "Date" in by_object
        mock_list_rel.assert_called_once()

    async def test_no_relationships_synced_means_none_returned(self) -> None:
        client = MagicMock()
        agent = OntologyAgent(client=client)

        with (
            patch(
                "navigraph_agents.understanding.ontology.agent.resolve_business_term",
                return_value=[],
            ),
            patch(
                "navigraph_agents.understanding.ontology.agent.list_relationship_concepts",
                return_value=[],
            ),
        ):
            output = await agent.run(_make_input(["Customer", "RiskLevel"]))

        assert output.result.relationship_resolutions == []

    async def test_relationship_concept_missing_a_required_field_is_skipped(self) -> None:
        """A `RelationshipConcept` node with an unresolved `OPTIONAL MATCH`
        (e.g. curated but not yet fully wired up in the graph -- missing
        its realizing table or a key column) must be skipped defensively,
        never turned into an invalid `RelationshipResolution`."""

        client = MagicMock()
        agent = OntologyAgent(client=client)

        incomplete_record = {
            "name": "Customer has RiskLevel",
            "subject_label": "Customer",
            "predicate": "HAS",
            "object_label": "RiskLevel",
            "realizing_table": None,
            "subject_key_column": "CUSTOMERID",
            "object_key_column": "RISKLEVEL",
        }

        with (
            patch(
                "navigraph_agents.understanding.ontology.agent.resolve_business_term",
                return_value=[],
            ),
            patch(
                "navigraph_agents.understanding.ontology.agent.list_relationship_concepts",
                return_value=[incomplete_record],
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
