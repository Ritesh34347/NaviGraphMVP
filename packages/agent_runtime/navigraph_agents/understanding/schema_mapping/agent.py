"""Schema Mapping agent implementation.

Fully deterministic: no LLM call, no `prompts/` directory, and (unlike the
Ontology agent) no external client dependency at all -- this agent is a
pure function of its input. It assembles whatever Ontology's
`concept_resolutions`/`relationship_resolutions` and Semantic Retrieval's
`semantic_matches` already resolved, cross-references
`catalog_inventory` (produced by Metadata Discovery) to get concrete
table/column/data-type information, assigns each resolved column a query
role, and derives the joins needed to bring resolved columns from more
than one table together.

Follows the same structural pattern as
`navigraph_agents.understanding.intent_understanding.agent`: open an OTel
span, never raise, always emit a `LineageEvent` and `AgentMetadata` with
`latency_ms` populated.
"""

from __future__ import annotations

import time
from typing import Literal

from navigraph_shared.contracts import AgentMetadata, LineageEvent
from navigraph_shared.telemetry import get_tracer, record_agent_invocation
from opentelemetry.trace import Tracer

from navigraph_agents.understanding.schema_mapping.contracts import (
    CatalogInventoryEntry,
    JoinSpec,
    ResolvedColumnRef,
    SchemaMappingInput,
    SchemaMappingOutput,
    SchemaMappingPayload,
    SchemaMappingResult,
)

AGENT_NAME = "understanding.schema_mapping"

# Data types treated as numeric for role-assignment purposes (step 4). This
# is deliberately a small, real set of Snowflake/Postgres-catalog-style
# numeric type names rather than an attempt at exhaustive type-system
# coverage -- see `_assign_role`'s docstring for the full rule.
_NUMERIC_DATA_TYPES = {"NUMBER", "FLOAT", "INTEGER", "DECIMAL", "NUMERIC", "DOUBLE"}

# Intents for which a numeric column should be treated as a measure rather
# than a dimension -- see `_assign_role`.
_MEASURE_INTENTS = {"metric_lookup", "comparison", "trend_analysis"}


def _assign_role(data_type: str, intent: str) -> Literal["measure", "dimension", "filter"]:
    """Assign a resolved column's query role.

    v1 rule: a numeric-looking `data_type` (case-insensitive match against
    `_NUMERIC_DATA_TYPES`) combined with an intent that actually asks for a
    quantity (`_MEASURE_INTENTS`) makes it a `"measure"`; everything else
    is a `"dimension"`. There is no `"filter"` assignment logic in this
    phase -- the `Literal["measure", "dimension", "filter"]` on
    `ResolvedColumnRef.role` includes it for future use (e.g. once this
    agent understands WHERE-clause terms specifically), but nothing
    produces it yet.
    """

    if data_type.upper() in _NUMERIC_DATA_TYPES and intent in _MEASURE_INTENTS:
        return "measure"
    return "dimension"


