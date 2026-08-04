"""Real unit tests for the Schema Mapping agent.

No mocking needed -- the agent is a pure function of its input, so these
are real end-to-end tests of `SchemaMappingAgent.run` against constructed
`SchemaMappingInput` payloads. `asyncio_mode = "auto"` is set at the
workspace root `packages/pyproject.toml`, so `async def test_...` functions
run without an explicit `@pytest.mark.asyncio` decorator.
"""

from __future__ import annotations

from navigraph_shared.contracts import RequestContext

from navigraph_agents.understanding.schema_mapping.agent import SchemaMappingAgent
from navigraph_agents.understanding.schema_mapping.contracts import (
    CatalogInventoryEntry,
    ConceptResolution,
    RelationshipResolution,
    SchemaMappingInput,
    SchemaMappingPayload,
    TermMatch,
)


def _request_context() -> RequestContext:
    return RequestContext(
        tenant_id="tenant-acme",
        user_id="user-1",
        trace_id="trace-1",
        roles=["analyst"],
    )


def _catalog_entry(
    catalog_column_id: str,
    table_name: str,
    column_name: str,
    data_type: str,
    schema_name: str = "PUBLIC",
) -> CatalogInventoryEntry:
    return CatalogInventoryEntry(
        catalog_column_id=catalog_column_id,
        table_name=table_name,
        schema_name=schema_name,
        column_name=column_name,
        data_type=data_type,
        nullable=True,
        business_name=None,
        synonyms=[],
        description=None,
    )


class TestAllResolvedSingleTable:
    async def test_single_table_all_terms_resolved(self) -> None:
        agent = SchemaMappingAgent()

        payload = SchemaMappingPayload(
            intent="metric_lookup",
            concept_resolutions=[
                ConceptResolution(
                    term="revenue",
                    resolved=True,
                    business_concept="Total Transaction Value",
                    catalog_column_id="col-1",
                    column_name="TOTALVALUE",
                    preferred=True,
                ),
            ],
            relationship_resolutions=[],
            semantic_matches=[],
            catalog_inventory=[
                _catalog_entry("col-1", "TRANSACTIONS", "TOTALVALUE", "NUMBER"),
            ],
        )
        input_ = SchemaMappingInput(request_context=_request_context(), payload=payload)

        output = await agent.run(input_)

        assert output.result.tables == ["TRANSACTIONS"]
        assert len(output.result.columns) == 1
        column = output.result.columns[0]
        assert column.term == "revenue"
        assert column.catalog_column_id == "col-1"
        assert column.table_name == "TRANSACTIONS"
        assert column.column_name == "TOTALVALUE"
        assert column.role == "measure"
        assert output.result.joins == []
        assert output.result.unmapped_terms == []
        assert output.confidence == 1.0
        assert output.errors == []
        assert len(output.lineage_events) == 1
        assert output.metadata.latency_ms >= 0
        assert output.metadata.model_version is None


