"""Ontology agent implementation.

Fully deterministic: no LLM call, no `prompts/` directory. Resolves each
entity extracted by Intent Understanding to a `BusinessConcept` -> `Column`
mapping via `navigraph_kg.api.resolve_business_term`, and separately scans
every `RelationshipConcept` synced for the tenant (via
`navigraph_kg.api.list_relationship_concepts` -- a tenant's activated
Semantic Model if it has one, or the hand-curated
`navigraph_kg.ontology.RELATIONSHIP_CONCEPTS` seed list otherwise, per
`navigraph_kg.ingestion.pipeline._load_relationship_concepts`) for any
relationship whose subject/object labels are both present among the input
entities.

Follows the same structural pattern as
`navigraph_agents.understanding.intent_understanding.agent`: open an OTel
span, never raise, always emit a `LineageEvent` and `AgentMetadata` with
`latency_ms` populated.
"""

from __future__ import annotations

import time

from navigraph_kg.api import (
    list_business_concepts,
    list_relationship_concepts,
    resolve_business_term,
)
from navigraph_kg.client import Neo4jClient
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


def _tokenize(text: str) -> list[str]:
    """Split into lowercase alphanumeric-run word tokens, e.g. "Units
    Traded" / "units-traded" -> `["units", "traded"]`.

    Deliberately PRESERVES word boundaries -- needed for
    `_glossary_term_matches_entity` below, where erasing boundaries would
    let a short glossary synonym accidentally match as a substring of an
    unrelated, longer word purely by coincidence (found while designing
    the fix: normalizing away spaces would make the glossary synonym
    `"state"` match inside `"real estate"`, since `"estate"` literally
    contains the letters `"state"` -- an entity in this dataset's
    brokerage/e-commerce questions would never mean this, but the
    collision is real and avoidable by keeping word boundaries)."""

    tokens: list[str] = []
    current: list[str] = []
    for ch in text.lower():
        if ch.isalnum():
            current.append(ch)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tokens


def _contains_token_subsequence(haystack: list[str], needle: list[str]) -> bool:
    """Is `needle` a contiguous run of tokens within `haystack`?"""

    if not needle:
        return False
    n, m = len(haystack), len(needle)
    return any(haystack[i : i + m] == needle for i in range(n - m + 1))


