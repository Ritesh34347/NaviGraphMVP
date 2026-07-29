"""Ontology agent implementation.

Fully deterministic: no LLM call, no `prompts/` directory. Resolves each
entity extracted by Intent Understanding to a `BusinessConcept` -> `Column`
mapping via `navigraph_kg.api.resolve_business_term`, and separately scans
the hand-curated `navigraph_kg.ontology.RELATIONSHIP_CONCEPTS` seed list for
any relationship whose subject/object labels are both present among the
input entities, resolving each match via
`navigraph_kg.api.get_relationship_concept`.

Follows the same structural pattern as
`navigraph_agents.understanding.intent_understanding.agent`: open an OTel
span, never raise, always emit a `LineageEvent` and `AgentMetadata` with
`latency_ms` populated.
"""

from __future__ import annotations

import time

from navigraph_kg.api import get_relationship_concept, resolve_business_term
from navigraph_kg.client import Neo4jClient
from navigraph_kg.ontology import RELATIONSHIP_CONCEPTS
from navigraph_shared.contracts import AgentError, AgentMetadata, LineageEvent
from navigraph_shared.telemetry import (
    get_tracer,
    record_agent_error,
    record_agent_invocation,
)
from opentelemetry.trace import Tracer

from navigraph_agents.understanding.ontology.contracts import (
    ConceptResolution,
    OntologyInput,
    OntologyOutput,
    OntologyResult,
    RelationshipResolution,
)

AGENT_NAME = "understanding.ontology"


def _label_matches_entities(label: str, entities: list[str]) -> bool:
    """Case-insensitive match of a relationship concept's subject/object
    label against the input entities.

    Uses substring matching in either direction (label-in-entity OR
    entity-in-label), not just exact equality: Intent Understanding's
    extracted entities are free-text spans from the user's question (e.g.
    "risk level" or "the customer's risk"), whereas `RELATIONSHIP_CONCEPTS`
    labels are canonical single tokens (e.g. "RiskLevel", "Customer"). An
    exact-match-only comparison would almost never fire in practice. This is
    a deliberate judgement call, not a proven-correct heuristic -- it can
    over-match on short/generic labels, but under-matching (missing a real
    relationship) is the worse failure mode here since a false-negative
    silently drops a join the Schema Mapping agent needs downstream.
    """

    label_lower = label.lower()
    return any(
        label_lower in entity.lower() or entity.lower() in label_lower for entity in entities
    )