class TestJoinAcrossTwoTables:
    async def test_join_emitted_when_relationship_spans_two_resolved_tables(self) -> None:
        agent = SchemaMappingAgent()

        payload = SchemaMappingPayload(
            intent="comparison",
            concept_resolutions=[
                ConceptResolution(
                    term="revenue",
                    resolved=True,
                    catalog_column_id="col-1",
                    column_name="TOTALVALUE",
                    preferred=True,
                ),
                ConceptResolution(
                    term="risk level",
                    resolved=True,
                    catalog_column_id="col-2",
                    column_name="RISKLEVEL",
                    preferred=True,
                ),
            ],
            relationship_resolutions=[
                RelationshipResolution(
                    subject_label="Customer",
                    predicate="HAS",
                    object_label="RiskLevel",
                    realizing_table="CUSTOMER_INFORMATION",
                    subject_key_column="CUSTOMERID",
                    object_key_column="RISKLEVEL",
                ),
            ],
            semantic_matches=[],
            catalog_inventory=[
                _catalog_entry("col-1", "TRANSACTIONS", "TOTALVALUE", "NUMBER"),
                # The join key (CUSTOMERID) isn't itself a resolved column
                # for this question, but a real catalog_inventory always
                # includes every real column of every table Metadata
                # Discovery crawled -- _build_joins now cross-checks the
                # join key actually exists on both sides before emitting it.
                _catalog_entry("col-1b", "TRANSACTIONS", "CUSTOMERID", "TEXT"),
                _catalog_entry("col-2", "CUSTOMER_INFORMATION", "RISKLEVEL", "VARCHAR"),
                _catalog_entry("col-2b", "CUSTOMER_INFORMATION", "CUSTOMERID", "TEXT"),
            ],
        )
        input_ = SchemaMappingInput(request_context=_request_context(), payload=payload)

        output = await agent.run(input_)

        assert set(output.result.tables) == {"TRANSACTIONS", "CUSTOMER_INFORMATION"}
        assert len(output.result.joins) == 1
        join = output.result.joins[0]
        assert join.left_table == "TRANSACTIONS"
        assert join.left_column == "CUSTOMERID"
        assert join.right_table == "CUSTOMER_INFORMATION"
        assert join.right_column == "CUSTOMERID"
        assert join.relationship_concept == "Customer HAS RiskLevel"
        assert output.result.unmapped_terms == []

    async def test_join_emitted_when_resolved_tables_are_staging_prefixed(self) -> None:
        """REAL BUG, live-reproduced: `RELATIONSHIP_CONCEPTS`' `realizing_table`
        values are bare (e.g. "CUSTOMER_INFORMATION"), but every column
        resolved via Ontology's business-concept path -- the dominant, real
        path, since ALL real `SCHEMA_ENRICHMENT` glossary mappings point at
        `STAGING_`-prefixed tables (LIMITATIONS.md item 14) -- has a
        `table_name` of e.g. "STAGING_CUSTOMER_INFORMATION". The exact-string
        match used to silently produce zero joins for this, the MOST common
        real case, not just an edge case."""

        agent = SchemaMappingAgent()

        payload = SchemaMappingPayload(
            intent="comparison",
            concept_resolutions=[
                ConceptResolution(
                    term="revenue",
                    resolved=True,
                    catalog_column_id="col-1",
                    column_name="TOTALVALUE",
                    preferred=True,
                ),
                ConceptResolution(
                    term="risk level",
                    resolved=True,
                    catalog_column_id="col-2",
                    column_name="RISKLEVEL",
                    preferred=True,
                ),
            ],
            relationship_resolutions=[
                RelationshipResolution(
                    subject_label="Customer",
                    predicate="HAS",
                    object_label="RiskLevel",
                    realizing_table="CUSTOMER_INFORMATION",
                    subject_key_column="CUSTOMERID",
                    object_key_column="RISKLEVEL",
                ),
            ],
            semantic_matches=[],
            catalog_inventory=[
                _catalog_entry("col-1", "STAGING_TRANSACTIONS", "TOTALVALUE", "NUMBER"),
                _catalog_entry("col-1b", "STAGING_TRANSACTIONS", "CUSTOMERID", "TEXT"),
                _catalog_entry("col-2", "STAGING_CUSTOMER_INFORMATION", "RISKLEVEL", "VARCHAR"),
                _catalog_entry("col-2b", "STAGING_CUSTOMER_INFORMATION", "CUSTOMERID", "TEXT"),
            ],
        )
        input_ = SchemaMappingInput(request_context=_request_context(), payload=payload)

        output = await agent.run(input_)

        assert set(output.result.tables) == {"STAGING_TRANSACTIONS", "STAGING_CUSTOMER_INFORMATION"}
        assert len(output.result.joins) == 1
        join = output.result.joins[0]
        # The emitted join must use the REAL resolved (STAGING_-prefixed)
        # table names, never the bare RelationshipConcept literal -- SQL
        # Generation looks up each table's schema by this exact name.
        assert join.left_table == "STAGING_TRANSACTIONS"
        assert join.right_table == "STAGING_CUSTOMER_INFORMATION"
        assert join.left_column == "CUSTOMERID"
        assert join.right_column == "CUSTOMERID"

    async def test_third_table_lacking_the_join_key_is_not_joined(self) -> None:
        """REAL BUG, live-reproduced via a real compound question spanning
        4 tables (`CUSTOMER_MARKET_AGG`/`STAGING_ASSET_INFORMATION`/
        `STAGING_CUSTOMER_INFORMATION`/`STAGING_MARKETS`): this loop used to
        emit a join between `realizing_table` and EVERY other resolved
        table unconditionally, even when a table doesn't actually have the
        join key column at all (e.g. `STAGING_MARKETS` has no `CUSTOMERID`)
        -- that would have produced a real, broken SQL statement referencing
        a nonexistent column. A third, key-less table must now be left
        unjoined rather than joined on a column it doesn't have."""

        agent = SchemaMappingAgent()

        payload = SchemaMappingPayload(
            intent="comparison",
            concept_resolutions=[
                ConceptResolution(
                    term="revenue",
                    resolved=True,
                    catalog_column_id="col-1",
                    column_name="TOTALVALUE",
                    preferred=True,
                ),
                ConceptResolution(
                    term="risk level",
                    resolved=True,
                    catalog_column_id="col-2",
                    column_name="RISKLEVEL",
                    preferred=True,
                ),
                ConceptResolution(
                    term="market",
                    resolved=True,
                    catalog_column_id="col-3",
                    column_name="NAME",
                    preferred=True,
                ),
            ],
            relationship_resolutions=[
                RelationshipResolution(
                    subject_label="Customer",
                    predicate="HAS",
                    object_label="RiskLevel",
                    realizing_table="CUSTOMER_INFORMATION",
                    subject_key_column="CUSTOMERID",
                    object_key_column="RISKLEVEL",
                ),
            ],
            semantic_matches=[],
            catalog_inventory=[
                _catalog_entry("col-1", "TRANSACTIONS", "TOTALVALUE", "NUMBER"),
                _catalog_entry("col-1b", "TRANSACTIONS", "CUSTOMERID", "TEXT"),
                _catalog_entry("col-2", "CUSTOMER_INFORMATION", "RISKLEVEL", "VARCHAR"),
                _catalog_entry("col-2b", "CUSTOMER_INFORMATION", "CUSTOMERID", "TEXT"),
                # STAGING_MARKETS has no CUSTOMERID column at all -- a real,
                # live schema fact.
                _catalog_entry("col-3", "STAGING_MARKETS", "NAME", "TEXT"),
            ],
        )
        input_ = SchemaMappingInput(request_context=_request_context(), payload=payload)

        output = await agent.run(input_)

        assert set(output.result.tables) == {"TRANSACTIONS", "CUSTOMER_INFORMATION", "STAGING_MARKETS"}
        # Only the real, key-sharing pair is joined; STAGING_MARKETS is left
        # unjoined rather than being joined on a column it doesn't have.
        assert len(output.result.joins) == 1
        join = output.result.joins[0]
        assert {join.left_table, join.right_table} == {"TRANSACTIONS", "CUSTOMER_INFORMATION"}
        assert not any(
            "STAGING_MARKETS" in (j.left_table, j.right_table) for j in output.result.joins
        )

    async def test_ambiguous_shared_key_across_two_other_tables_joins_neither(self) -> None:
        """REAL BUG, live-reproduced: "What is driving the high transaction
        volume in Athens Exchange -- concentrated in a few securities or
        accounts?" resolved TRANSACTIONS/ASSET_INFORMATION/MARKETS
        together. "Transaction happens in Market" (realizing_table=
        TRANSACTIONS, key=MARKETID) used to connect TRANSACTIONS to EVERY
        other resolved table sharing a MARKETID column -- and
        ASSET_INFORMATION genuinely has one (an asset is listed on a
        market), so it got joined to TRANSACTIONS via MARKETID too. That
        is NOT the same relationship as "this transaction is for this
        asset" (the real FK is ISIN) -- it silently fanned every asset in
        a market out against every transaction in that market, repeating
        the market's grand total for every security in it. Since which of
        {ASSET_INFORMATION, MARKETS} is the relationship's REAL object
        can't be determined from a shared column name alone, neither may
        be joined via this relationship -- both must stay unjoined."""

        agent = SchemaMappingAgent()

        payload = SchemaMappingPayload(
            intent="metric_lookup",
            concept_resolutions=[
                ConceptResolution(
                    term="transaction volume",
                    resolved=True,
                    catalog_column_id="col-1",
                    column_name="UNITS",
                    preferred=True,
                ),
                ConceptResolution(
                    term="security",
                    resolved=True,
                    catalog_column_id="col-2",
                    column_name="ISIN",
                    preferred=True,
                ),
                ConceptResolution(
                    term="market",
                    resolved=True,
                    catalog_column_id="col-3",
                    column_name="NAME",
                    preferred=True,
                ),
            ],
            relationship_resolutions=[
                RelationshipResolution(
                    subject_label="Transaction",
                    predicate="HAPPENS_IN",
                    object_label="Market",
                    realizing_table="TRANSACTIONS",
                    subject_key_column="MARKETID",
                    object_key_column="MARKETID",
                ),
            ],
            semantic_matches=[],
            catalog_inventory=[
                _catalog_entry("col-1", "TRANSACTIONS", "UNITS", "NUMBER"),
                _catalog_entry("col-1b", "TRANSACTIONS", "MARKETID", "TEXT"),
                _catalog_entry("col-2", "ASSET_INFORMATION", "ISIN", "TEXT"),
                # ASSET_INFORMATION also has a real MARKETID column -- this
                # is what makes the shared key ambiguous.
                _catalog_entry("col-2b", "ASSET_INFORMATION", "MARKETID", "TEXT"),
                _catalog_entry("col-3", "MARKETS", "NAME", "TEXT"),
                _catalog_entry("col-3b", "MARKETS", "MARKETID", "TEXT"),
            ],
        )
        input_ = SchemaMappingInput(request_context=_request_context(), payload=payload)

        output = await agent.run(input_)

        assert set(output.result.tables) == {"TRANSACTIONS", "ASSET_INFORMATION", "MARKETS"}
        # The ambiguous MARKETID key connects nobody -- no wrong join, and
        # no partial-but-misleading join either.
        assert output.result.joins == []

    async def test_no_join_when_realizing_table_not_among_resolved_columns(self) -> None:
        agent = SchemaMappingAgent()

        payload = SchemaMappingPayload(
            intent="metric_lookup",
            concept_resolutions=[
                ConceptResolution(
                    term="revenue",
                    resolved=True,
                    catalog_column_id="col-1",
                    column_name="TOTALVALUE",
                    preferred=True,
                ),
            ],
            relationship_resolutions=[
                RelationshipResolution(
                    subject_label="Customer",
                    predicate="HAS",
                    object_label="RiskLevel",
                    realizing_table="CUSTOMER_INFORMATION",
                    subject_key_column="CUSTOMERID",
                    object_key_column="RISKLEVEL",
                ),
            ],
            semantic_matches=[],
            catalog_inventory=[
                _catalog_entry("col-1", "TRANSACTIONS", "TOTALVALUE", "NUMBER"),
            ],
        )
        input_ = SchemaMappingInput(request_context=_request_context(), payload=payload)

        output = await agent.run(input_)

        # realizing_table (CUSTOMER_INFORMATION) was never itself selected,
        # so there is nothing to join to -- no join should be emitted.
        assert output.result.joins == []
        assert output.result.tables == ["TRANSACTIONS"]

    async def test_no_self_join_when_only_realizing_table_present(self) -> None:
        agent = SchemaMappingAgent()

        payload = SchemaMappingPayload(
            intent="metric_lookup",
            concept_resolutions=[
                ConceptResolution(
                    term="risk level",
                    resolved=True,
                    catalog_column_id="col-2",
                    column_name="RISKLEVEL",
                    preferred=True,
                ),
            ],
            relationship_resolutions=[
                RelationshipResolution(
                    subject_label="Customer",
                    predicate="HAS",
                    object_label="RiskLevel",
                    realizing_table="CUSTOMER_INFORMATION",
                    subject_key_column="CUSTOMERID",
                    object_key_column="RISKLEVEL",
                ),
            ],
            semantic_matches=[],
            catalog_inventory=[
                _catalog_entry("col-2", "CUSTOMER_INFORMATION", "RISKLEVEL", "VARCHAR"),
            ],
        )
        input_ = SchemaMappingInput(request_context=_request_context(), payload=payload)

        output = await agent.run(input_)

        # Only one table is present at all -- no other table to join to,
        # so no (nonsensical, self-referential) join is emitted.
        assert output.result.joins == []


