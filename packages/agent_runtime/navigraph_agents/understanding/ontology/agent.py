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

from navigraph_kg.api import (
    entity_matches_reference_node,
    get_relationship_concept,
    resolve_business_term,
)
from navigraph_kg.client import Neo4jClient
from navigraph_kg.ontology import (
    NODE_ASSET,
    NODE_CHANNEL,
    NODE_CUSTOMER_TYPE,
    NODE_EXCHANGE,
    NODE_INDUSTRY,
    NODE_INVESTMENT_CAPACITY_BAND,
    NODE_MARKET,
    NODE_RISK_LEVEL,
    NODE_SECTOR,
    RELATIONSHIP_CONCEPTS,
)

# Labels that correspond to real, crawled Tier-1 reference-data node types
# (see `navigraph_kg.ingestion.pipeline._sync_reference_data`) -- these are
# the only labels `entity_matches_reference_node` is ever worth querying
# for; "Customer"/"Transaction" (the other subject labels seen in
# `RELATIONSHIP_CONCEPTS`) are deliberately excluded from the graph
# entirely (customer/transaction-cardinality data), so no such node type
# exists to match against.
_REFERENCE_NODE_LABELS = {
    NODE_ASSET,
    NODE_MARKET,
    NODE_EXCHANGE,
    NODE_SECTOR,
    NODE_INDUSTRY,
    NODE_CHANNEL,
    NODE_CUSTOMER_TYPE,
    NODE_RISK_LEVEL,
    NODE_INVESTMENT_CAPACITY_BAND,
}
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


def _normalize_label(text: str) -> str:
    """Strip everything but letters/digits and lowercase, so "RiskLevel"
    and "risk level" (or "risk-level") compare equal. REAL BUG, found
    live: `_label_matches_entities` used to compare `label.lower()`
    against `entity.lower()` verbatim, so a real extracted entity like
    "risk level" (the natural two-word phrasing a real question and this
    project's own golden set both use) never matched the seed data's
    single-token canonical label "RiskLevel" -- the space is the only
    difference, but substring matching alone can never bridge it. This
    silently dropped the "Customer has RiskLevel" relationship for every
    real question phrased with a space, which meant Schema Mapping never
    got the join it needed.
    """

    return "".join(ch for ch in text.lower() if ch.isalnum())