def _glossary_term_matches_entity(term: str, entity_tokens: list[str]) -> bool:
    """Does `entity_tokens` contain `term`'s own tokens as a contiguous
    run? Used by `OntologyAgent._resolve_concepts`'s fuzzy fallback -- see
    that method's docstring for the real gap this closes (a compound
    extracted entity like "total units traded" wrapping extra words
    around the glossary's exact synonym "units traded")."""

    term_tokens = _tokenize(term)
    return bool(term_tokens) and _contains_token_subsequence(entity_tokens, term_tokens)


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

        REAL BUG, found live and logged (items 38/44/96/97) across several
        earlier fixes this session: `resolve_business_term`'s Cypher
        requires EXACT (case-insensitive) equality against a
        `BusinessConcept`'s `name` or one of its `synonyms`. Intent
        Understanding's real LLM-based entity extraction is genuinely
        non-deterministic and often wraps the canonical phrase in extra
        words -- e.g. extracting `"total units traded"` where the real
        glossary synonym is exactly `"units traded"` -- so the exact match
        silently finds nothing even though a real, correct column exists.
        Confirmed live: a repeated call to this same agent with the
        identical real candidate data resolved differently across runs
        purely because of which exact phrasing Intent Understanding
        happened to extract that time. This was always a SAFE failure
        (the term surfaces as unresolved, at worst producing
        `unjoined_table_in_multi_table_query` rather than wrong data) but
        a real usability gap, not fixed until now.

        Fix: when the exact match returns nothing for an entity, fall back
        to a fuzzy match against the tenant's full glossary (fetched once
        per `run()` call via `list_business_concepts`, not once per
        entity, and only when actually needed) -- does the entity's own
        token sequence contain a glossary name/synonym's tokens as a
        contiguous run (`_glossary_term_matches_entity`)? This is
        deliberately ONE-DIRECTIONAL (the short, specific glossary term
        must be contained WITHIN the longer, free-text entity, never the
        reverse) -- the same safe direction `query.sql_generation.agent.
        _resolved_via_named_value` already established for the equivalent
        problem elsewhere in this codebase; matching the other direction
        would let a single short/generic word extracted as its own entity
        wrongly "contain" a much more specific multi-word business term.
        It is ALSO token-based rather than raw-substring, unlike that
        precedent -- glossary terms include real, short, common English
        words (`"tax"`, `"qty"`, `"date"`, `"city"`, `"tier"`, `"isin"`,
        confirmed via the live glossary) that would otherwise risk
        matching as an accidental substring of a completely unrelated
        longer word (e.g. `"state"` inside `"real estate"`) if word
        boundaries were erased.

        If the fuzzy fallback matches 2+ glossary concepts mapping to
        DIFFERENT columns for the same one entity string, this is a real
        ambiguity -- which one the entity actually means cannot be
        determined here, so it is left unresolved rather than guessed,
        matching this codebase's standing "never guess" discipline. The
        fuzzy fallback NEVER runs at all for an entity whose exact match
        already succeeded -- it only ever recovers a term that would
        otherwise have gone completely unresolved.
        """

        concept_resolutions: list[ConceptResolution] = []
        unresolved_terms: list[str] = []
        glossary: list[dict] | None = None

        for entity in entities:
            records = resolve_business_term(self._client, tenant_id=tenant_id, term=entity)
            used_fuzzy_fallback = False

            if not records:
                if glossary is None:
                    glossary = list_business_concepts(self._client, tenant_id=tenant_id)
                entity_tokens = _tokenize(entity)
                records = [
                    concept
                    for concept in glossary
                    if _glossary_term_matches_entity(
                        concept.get("business_concept") or "", entity_tokens
                    )
                    or any(
                        _glossary_term_matches_entity(synonym, entity_tokens)
                        for synonym in concept.get("synonyms") or []
                    )
                ]
                used_fuzzy_fallback = True

            if not records:
                concept_resolutions.append(ConceptResolution(term=entity, resolved=False))
                unresolved_terms.append(entity)
                continue

            if used_fuzzy_fallback:
                distinct_column_ids = {r.get("catalog_column_id") for r in records}
                if len(distinct_column_ids) > 1:
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

    def _resolve_relationships(
        self,
        entities: list[str],
        concept_resolutions: list[ConceptResolution],
        *,
        tenant_id: str,
    ) -> list[RelationshipResolution]:
        """Return every `RelationshipConcept` synced for the tenant,
        unconditionally -- no label-matching or "is the realizing table
        implied by my own concept resolutions" gate at all.

        HISTORY: earlier versions of this method tried to guess relevance
        here, first via literal/named-instance label matching against
        `entities` (`_label_or_instance_matches` -- now dead code, see the
        prior version of this docstring in git history), then via an
        "implied table" relaxation once a concept resolution already
        pointed at the concept's `realizing_table`. Both were real,
        incremental fixes for real live bugs, but each one only patched
        ONE specific way this agent's guess could be wrong.

        REAL BUG, found live testing the e-commerce data source, that no
        amount of further guessing here can fix: "How many orders were
        placed in the last 30 days?" resolves "orders" to
        `FACT_ORDERS.ORDER_ID` ONLY via Semantic Retrieval's separate LLM
        fallback -- which runs AFTER this agent in the real request
        pipeline (see `request_orchestrator.agent`'s call order). This
        method's own `concept_resolutions` parameter is Ontology's
        glossary-only view, so `implied_tables` could never have included
        `FACT_ORDERS` for that question no matter how the relaxation logic
        was written -- the information this method would need already
        doesn't exist yet at the point this agent runs.

        The actual fix: stop guessing here at all. `navigraph_agents.
        understanding.schema_mapping.agent._build_joins` is the REAL
        correctness gate, not this method -- it runs downstream, after
        BOTH Ontology's and Semantic Retrieval's resolutions have been
        merged into one final resolved-column set, and it independently
        re-verifies (against the real live catalog) that a relationship's
        `subject_key_column` exists on both the realizing table AND
        exactly one other resolved table before ever emitting a
        `JoinSpec` (its own ambiguity guard). A relationship concept that
        turns out irrelevant to a given question (its `realizing_table`
        was never actually resolved by anything, or no other resolved
        table shares its join key) is simply skipped there as a no-op --
        never a wrong join. Returning every relationship concept
        unconditionally removes an entire class of "Ontology didn't know
        what Semantic Retrieval would later find" bugs at the source,
        rather than patching each new instance of it as it's discovered.
        A real tenant's relationship-concept set is small (on the order of
        a dozen), so returning it all every time is cheap.
        """

        del entities, concept_resolutions  # no longer used -- see docstring

        relationship_resolutions: list[RelationshipResolution] = []

        for concept in list_relationship_concepts(self._client, tenant_id=tenant_id):
            subject_label = concept.get("subject_label")
            predicate = concept.get("predicate")
            object_label = concept.get("object_label")
            realizing_table = concept.get("realizing_table")
            subject_key_column = concept.get("subject_key_column")
            object_key_column = concept.get("object_key_column")

            # A RelationshipConcept node could in principle be missing any
            # of these (e.g. curated but not yet fully wired up in the
            # graph). Treat that defensively as "no usable match" rather
            # than constructing an invalid `RelationshipResolution` (whose
            # fields are all required `str`) and letting a
            # `ValidationError` bubble up.
            if (
                subject_label is None
                or predicate is None
                or object_label is None
                or realizing_table is None
                or subject_key_column is None
                or object_key_column is None
            ):
                continue

            relationship_resolutions.append(
                RelationshipResolution(
                    subject_label=subject_label,
                    predicate=predicate,
                    object_label=object_label,
                    realizing_table=realizing_table,
                    subject_key_column=subject_key_column,
                    object_key_column=object_key_column,
                )
            )

        return relationship_resolutions