class TestUnmappedTerms:
    async def test_unresolved_term_not_rescued_by_semantic_match_is_unmapped(self) -> None:
        agent = SchemaMappingAgent()

        payload = SchemaMappingPayload(
            intent="metric_lookup",
            concept_resolutions=[
                ConceptResolution(term="gibberish_term", resolved=False),
            ],
            relationship_resolutions=[],
            semantic_matches=[],
            catalog_inventory=[],
        )
        input_ = SchemaMappingInput(request_context=_request_context(), payload=payload)

        output = await agent.run(input_)

        assert output.result.unmapped_terms == ["gibberish_term"]
        assert output.result.columns == []
        assert output.confidence == 0.5

    async def test_unresolved_term_rescued_by_semantic_match_is_not_unmapped(self) -> None:
        agent = SchemaMappingAgent()

        payload = SchemaMappingPayload(
            intent="metric_lookup",
            concept_resolutions=[
                ConceptResolution(term="revenue", resolved=False),
            ],
            relationship_resolutions=[],
            semantic_matches=[
                TermMatch(
                    term="revenue",
                    matched=True,
                    catalog_column_id="col-1",
                    table_name="TRANSACTIONS",
                    column_name="TOTALVALUE",
                ),
            ],
            catalog_inventory=[
                _catalog_entry("col-1", "TRANSACTIONS", "TOTALVALUE", "NUMBER"),
            ],
        )
        input_ = SchemaMappingInput(request_context=_request_context(), payload=payload)

        output = await agent.run(input_)

        assert output.result.unmapped_terms == []
        assert len(output.result.columns) == 1
        assert output.result.columns[0].term == "revenue"

    async def test_resolved_column_missing_from_inventory_is_unmapped_not_crashing(self) -> None:
        agent = SchemaMappingAgent()

        payload = SchemaMappingPayload(
            intent="metric_lookup",
            concept_resolutions=[
                ConceptResolution(
                    term="revenue",
                    resolved=True,
                    catalog_column_id="col-missing",
                    column_name="TOTALVALUE",
                    preferred=True,
                ),
            ],
            relationship_resolutions=[],
            semantic_matches=[],
            catalog_inventory=[],
        )
        input_ = SchemaMappingInput(request_context=_request_context(), payload=payload)

        # Must not raise.
        output = await agent.run(input_)

        assert output.result.columns == []
        assert output.result.unmapped_terms == ["revenue"]
        assert output.result.tables == []