class OntologyAgent:
    """Resolves business terms and relationship concepts from the knowledge graph."""

    def __init__(self, client: Neo4jClient, tracer: Tracer | None = None) -> None:
        self._client = client
        self._tracer = tracer or get_tracer("navigraph-agent-runtime")

    async def run(self, input: OntologyInput) -> OntologyOutput:
        start = time.perf_counter()
        request_context = input.request_context
        entities = input.payload.entities

        errors: list[AgentError] = []
        concept_resolutions: list[ConceptResolution] = []
        relationship_resolutions: list[RelationshipResolution] = []
        unresolved_terms: list[str] = []

        with self._tracer.start_as_current_span("agent.ontology.run") as span:
            span.set_attribute("navigraph.tenant_id", request_context.tenant_id)
            span.set_attribute("navigraph.trace_id", request_context.trace_id)
            span.set_attribute("navigraph.agent_name", AGENT_NAME)

            try:
                concept_resolutions, unresolved_terms = self._resolve_concepts(
                    entities, tenant_id=request_context.tenant_id
                )
                relationship_resolutions = self._resolve_relationships(
                    entities, tenant_id=request_context.tenant_id
                )
            except Exception as exc:  # noqa: BLE001 - never let a KG-side failure crash the agent
                errors.append(
                    AgentError(
                        code="knowledge_graph_query_failed",
                        message=f"Knowledge graph query failed: {exc}",
                        recoverable=True,
                    )
                )
                # The graph is unreachable (or a query is broken) -- there is
                # no partial result worth trusting, so every entity is
                # treated as unresolved rather than returning a mix of
                # results from before/after the failure.
                concept_resolutions = [
                    ConceptResolution(term=entity, resolved=False) for entity in entities
                ]
                unresolved_terms = list(entities)
                relationship_resolutions = []

            result = OntologyResult(
                concept_resolutions=concept_resolutions,
                relationship_resolutions=relationship_resolutions,
                unresolved_terms=unresolved_terms,
            )

            confidence = 0.0 if errors else (1.0 if not unresolved_terms else 0.5)

            lineage_event = LineageEvent(
                agent_name=AGENT_NAME,
                input_summary=f"entities={entities}",
                output_summary=(
                    f"resolved={len(concept_resolutions) - len(unresolved_terms)} "
                    f"unresolved={unresolved_terms} "
                    f"relationships={len(relationship_resolutions)}"
                ),
                tenant_id=request_context.tenant_id,
                trace_id=request_context.trace_id,
            )

            latency_ms = (time.perf_counter() - start) * 1000.0

            metadata = AgentMetadata(latency_ms=latency_ms)

            span.set_attribute("navigraph.unresolved_count", len(unresolved_terms))

        record_agent_invocation(AGENT_NAME, latency_ms=latency_ms, success=not errors)
        for error in errors:
            record_agent_error(AGENT_NAME, error_code=error.code, recoverable=error.recoverable)

        return OntologyOutput(
            result=result,
            confidence=confidence,
            lineage_events=[lineage_event],
            errors=errors,
            metadata=metadata,
        )

    def _resolve_concepts(
        self, entities: list[str], *, tenant_id: str
    ) -> tuple[list[ConceptResolution], list[str]]:
        """Resolve each entity to a `BusinessConcept` -> `Column` mapping.

        `resolve_business_term` can return more than one match (e.g. more
        than one glossary source mapping the same term to different
        columns); the record with `preferred=True` wins, falling back to
        the first record returned when none is marked preferred -- there is
        no other ordering guarantee in `resolve_business_term`'s Cypher (no
        `ORDER BY`), so "first" here is just "whatever Neo4j returned first",
        picked only as a deterministic-enough fallback rather than a
        meaningful ranking.
        """

        concept_resolutions: list[ConceptResolution] = []
        unresolved_terms: list[str] = []

        for entity in entities:
            records = resolve_business_term(self._client, tenant_id=tenant_id, term=entity)

            if not records:
                concept_resolutions.append(ConceptResolution(term=entity, resolved=False))
                unresolved_terms.append(entity)
                continue

            chosen = next((r for r in records if r.get("preferred") is True), records[0])

            concept_resolutions.append(
                ConceptResolution(
                    term=entity,
                    resolved=True,
                    business_concept=chosen.get("business_concept"),
                    catalog_column_id=chosen.get("catalog_column_id"),
                    column_name=chosen.get("column_name"),
                    preferred=chosen.get("preferred"),
                )
            )

        return concept_resolutions, unresolved_terms

    def _resolve_relationships(
        self, entities: list[str], *, tenant_id: str
    ) -> list[RelationshipResolution]:
        """Scan every hand-curated `RelationshipConcept` seed for one whose
        subject AND object label both appear among the input entities, and
        resolve each match against the real graph."""

        relationship_resolutions: list[RelationshipResolution] = []

        for concept in RELATIONSHIP_CONCEPTS:
            subject_label = concept["subject_label"]
            object_label = concept["object_label"]

            if not _label_matches_entities(subject_label, entities):
                continue
            if not _label_matches_entities(object_label, entities):
                continue

            record = get_relationship_concept(
                self._client,
                tenant_id=tenant_id,
                subject_label=subject_label,
                predicate=concept["predicate"],
                object_label=object_label,
            )

            if record is None:
                continue

            # `get_relationship_concept`'s Cypher uses OPTIONAL MATCH for the
            # realizing table and key columns, so a matched
            # `RelationshipConcept` node could in principle come back with
            # some of these still None (e.g. curated but not yet fully
            # wired up in the graph). Treat that defensively as "no usable
            # match" rather than constructing an invalid
            # `RelationshipResolution` (whose fields are all required
            # `str`) and letting a `ValidationError` bubble up.
            realizing_table = record.get("realizing_table")
            subject_key_column = record.get("subject_key_column")
            object_key_column = record.get("object_key_column")
            if realizing_table is None or subject_key_column is None or object_key_column is None:
                continue

            relationship_resolutions.append(
                RelationshipResolution(
                    subject_label=subject_label,
                    predicate=concept["predicate"],
                    object_label=object_label,
                    realizing_table=realizing_table,
                    subject_key_column=subject_key_column,
                    object_key_column=object_key_column,
                )
            )

        return relationship_resolutions
