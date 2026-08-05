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
            columns = self._merge_staging_schema_duplicate_tables(
                columns, payload.catalog_inventory
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
    def _merge_staging_schema_duplicate_tables(
        columns: list[ResolvedColumnRef],
        catalog_inventory: list[CatalogInventoryEntry],
    ) -> list[ResolvedColumnRef]:
        """Merge two resolved tables that are really the SAME real
        Snowflake table crawled under two different catalog registrations
        -- e.g. `CUSTOMER_INFORMATION` and `STAGING_CUSTOMER_INFORMATION`
        (see LIMITATIONS.md item 14: every real business-glossary mapping
        anchors to the `STAGING_`-prefixed copy, but Semantic Retrieval's
        LLM fallback can freely resolve a term to the bare copy instead).

        REAL BUG, found live (golden-set `gq_009`, "How has the customer
        base's risk profile changed over time?"): "risk level" resolved
        via Ontology's glossary to `STAGING_CUSTOMER_INFORMATION.RISKLEVEL`,
        while "customer"/"trend" resolved via Semantic Retrieval's LLM to
        `CUSTOMER_INFORMATION.CUSTOMERID`/`.TIMESTAMP` -- two DIFFERENT
        column names each, so `_collapse_redundant_key_only_tables` (which
        only ever collapses an IDENTICALLY-named duplicate key) correctly
        left both tables in place, and the question failed with
        `unjoined_table_in_multi_table_query` even though both tables are
        the literal same underlying data.

        Unlike an incidental same-named-column coincidence, `STAGING_X`
        and `X` being the same real table is an established, confirmed
        structural fact about this specific dataset (item 14), not a
        guess -- so whenever a resolved `STAGING_`-prefixed table and its
        bare counterpart are BOTH present among the resolved tables, every
        column resolved from the bare table is redirected to the
        `STAGING_`-prefixed one (the convention every other curated
        resolution already anchors to), verified against the real catalog
        inventory for that exact column name -- a table pair sharing this
        exact core name but where the target genuinely lacks that column
        name is left untouched rather than guessed at.
        """

        if len(columns) < 2:
            return columns

        resolved_tables = {c.table_name for c in columns}
        if len(resolved_tables) < 2:
            return columns

        def _core_name(table_name: str) -> str:
            return table_name.upper().removeprefix("STAGING_")

        staging_by_core: dict[str, str] = {}
        bare_by_core: dict[str, str] = {}
        for table in resolved_tables:
            core = _core_name(table)
            if table.upper().startswith("STAGING_"):
                staging_by_core[core] = table
            else:
                bare_by_core[core] = table

        duplicate_pairs = {
            core: (bare_by_core[core], staging_by_core[core])
            for core in bare_by_core
            if core in staging_by_core
        }
        if not duplicate_pairs:
            return columns

        catalog_by_table: dict[str, set[str]] = {}
        for entry in catalog_inventory:
            catalog_by_table.setdefault(entry.table_name, set()).add(
                entry.column_name.upper()
            )
        catalog_entry_by_table_column: dict[tuple[str, str], CatalogInventoryEntry] = {
            (entry.table_name, entry.column_name.upper()): entry for entry in catalog_inventory
        }

        bare_to_staging = {bare: staging for bare, staging in duplicate_pairs.values()}

        merged: list[ResolvedColumnRef] = []
        for c in columns:
            canonical_table = bare_to_staging.get(c.table_name)
            if canonical_table is None:
                merged.append(c)
                continue

            if c.column_name.upper() not in catalog_by_table.get(canonical_table, set()):
                # The canonical copy genuinely doesn't have this column --
                # don't guess, leave it pointed at the bare table.
                merged.append(c)
                continue

            entry = catalog_entry_by_table_column[(canonical_table, c.column_name.upper())]
            merged.append(
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
        for c in merged:
            if c.catalog_column_id in seen_ids:
                continue
            seen_ids.add(c.catalog_column_id)
            deduped.append(c)
        return deduped

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

        FIFTH REAL BUG, found live via "What is the average closing price
        for assets on Euronext - Growth Paris?": `CLOSE_PRICES` and
        `MARKETS` both resolve directly (closing price, market name), but
        no term ever resolves anything from `ASSET_INFORMATION` -- yet the
        real join path is CLOSE_PRICES --[ISIN]--> ASSET_INFORMATION
        --[MARKETID]--> MARKETS, a genuine 2-hop bridge through a table
        that contributes zero selected columns of its own. Pass 1 above
        only ever considers a relationship whose `realizing_table` is
        ALREADY one of the resolved tables, so "Asset traded in Market"
        (realizing_table=ASSET_INFORMATION) was silently skipped even
        though Ontology correctly returned it as relevant, and the
        question failed with `unjoined_table_in_multi_table_query` despite
        the exact relationship data needed already being present.

        A fully general graph-based multi-hop solver was deliberately
        rejected here: broadening the "other_table" candidate pool to
        every relationship's realizing_table (regardless of whether that
        relationship ends up used) re-introduces exactly the kind of
        coincidental-shared-key-name ambiguity the FOURTH REAL BUG's guard
        exists to prevent -- e.g. `LIMIT_PRICES` also shares `ISIN` with
        `CLOSE_PRICES` purely because both are asset-keyed tables, which
        would make "Asset has ClosingPrice"'s bridge search spuriously
        ambiguous. Pass 1.5 below instead requires the bridge to prove
        itself via its OWN, separate relationship: a candidate bridge
        table only counts if some OTHER relationship's realizing_table (a)
        genuinely carries the key the stuck relationship needs AND (b)
        independently and unambiguously reaches a SECOND, different
        resolved table via its own key. `LIMIT_PRICES` fails part (b) --
        it has no relationship connecting it to `MARKETS` at all -- so it
        is correctly excluded without any dataset-specific special-casing.
        Bounded to exactly one bridge hop (two joins), consistent with
        every other guard in this method: prefer a safe, explicit failure
        (`unjoined_table_in_multi_table_query`) over guessing a longer or
        ambiguous chain.

        SIXTH REAL BUG, found live during THIS fix's own regression check:
        "What is the total transaction volume by market?" (the session's
        flagship worked example) already connects `TRANSACTIONS` to
        `MARKETS` directly via `"Transaction happens in Market"` -- but
        `"Transaction involves Asset"` (also realizing_table
        `TRANSACTIONS`, key `ISIN`) independently has zero direct
        candidates (`MARKETS` has no `ISIN` column), so Pass 1.5's first
        version bridged it anyway via `ASSET_INFORMATION`, adding a real
        but completely unnecessary extra `JOIN STAGING_ASSET_INFORMATION
        ON ISIN` to already-correct SQL. An unnecessary `INNER JOIN` like
        this is a real, silent risk (dropping any `TRANSACTIONS` row whose
        `ISIN` doesn't match a real asset, or fanning out if that key were
        ever non-unique on the bridge side) even when it happens not to
        change this particular result. Fixed by computing Pass 1's own
        resolved-table connectivity graph BEFORE Pass 1.5 runs, and only
        considering a bridge when it would connect two tables not already
        reachable from each other via Pass 1 alone -- a bridge is for
        filling a genuine gap, never an unnecessary alternate path to
        somewhere already reachable.
        """

        def _core_name(table_name: str) -> str:
            return table_name.upper().removeprefix("STAGING_")

        resolved_tables = {column.table_name for column in columns}
        core_to_real = {_core_name(table): table for table in resolved_tables}

        table_columns: dict[str, set[str]] = {}
        table_schema: dict[str, str] = {}
        real_tables_by_core: dict[str, list[str]] = {}
        for entry in payload.catalog_inventory:
            table_columns.setdefault(entry.table_name.upper(), set()).add(
                entry.column_name.upper()
            )
            table_schema.setdefault(entry.table_name, entry.schema_name)
            core = _core_name(entry.table_name)
            if entry.table_name not in real_tables_by_core.setdefault(core, []):
                real_tables_by_core[core].append(entry.table_name)

        def _table_has_column(table_name: str, column_name: str) -> bool:
            return column_name.upper() in table_columns.get(table_name.upper(), set())

        def _resolve_bridge_table(name: str) -> str | None:
            """Resolve a `RelationshipConcept.realizing_table` name to the
            one real catalog table it refers to, for use as a join-only
            bridge (no columns are ever selected from it). Prefers an
            already-resolved table for that core name if one exists;
            otherwise the single real catalog variant if there is exactly
            one. Two or more real variants with neither already resolved
            are, in this catalog, always the bare/`STAGING_`-prefixed
            duplicate pair for the same real table (item 14) -- since a
            bridge contributes no selected column, either registration's
            join-key VALUES are identical, so the `STAGING_`-prefixed one
            is picked deterministically (matching this codebase's
            established STAGING_-is-canonical convention) rather than
            refused as ambiguous."""

            core = _core_name(name)
            if core in core_to_real:
                return core_to_real[core]
            variants = real_tables_by_core.get(core, [])
            if not variants:
                return None
            if len(variants) == 1:
                return variants[0]
            staging_variant = next(
                (v for v in variants if v.upper().startswith("STAGING_")), None
            )
            return staging_variant if staging_variant is not None else variants[0]

        # Pass 1: collect every relationship that independently passes the
        # existing per-relationship checks below. Each survivor is a
        # candidate `JoinSpec`, grouped by the unordered (other_table,
        # realizing_table) pair it would connect.
        candidate_joins: list[tuple[JoinSpec, frozenset[str]]] = []
        key_columns_by_pair: dict[frozenset[str], set[str]] = {}

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
            pair_key = frozenset({other_table, real_realizing_table})
            key_columns_by_pair.setdefault(pair_key, set()).add(rel.subject_key_column)
            candidate_joins.append(
                (
                    JoinSpec(
                        left_table=other_table,
                        left_column=rel.subject_key_column,
                        right_table=real_realizing_table,
                        right_column=rel.subject_key_column,
                        relationship_concept=(
                            f"{rel.subject_label} {rel.predicate} {rel.object_label}"
                        ),
                    ),
                    pair_key,
                )
            )

        # SIXTH REAL BUG, found live during this fix's own regression
        # check: "What is the total transaction volume by market?" (the
        # session's flagship worked example) already connects TRANSACTIONS
        # to MARKETS directly via "Transaction happens in Market" -- but
        # "Transaction involves Asset" (also realizing_table=TRANSACTIONS,
        # key=ISIN) independently has zero DIRECT candidates (MARKETS has
        # no ISIN column), so Pass 1.5's first version bridged it anyway
        # via ASSET_INFORMATION, adding a real but completely unnecessary
        # extra `JOIN STAGING_ASSET_INFORMATION ON ISIN` to already-correct
        # SQL -- a silent row-dropping/fan-out risk for no reason, since
        # TRANSACTIONS and MARKETS were already connected. A bridge must
        # only be considered when it would connect two tables NOT already
        # reachable from each other via Pass 1's own edges -- computed
        # once, from a snapshot of Pass 1's candidate pairs, before Pass
        # 1.5 runs.
        def _reachable(start: str, adjacency: dict[str, set[str]]) -> set[str]:
            seen = {start}
            queue = [start]
            while queue:
                node = queue.pop()
                for neighbor in adjacency.get(node, set()):
                    if neighbor not in seen:
                        seen.add(neighbor)
                        queue.append(neighbor)
            return seen

        pass1_adjacency: dict[str, set[str]] = {}
        for _, pair_key in candidate_joins:
            left, right = tuple(pair_key)
            pass1_adjacency.setdefault(left, set()).add(right)
            pass1_adjacency.setdefault(right, set()).add(left)

        # Pass 1.5: FIFTH REAL BUG (see this method's docstring for the
        # full Euronext worked example). For a relationship whose
        # realizing_table IS already resolved but found ZERO direct
        # candidates in Pass 1 above (its key isn't shared by any other
        # resolved table), look for a 2-hop bridge: another relationship
        # `rel2` whose OWN realizing_table (resolved via
        # `_resolve_bridge_table`, since a bridge is by definition never
        # independently resolved) both (a) genuinely carries `rel`'s key
        # column, and (b) independently and unambiguously reaches a
        # SECOND, distinct resolved table via `rel2`'s own key -- one that
        # is NOT already reachable from `rel`'s realizing_table via Pass
        # 1's own edges (see SIXTH REAL BUG above). Only fires when
        # exactly one such bridge resolution exists across every candidate
        # `rel2` -- multiple distinct candidates are a real ambiguity,
        # left unresolved rather than guessed.
        for rel in payload.relationship_resolutions:
            real_realizing_table = core_to_real.get(_core_name(rel.realizing_table))
            if real_realizing_table is None:
                continue
            if not _table_has_column(real_realizing_table, rel.subject_key_column):
                continue

            other_tables = {
                table for table in resolved_tables if table != real_realizing_table
            }
            direct_candidates = [
                table for table in other_tables if _table_has_column(table, rel.subject_key_column)
            ]
            if direct_candidates:
                # Pass 1 either already joined this (exactly one candidate)
                # or correctly rejected it as ambiguous (2+ candidates) --
                # a bridge search is only for the "nothing shares this key
                # at all" case.
                continue

            bridge_matches: set[tuple[str, str, str]] = set()
            for rel2 in payload.relationship_resolutions:
                if rel2 is rel:
                    continue
                bridge_table = _resolve_bridge_table(rel2.realizing_table)
                if bridge_table is None or bridge_table == real_realizing_table:
                    continue
                if not _table_has_column(bridge_table, rel.subject_key_column):
                    continue
                if not _table_has_column(bridge_table, rel2.subject_key_column):
                    # Curated seed data disagrees with the real catalog for
                    # rel2's own key -- never trust the seed over the real
                    # schema, same discipline as Pass 1.
                    continue

                second_candidates = [
                    table
                    for table in resolved_tables
                    if table != real_realizing_table
                    and _table_has_column(table, rel2.subject_key_column)
                ]
                if len(second_candidates) != 1:
                    continue
                second_table = second_candidates[0]
                if second_table in _reachable(real_realizing_table, pass1_adjacency):
                    # SIXTH REAL BUG (see above): `real_realizing_table` is
                    # already connected to `second_table` via Pass 1 --
                    # directly or transitively through other resolved
                    # tables -- so a bridge here would be a real but
                    # unnecessary extra join, not something this question
                    # actually needs.
                    continue

                bridge_matches.add((bridge_table, rel2.subject_key_column, second_table))

            if len(bridge_matches) != 1:
                # Zero: no relationship in this question's own
                # relationship_resolutions bridges the gap -- leave it
                # unjoined. Two or more: which bridge is actually intended
                # can't be determined -- same "never guess" discipline as
                # every other guard in this method.
                continue

            bridge_table, bridge_key, second_table = next(iter(bridge_matches))

            hop1_pair = frozenset({real_realizing_table, bridge_table})
            key_columns_by_pair.setdefault(hop1_pair, set()).add(rel.subject_key_column)
            candidate_joins.append(
                (
                    JoinSpec(
                        left_table=real_realizing_table,
                        left_column=rel.subject_key_column,
                        right_table=bridge_table,
                        right_column=rel.subject_key_column,
                        relationship_concept=(
                            f"{rel.subject_label} {rel.predicate} {rel.object_label} (bridge)"
                        ),
                    ),
                    hop1_pair,
                )
            )

            hop2_pair = frozenset({bridge_table, second_table})
            key_columns_by_pair.setdefault(hop2_pair, set()).add(bridge_key)
            candidate_joins.append(
                (
                    JoinSpec(
                        left_table=second_table,
                        left_column=bridge_key,
                        right_table=bridge_table,
                        right_column=bridge_key,
                        relationship_concept=f"bridge via {bridge_table}",
                    ),
                    hop2_pair,
                )
            )

        # Pass 2: FOURTH REAL BUG, found live (item 91's implied-table
        # relaxation made this reachable): once a fact table like
        # `TRANSACTIONS` is implied by ANY resolved measure, EVERY
        # relationship concept realized by it fires unconditionally (per
        # `understanding.ontology.agent._resolve_relationships`'s own
        # documented design) -- including ones utterly irrelevant to the
        # actual question. For "total transaction value for the Technology
        # sector", BOTH "Transaction happens in Market" (key `MARKETID`)
        # and "Transaction involves Asset" (key `ISIN`) fired and both
        # independently found `ASSET_INFORMATION` as their sole candidate
        # (it has both columns, for unrelated reasons) -- Pass 1 above
        # can't tell these apart, since each relationship is checked in
        # isolation. Without this guard, whichever proposal happened to be
        # deduped in first silently won: live-verified, the resulting SQL
        # joined `TRANSACTIONS` to `ASSET_INFORMATION` via `MARKETID` (every
        # transaction fanned out against every Technology asset sharing its
        # market -- confirmed independently: real total `$44,664,559.45` via
        # the correct `ISIN` join vs. the pipeline's actual
        # `$22,818,053,245.26`, a >500x inflation from the fan-out). This
        # pass detects exactly that: when the SAME (other_table,
        # realizing_table) pair was proposed via 2+ DIFFERENT key columns
        # by different relationship concepts, which one is actually
        # relevant to this specific question cannot be determined here --
        # guessing either risks silently wrong data, so BOTH are dropped,
        # surfacing as a real, explicit `unjoined_table_in_multi_table_query`
        # instead. A pair proposed via only ONE distinct key column (the
        # common, safe case -- e.g. e-commerce's uniquely-keyed dimension
        # tables never produce a second candidate column for the same
        # table pair) is unaffected.
        joins: list[JoinSpec] = []
        seen_joins: set[tuple[str, str, str, str]] = set()

        for join_spec, pair_key in candidate_joins:
            if len(key_columns_by_pair[pair_key]) != 1:
                continue

            join_key = (
                join_spec.left_table,
                join_spec.left_column,
                join_spec.right_table,
                join_spec.right_column,
            )
            if join_key not in seen_joins:
                seen_joins.add(join_key)
                joins.append(join_spec)

        # Populate schema for every emitted join directly from the real
        # catalog inventory, rather than leaving SQL Generation to derive
        # it from `columns` alone -- a bridge table (Pass 1.5) contributes
        # no `ResolvedColumnRef`, so a `columns`-only derivation would have
        # nothing to find for it. See `JoinSpec.left_schema`'s docstring.
        return [
            join.model_copy(
                update={
                    "left_schema": table_schema.get(join.left_table),
                    "right_schema": table_schema.get(join.right_table),
                }
            )
            for join in joins
        ]


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    """Dedupe a list of strings while preserving first-seen order."""

    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