class TestRoleAssignment:
    async def test_numeric_column_with_measure_intent_is_a_measure(self) -> None:
        agent = SchemaMappingAgent()

        for intent in ("metric_lookup", "comparison", "trend_analysis"):
            payload = SchemaMappingPayload(
                intent=intent,  # type: ignore[arg-type]
                concept_resolutions=[
                    ConceptResolution(
                        term="revenue",
                        resolved=True,
                        catalog_column_id="col-1",
                        column_name="TOTALVALUE",
                        preferred=True,
                    ),
                ],
                relationship_resolutions=[],
                semantic_matches=[],
                catalog_inventory=[
                    _catalog_entry("col-1", "TRANSACTIONS", "TOTALVALUE", "NUMBER"),
                ],
            )
            input_ = SchemaMappingInput(request_context=_request_context(), payload=payload)

            output = await agent.run(input_)

            assert output.result.columns[0].role == "measure", f"failed for intent={intent}"

    async def test_non_numeric_column_is_a_dimension_regardless_of_intent(self) -> None:
        agent = SchemaMappingAgent()

        payload = SchemaMappingPayload(
            intent="metric_lookup",
            concept_resolutions=[
                ConceptResolution(
                    term="channel",
                    resolved=True,
                    catalog_column_id="col-3",
                    column_name="CHANNEL",
                    preferred=True,
                ),
            ],
            relationship_resolutions=[],
            semantic_matches=[],
            catalog_inventory=[
                _catalog_entry("col-3", "TRANSACTIONS", "CHANNEL", "VARCHAR"),
            ],
        )
        input_ = SchemaMappingInput(request_context=_request_context(), payload=payload)

        output = await agent.run(input_)

        assert output.result.columns[0].role == "dimension"

    async def test_numeric_column_with_non_measure_intent_is_a_dimension(self) -> None:
        agent = SchemaMappingAgent()

        payload = SchemaMappingPayload(
            intent="unknown",
            concept_resolutions=[
                ConceptResolution(
                    term="revenue",
                    resolved=True,
                    catalog_column_id="col-1",
                    column_name="TOTALVALUE",
                    preferred=True,
                ),
            ],
            relationship_resolutions=[],
            semantic_matches=[],
            catalog_inventory=[
                _catalog_entry("col-1", "TRANSACTIONS", "TOTALVALUE", "NUMBER"),
            ],
        )
        input_ = SchemaMappingInput(request_context=_request_context(), payload=payload)

        output = await agent.run(input_)

        assert output.result.columns[0].role == "dimension"
