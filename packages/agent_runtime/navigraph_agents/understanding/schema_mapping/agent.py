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
            columns = self._collapse_redundant_key_only_tables(
                columns, payload.catalog_inventory
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
    def _collapse_redundant_key_only_tables(
        columns: list[ResolvedColumnRef],
        catalog_inventory: list[CatalogInventoryEntry],
    ) -> list[ResolvedColumnRef]:
        """Redirect a resolved column away from a table that contributes
        NOTHING beyond that one column, when another already-resolved
        table has a real column of the identical name.

        REAL BUG, found live in the golden set (gq_002 "How many
        transactions has each customer made?", gq_009 "How has the
        customer base's risk profile changed over time?"): Semantic
        Retrieval's real LLM call is non-deterministic (see LIMITATIONS.md
        item 38/44) and can resolve a bare entity like "customer" to a
        DIFFERENT table's copy of the same natural key than the table the
        question's other terms already anchor on -- e.g. resolving
        "transactions" to `STAGING_TRANSACTIONS.TRANSACTIONID` and
        "customer" to `CUSTOMER_INFORMATION.CUSTOMERID` instead of
        `STAGING_TRANSACTIONS.CUSTOMERID` (confirmed live: a direct,
        repeated call to Semantic Retrieval with the identical real
        candidate list resolved "customer" to `STAGING_TRANSACTIONS.CUSTOMERID`
        on one run). Both resolutions are real, valid columns -- `_resolve_columns`'s
        dedupe-by-`catalog_column_id` can't collapse them, since they're
        different physical columns -- so the extra table pulled in
        `CUSTOMER_INFORMATION` for a question that never needed anything
        from it beyond a key value the anchor table (`STAGING_TRANSACTIONS`)
        already has natively. That correctly (per this agent's own
        ambiguous/missing-join safety guards) surfaced as a real
        `unjoined_table_in_multi_table_query` failure rather than ever
        guessing a join -- but the question was answerable all along
        using a single table.

        A resolved column's table is a "key-only" candidate for collapsing
        when it contributes NO other resolved column. If some OTHER
        already-resolved table (one that itself has a resolved column, so
        it's genuinely needed) has a REAL column of the identical name per
        `catalog_inventory` -- even if that column was never itself
        explicitly resolved as a term -- the key-only table's column is
        redirected to that other table's copy, and the key-only table
        drops out of the resolved set entirely. A table that contributes
        any OTHER attribute (e.g. `RISKLEVEL`, which no other resolved
        table happens to also have) is never touched -- it is genuinely
        needed and must still go through `_build_joins` normally.
        """

        if len(columns) < 2:
            return columns

        columns_by_table: dict[str, list[ResolvedColumnRef]] = {}
        for c in columns:
            columns_by_table.setdefault(c.table_name, []).append(c)

        if len(columns_by_table) < 2:
            return columns

        catalog_by_table: dict[str, dict[str, CatalogInventoryEntry]] = {}
        for entry in catalog_inventory:
            catalog_by_table.setdefault(entry.table_name, {})[
                entry.column_name.upper()
            ] = entry

        rewritten: list[ResolvedColumnRef] = []
        for c in columns:
            if len(columns_by_table[c.table_name]) > 1:
                # This table already contributes something beyond `c` --
                # a real, needed table, never a redundant duplicate.
                rewritten.append(c)
                continue

            replacement_table = next(
                (
                    other_table
                    for other_table in columns_by_table
                    if other_table != c.table_name
                    and c.column_name.upper() in catalog_by_table.get(other_table, {})
                ),
                None,
            )
            if replacement_table is None:
                rewritten.append(c)
                continue

            entry = catalog_by_table[replacement_table][c.column_name.upper()]
            rewritten.append(
                ResolvedColumnRef(
                    term=c.term,
                    catalog_column_id=entry.catalog_column_id,
                    table_name=entry.table_name,
                    schema_name=entry.schema_name,
                    column_name=entry.column_name,
                    data_type=entry.data_type,
                    role=c.role,
                )
            )

        seen_ids: set[str] = set()
        deduped: list[ResolvedColumnRef] = []
        for c in rewritten:
            if c.catalog_column_id in seen_ids:
                # The rewrite pointed two different terms at the same real
                # column -- keep only the first.
                continue
            seen_ids.add(c.catalog_column_id)
            deduped.append(c)
        return deduped

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

        SECOND REAL BUG, found live via a compound question spanning 4
        tables ("...is it concentrated in a few securities or accounts?",
        resolving `CUSTOMER_MARKET_AGG`/`STAGING_ASSET_INFORMATION`/
        `STAGING_CUSTOMER_INFORMATION`/`STAGING_MARKETS` together): this
        loop used to emit a join between `real_realizing_table` and EVERY
        other resolved table, unconditionally, using `subject_key_column`
        on both sides -- with 3+ resolved tables that don't all share the
        same natural key (e.g. `STAGING_CUSTOMER_INFORMATION` has no
        `MARKETID` column at all), this produces a `JoinSpec` referencing a
        column that does not exist in that table, which would fail loudly
        at Snowflake execution time rather than ever building a query that
        makes sense. Fixed by cross-checking `payload.catalog_inventory`
        (the real, live catalog listing Metadata Discovery already
        produced) before emitting each join: `other_table` is only joined
        via `subject_key_column` if that table genuinely has a column by
        that name. A table that doesn't share the key with any curated
        relationship simply stays unjoined -- which the caller
        (`_generate_statements` in `sql_generation`) now correctly reports
        as a real `unjoined_table_in_multi_table_query` error instead of
        ever seeing invalid SQL.

        THIRD REAL BUG, found live via "What is driving the high
        transaction volume in Athens Exchange -- is it concentrated in a
        few securities or accounts?": even with the first two fixes above,
        resolving `STAGING_TRANSACTIONS`/`STAGING_ASSET_INFORMATION`/
        `STAGING_MARKETS` together produced a real, live, WRONG answer --
        every security in a market showed the IDENTICAL total units, e.g.
        "Athens Exchange S.A. Cash Market" repeating `914679074.6164` for
        every one of ~80 distinct securities. Root cause: "Transaction
        happens in Market" (`realizing_table=TRANSACTIONS`,
        `subject_key_column=MARKETID`) connects `TRANSACTIONS` to EVERY
        other resolved table that happens to have a `MARKETID` column --
        and `STAGING_ASSET_INFORMATION` genuinely has one (an asset is
        listed on a market), so this loop joined `TRANSACTIONS` to
        `ASSET_INFORMATION` via `MARKETID` too. That is NOT the same
        relationship as "this transaction is FOR this asset" (the real FK
        for that is `ISIN`, not `MARKETID`) -- joining on `MARKETID`
        instead fans every asset in a market out against every
        transaction in that same market, then the `GROUP BY` on `ISIN`
        just repeats that market's grand total for every security in it.
        This is a real, PRE-EXISTING gap in "Transaction happens in
        Market" (live since Phase 9, item 15) -- it was never wrong for a
        real 2-table (Transaction+Market) question, only once a THIRD
        table sharing the exact same column name entered the resolved
        set. Fixed by requiring the shared key to be UNAMBIGUOUS: a
        relationship only connects `realizing_table` to `other_table` when
        `other_table` is the ONLY OTHER resolved table with a column named
        `subject_key_column` -- if 2+ resolved tables share that column
        name, which one is the relationship's real, intended object cannot
        be determined from the data available here, so NONE of them are
        joined via this relationship (they surface, correctly, as
        `unjoined_table_in_multi_table_query` rather than a confident but
        wrong per-group breakdown). A real `RelationshipConcept` connecting
        `TRANSACTIONS` and `ASSET_INFORMATION` via the correct `ISIN` key
        is a separate, deliberate addition, not a side effect of this
        safety fix.
        """

        def _core_name(table_name: str) -> str:
            return table_name.upper().removeprefix("STAGING_")

        resolved_tables = {column.table_name for column in columns}
        core_to_real = {_core_name(table): table for table in resolved_tables}

        table_columns: dict[str, set[str]] = {}
        for entry in payload.catalog_inventory:
            table_columns.setdefault(entry.table_name.upper(), set()).add(
                entry.column_name.upper()
            )

        def _table_has_column(table_name: str, column_name: str) -> bool:
            return column_name.upper() in table_columns.get(table_name.upper(), set())

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
            if not _table_has_column(real_realizing_table, rel.subject_key_column):
                # Curated seed data disagrees with the real, live catalog --
                # never trust the seed over the real schema.
                continue

            other_tables = {
                table for table in resolved_tables if table != real_realizing_table
            }

            candidates = [
                table
                for table in sorted(other_tables)
                if _table_has_column(table, rel.subject_key_column)
            ]
            if len(candidates) != 1:
                # Zero candidates: this table's key genuinely isn't shared
                # by anything else resolved -- nothing to join. Two or
                # more: the shared column name is ambiguous -- e.g. both
                # `STAGING_MARKETS` and `STAGING_ASSET_INFORMATION` have a
                # real `MARKETID` column, but only one of them is what
                # `realizing_table`'s relationship is actually about; a
                # third, incidentally-same-named column is NOT the same
                # relationship. Guessing which one is right would risk
                # exactly the live, wrong-data fan-out bug this guard
                # exists to prevent -- so neither is joined via this
                # relationship, leaving them to surface as a real,
                # explicit `unjoined_table_in_multi_table_query` error
                # instead.
                continue

            other_table = candidates[0]

            join_key = (
                other_table,
                rel.subject_key_column,
                real_realizing_table,
                rel.subject_key_column,
            )
            if join_key not in seen_joins:
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