def _label_matches_entities(label: str, entities: list[str]) -> bool:
    """Case-insensitive, whitespace/punctuation-insensitive match of a
    relationship concept's subject/object label against the input
    entities.

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

    label_norm = _normalize_label(label)
    return any(
        label_norm in _normalize_label(entity) or _normalize_label(entity) in label_norm
        for entity in entities
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
                    entities, concept_resolutions, tenant_id=request_context.tenant_id
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
                    table_name=chosen.get("table_name"),
                    preferred=chosen.get("preferred"),
                )
            )

        return concept_resolutions, unresolved_terms

    def _label_or_instance_matches(
        self, label: str, entities: list[str], *, tenant_id: str
    ) -> bool:
        """A relationship concept's label matches if either (a) the label
        word itself appears among the extracted entities (the original
        check), or (b) -- REAL BUG, found live -- an entity names a real,
        specific instance of that category instead of the category word
        (e.g. "Athens Exchange" rather than "market"). `_label_matches_entities`
        alone can never bridge that gap; a real question naming a specific
        market/asset/channel/risk level/etc. by name would otherwise never
        resolve the relationship it needs. (b) is only ever checked for
        labels that correspond to a real, crawled reference-data node type
        (`_REFERENCE_NODE_LABELS`) -- "Customer"/"Transaction" have no such
        node type at all (customer/transaction-cardinality data is
        deliberately excluded from the graph), so querying for them would
        just waste a call.
        """

        if _label_matches_entities(label, entities):
            return True
        if label not in _REFERENCE_NODE_LABELS:
            return False
        return any(
            entity_matches_reference_node(
                self._client, tenant_id=tenant_id, label=label, entity=entity
            )
            for entity in entities
        )

    def _resolve_relationships(
        self,
        entities: list[str],
        concept_resolutions: list[ConceptResolution],
        *,
        tenant_id: str,
    ) -> list[RelationshipResolution]:
        """Scan every hand-curated `RelationshipConcept` seed for one whose
        subject AND object label both appear among the input entities
        (directly, or via a named real instance -- see
        `_label_or_instance_matches`), and resolve each match against the
        real graph.

        REAL BUG, found live testing the e-commerce data source, in two
        stages. First: every e-commerce `RelationshipConcept`'s
        `subject_label` is `"Order"` or `"OrderItem"` (see
        `navigraph_kg.ontology.RELATIONSHIP_CONCEPTS`'s e-commerce block)
        -- a table-role word real users almost never say. A question like
        "What is the total revenue by channel?" mentions "channel"
        (matches `object_label`) but never "order" in any form, so
        `_label_or_instance_matches(subject_label=...)` never matched,
        "Order uses Channel" never fired, and Schema Mapping's
        `_build_joins` -- which only ever considers relationships Ontology
        actually resolved -- never got a join to build, even though
        `FACT_ORDERS` (the concept's own `realizing_table`) was ALREADY
        one of the resolved tables once "revenue" resolved via the
        glossary. Second, found live re-testing after the first fix
        shipped: the SAME problem recurs on the OBJECT side -- "What are
        the top 5 categories by revenue?" mentions "categories" (which
        resolves to `DIM_PRODUCT.CATEGORY`) but never the word "product",
        so "OrderItem involves Product" (`object_label="Product"`) never
        fired either, even after "revenue" correctly implied
        `FACT_ORDER_ITEMS`. No amount of literal-word or reference-node
        matching on "Order"/"Product" themselves can fix either direction
        -- these are table-role words, and there is no bound on how many
        different real column/dimension names could refer to "that
        table" without ever saying its role name.

        Fix: once the concept's `realizing_table` is already implied by a
        resolved business concept (some entity in this question resolved,
        via the deterministic glossary path, to a column on that exact
        table -- matched by core table name, `STAGING_` prefix ignored,
        same normalization `schema_mapping._build_joins` already uses),
        BOTH the subject and object literal/instance checks are skipped
        entirely -- the relationship fires unconditionally. This is safe
        specifically because `_build_joins` is the actual correctness
        gate, not this method: it independently re-verifies, against the
        real live catalog, that the relationship's `subject_key_column`
        exists on both the realizing table AND exactly one other resolved
        table before ever emitting a `JoinSpec` (its own ambiguity guard,
        item 87) -- a relationship_resolution that turns out to be
        irrelevant (its `realizing_table` was never actually resolved, or
        no other table shares its join key) is simply skipped there as a
        no-op, never a wrong join. This requires a real `ColumnGlossary`
        entry for the measure/dimension term to exist (Semantic
        Retrieval's LLM-fallback resolutions don't feed back into
        `concept_resolutions`, only Ontology's own glossary path does) --
        see BUILD_LOG.md's e-commerce ColumnGlossary entry for why that
        glossary is a necessary companion to this fix. When the realizing
        table is NOT implied, behavior is unchanged: both labels must
        still match literally or via a named reference-node instance,
        exactly as before.
        """

        def _core_name(table_name: str) -> str:
            return table_name.upper().removeprefix("STAGING_")

        implied_tables = {
            _core_name(cr.table_name)
            for cr in concept_resolutions
            if cr.resolved and cr.table_name
        }

        relationship_resolutions: list[RelationshipResolution] = []

        for concept in RELATIONSHIP_CONCEPTS:
            subject_label = concept["subject_label"]
            object_label = concept["object_label"]
            realizing_table_implied = _core_name(concept["realizing_table"]) in implied_tables

            if not realizing_table_implied:
                if not self._label_or_instance_matches(
                    subject_label, entities, tenant_id=tenant_id
                ):
                    continue
                if not self._label_or_instance_matches(
                    object_label, entities, tenant_id=tenant_id
                ):
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