class SchemaMappingAgent:
    """Assembles resolved concepts, relationships, and semantic matches into
    concrete tables, columns, and joins. Pure function of its input -- no
    external client dependency."""

    def __init__(self, tracer: Tracer | None = None) -> None:
        self._tracer = tracer or get_tracer("navigraph-agent-runtime")

    async def run(self, input: SchemaMappingInput) -> SchemaMappingOutput:
        start = time.perf_counter()
        request_context = input.request_context
        payload = input.payload

        with self._tracer.start_as_current_span("agent.schema_mapping.run") as span:
            span.set_attribute("navigraph.tenant_id", request_context.tenant_id)
            span.set_attribute("navigraph.trace_id", request_context.trace_id)
            span.set_attribute("navigraph.agent_name", AGENT_NAME)

            inventory_by_id: dict[str, CatalogInventoryEntry] = {
                entry.catalog_column_id: entry for entry in payload.catalog_inventory
            }

            columns, unmapped_from_lookup = self._resolve_columns(
                payload, inventory_by_id, intent=payload.intent
            )

            tables = sorted({column.table_name for column in columns})

            joins = self._build_joins(payload, columns)

            unmapped_from_concepts = self._unmapped_concept_terms(payload)

            unmapped_terms = _dedupe_preserve_order(
                [*unmapped_from_lookup, *unmapped_from_concepts]
            )

            result = SchemaMappingResult(
                tables=tables,
                columns=columns,
                joins=joins,
                unmapped_terms=unmapped_terms,
            )

            confidence = 1.0 if not unmapped_terms else 0.5

            lineage_event = LineageEvent(
                agent_name=AGENT_NAME,
                input_summary=(
                    f"intent={payload.intent} "
                    f"concept_resolutions={len(payload.concept_resolutions)} "
                    f"semantic_matches={len(payload.semantic_matches)} "
                    f"catalog_inventory={len(payload.catalog_inventory)}"
                ),
                output_summary=(
                    f"tables={tables} columns={len(columns)} joins={len(joins)} "
                    f"unmapped_terms={unmapped_terms}"
                ),
                tenant_id=request_context.tenant_id,
                trace_id=request_context.trace_id,
            )

            latency_ms = (time.perf_counter() - start) * 1000.0

            metadata = AgentMetadata(latency_ms=latency_ms)

            span.set_attribute("navigraph.unmapped_count", len(unmapped_terms))

        record_agent_invocation(AGENT_NAME, latency_ms=latency_ms, success=True)

        return SchemaMappingOutput(
            result=result,
            confidence=confidence,
            lineage_events=[lineage_event],
            errors=[],
            metadata=metadata,
        )

    @staticmethod
    def _resolve_columns(
        payload: SchemaMappingPayload,
        inventory_by_id: dict[str, CatalogInventoryEntry],
        *,
        intent: str,
    ) -> tuple[list[ResolvedColumnRef], list[str]]:
        """Build the deduplicated list of resolved columns plus any terms
        whose resolved `catalog_column_id` was not found in the inventory.

        Candidate (term, catalog_column_id) pairs come from two sources --
        `concept_resolutions` where `resolved=True` and `semantic_matches`
        where `matched=True` -- collected in that order and deduped by
        `catalog_column_id` so a term resolved by both Ontology and
        Semantic Retrieval only produces one column (keeping whichever
        term was seen first, from `concept_resolutions`).
        """

        candidates: list[tuple[str, str]] = []

        for cr in payload.concept_resolutions:
            if cr.resolved and cr.catalog_column_id is not None:
                candidates.append((cr.term, cr.catalog_column_id))

        for match in payload.semantic_matches:
            if match.matched and match.catalog_column_id is not None:
                candidates.append((match.term, match.catalog_column_id))

        seen_column_ids: set[str] = set()
        columns: list[ResolvedColumnRef] = []
        unmapped_terms: list[str] = []

        for term, catalog_column_id in candidates:
            if catalog_column_id in seen_column_ids:
                continue
            seen_column_ids.add(catalog_column_id)

            entry = inventory_by_id.get(catalog_column_id)
            if entry is None:
                # Resolved upstream but absent from this request's catalog
                # inventory snapshot -- shouldn't normally happen, but don't
                # crash: drop it and surface the term as unmapped instead.
                unmapped_terms.append(term)
                continue

            columns.append(
                ResolvedColumnRef(
                    term=term,
                    catalog_column_id=entry.catalog_column_id,
                    table_name=entry.table_name,
                    schema_name=entry.schema_name,
                    column_name=entry.column_name,
                    data_type=entry.data_type,
                    role=_assign_role(entry.data_type, intent),
                )
            )

        return columns, unmapped_terms

    @staticmethod
    def _unmapped_concept_terms(payload: SchemaMappingPayload) -> list[str]:
        """Terms Ontology could not resolve at all, and that Semantic
        Retrieval didn't rescue either (step 7)."""

        matched_terms = {match.term for match in payload.semantic_matches if match.matched}
        return [
            cr.term
            for cr in payload.concept_resolutions
            if not cr.resolved and cr.term not in matched_terms
        ]

    @staticmethod
    def _build_joins(
        payload: SchemaMappingPayload,
        columns: list[ResolvedColumnRef],
    ) -> list[JoinSpec]:
        """Derive the joins needed to bring resolved columns from more than
        one table together.

        Heuristic (documented here since "when do we actually need a
        join" is inherently judgement-driven, not a hard rule): NaviGraph's
        `RelationshipConcept`s are each realized within a SINGLE
        denormalized table (e.g. "Customer has RiskLevel" realized via
        `CUSTOMER_INFORMATION.CUSTOMERID` / `.RISKLEVEL` -- see
        `navigraph_kg.ontology.RELATIONSHIP_CONCEPTS`'s module docstring),
        not as a genuine two-table foreign key. A join is only actually
        needed when the resolved columns ALSO include a column from some
        OTHER table (e.g. a measure from `TRANSACTIONS`) that must be
        connected to the relationship's `realizing_table` to pull in that
        relationship's value (e.g. `RISKLEVEL`). So: for each relationship
        resolution, find every distinct resolved-column table that differs
        from its `realizing_table`; for each such "other table", emit one
        join keyed on `subject_key_column` on both sides -- the seed data's
        `subject_key_column` (e.g. `CUSTOMERID`) is consistently the shared
        natural key across NaviGraph's denormalized Snowflake tables, so it
        is the correct join key even though the relationship's OTHER key
        (`object_key_column`) lives only inside `realizing_table` as the
        value being fetched, not as a second join key. A relationship is
        only considered at all when its `realizing_table` is itself one of
        the resolved columns' tables (matching the spec's "two tables
        actually present in the resolved columns" -- a relationship whose
        realizing table was never selected has nothing to join to).
        Explicitly guards against a self-join (identical left/right table)
        and dedupes identical `(left_table, left_column, right_table,
        right_column)` joins across multiple relationship resolutions.

        REAL BUG, found live: `RELATIONSHIP_CONCEPTS`' `realizing_table`
        values are bare names (e.g. `"TRANSACTIONS"`, `"CUSTOMER_INFORMATION"`),
        but every column resolved via Ontology's business-concept path (the
        dominant, deterministic, no-LLM path -- all real
        `SCHEMA_ENRICHMENT`-derived glossary mappings point exclusively at
        `STAGING_`-prefixed tables, see LIMITATIONS.md item 14) has a
        `table_name` of e.g. `"STAGING_TRANSACTIONS"`, not `"TRANSACTIONS"`.
        An exact-string `rel.realizing_table not in resolved_tables` check
        therefore NEVER matched for that path -- it only ever matched by
        accident when Semantic Retrieval's LLM fallback happened to resolve
        a bare (`FAR_TRANS`-schema) table name instead. In production this
        meant almost every real multi-table question silently got zero
        joins (a Cartesian product before that was separately fixed; a hard
        `unjoined_table_in_multi_table_query` failure after). Fixed by
        matching `realizing_table` against resolved tables with a leading
        `STAGING_` prefix stripped from both sides before comparing, then
        using the REAL resolved table name (never the bare literal) for the
        emitted `JoinSpec` -- SQL Generation's `_qualified_table` looks up
        the schema by the exact resolved table name, so using the wrong
        (bare) name there would silently produce an unqualified `FROM
        TRANSACTIONS` referencing whatever table that name resolves to in
        the connection's default schema, which is not guaranteed to be the
        right one.
        """

        def _core_name(table_name: str) -> str:
            return table_name.upper().removeprefix("STAGING_")

        resolved_tables = {column.table_name for column in columns}
        core_to_real = {_core_name(table): table for table in resolved_tables}

        joins: list[JoinSpec] = []
        seen_joins: set[tuple[str, str, str, str]] = set()

        for rel in payload.relationship_resolutions:
            # Both sides of the join must actually be present among the
            # resolved columns' tables -- a relationship whose
            # `realizing_table` was never itself selected has nothing to
            # join *to*, so it is skipped rather than emitting a dangling
            # join. Matched by core name (STAGING_ prefix ignored) so this
            # fires regardless of which schema variant got resolved.
            real_realizing_table = core_to_real.get(_core_name(rel.realizing_table))
            if real_realizing_table is None:
                continue

            other_tables = {
                table for table in resolved_tables if table != real_realizing_table
            }

            for other_table in sorted(other_tables):
                if other_table == real_realizing_table:
                    # Defensive; `other_tables` already excludes this, but
                    # a self-join is exactly the "nonsensical join" this
                    # logic must never emit.
                    continue

                join_key = (
                    other_table,
                    rel.subject_key_column,
                    real_realizing_table,
                    rel.subject_key_column,
                )
                if join_key in seen_joins:
                    continue
                seen_joins.add(join_key)

                joins.append(
                    JoinSpec(
                        left_table=other_table,
                        left_column=rel.subject_key_column,
                        right_table=real_realizing_table,
                        right_column=rel.subject_key_column,
                        relationship_concept=(
                            f"{rel.subject_label} {rel.predicate} {rel.object_label}"
                        ),
                    )
                )

        return joins


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    """Dedupe a list of strings while preserving first-seen order."""

    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
