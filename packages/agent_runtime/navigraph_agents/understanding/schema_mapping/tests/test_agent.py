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


class TestMergeStagingSchemaDuplicateTables:
    async def test_bare_table_columns_redirect_to_the_staging_prefixed_duplicate(
        self,
    ) -> None:
        """REAL BUG, live-reproduced (golden-set gq_009: "How has the
        customer base's risk profile changed over time?"): "risk level"
        resolved via Ontology's glossary to
        `STAGING_CUSTOMER_INFORMATION.RISKLEVEL` (item 14's established
        anchor), while "customer"/"trend" resolved via Semantic
        Retrieval's LLM to `CUSTOMER_INFORMATION.CUSTOMERID`/`.TIMESTAMP`
        -- two DIFFERENT column names, so the redundant-key-only collapse
        alone can't merge them, even though `CUSTOMER_INFORMATION` and
        `STAGING_CUSTOMER_INFORMATION` are the literal same real table.
        Both bare-table columns must redirect to the STAGING_-prefixed
        table's own real copies, collapsing to one table with no join
        needed."""

        agent = SchemaMappingAgent()

        payload = SchemaMappingPayload(
            intent="trend_analysis",
            concept_resolutions=[
                ConceptResolution(
                    term="risk level",
                    resolved=True,
                    catalog_column_id="col-risklevel-staging",
                    column_name="RISKLEVEL",
                    preferred=True,
                ),
            ],
            relationship_resolutions=[],
            semantic_matches=[
                TermMatch(
                    term="customer",
                    matched=True,
                    catalog_column_id="col-customerid-bare",
                    table_name="CUSTOMER_INFORMATION",
                    column_name="CUSTOMERID",
                ),
                TermMatch(
                    term="trend",
                    matched=True,
                    catalog_column_id="col-timestamp-bare",
                    table_name="CUSTOMER_INFORMATION",
                    column_name="TIMESTAMP",
                ),
            ],
            catalog_inventory=[
                _catalog_entry(
                    "col-risklevel-staging", "STAGING_CUSTOMER_INFORMATION", "RISKLEVEL", "TEXT"
                ),
                _catalog_entry("col-customerid-bare", "CUSTOMER_INFORMATION", "CUSTOMERID", "TEXT"),
                _catalog_entry("col-timestamp-bare", "CUSTOMER_INFORMATION", "TIMESTAMP", "DATE"),
                # The STAGING_-prefixed table's own real copies of these
                # same columns -- present in the catalog even though
                # nothing explicitly resolved them as terms.
                _catalog_entry(
                    "col-customerid-staging", "STAGING_CUSTOMER_INFORMATION", "CUSTOMERID", "TEXT"
                ),
                _catalog_entry(
                    "col-timestamp-staging", "STAGING_CUSTOMER_INFORMATION", "TIMESTAMP", "DATE"
                ),
            ],
        )
        input_ = SchemaMappingInput(request_context=_request_context(), payload=payload)

        output = await agent.run(input_)

        assert output.result.tables == ["STAGING_CUSTOMER_INFORMATION"]
        assert output.result.joins == []
        assert {c.column_name for c in output.result.columns} == {
            "RISKLEVEL",
            "CUSTOMERID",
            "TIMESTAMP",
        }
        assert all(c.table_name == "STAGING_CUSTOMER_INFORMATION" for c in output.result.columns)
        customer_column = next(
            c for c in output.result.columns if c.column_name == "CUSTOMERID"
        )
        assert customer_column.catalog_column_id == "col-customerid-staging"


class TestCollapseRedundantKeyOnlyTables:
    async def test_redundant_customer_id_from_a_second_table_collapses_to_one_table(
        self,
    ) -> None:
        """REAL BUG, live-reproduced (golden-set gq_002: "How many
        transactions has each customer made?"): Semantic Retrieval's real
        LLM call is non-deterministic and can resolve "customer" to
        `CUSTOMER_INFORMATION.CUSTOMERID` instead of the anchor table's own
        `STAGING_TRANSACTIONS.CUSTOMERID` -- both are real, valid columns,
        so the usual dedupe-by-`catalog_column_id` can't collapse them,
        and the question ends up needlessly requiring an unresolvable
        join. Since `CUSTOMER_INFORMATION` contributes nothing beyond that
        one redundant key, and `STAGING_TRANSACTIONS` (already anchored by
        the "transactions" resolution) genuinely has its own real
        `CUSTOMERID` column, the resolution must redirect there instead --
        collapsing to a single table with no join needed at all."""

        agent = SchemaMappingAgent()

        payload = SchemaMappingPayload(
            intent="metric_lookup",
            concept_resolutions=[],
            relationship_resolutions=[],
            semantic_matches=[
                TermMatch(
                    term="transactions",
                    matched=True,
                    catalog_column_id="col-txn-id",
                    table_name="STAGING_TRANSACTIONS",
                    column_name="TRANSACTIONID",
                ),
                TermMatch(
                    term="customer",
                    matched=True,
                    catalog_column_id="col-cust-id-wrong-table",
                    table_name="CUSTOMER_INFORMATION",
                    column_name="CUSTOMERID",
                ),
            ],
            catalog_inventory=[
                _catalog_entry("col-txn-id", "STAGING_TRANSACTIONS", "TRANSACTIONID", "NUMBER"),
                _catalog_entry("col-cust-id-wrong-table", "CUSTOMER_INFORMATION", "CUSTOMERID", "TEXT"),
                # The real column Semantic Retrieval SHOULD have picked --
                # present in the catalog even though nothing explicitly
                # resolved it as a term.
                _catalog_entry("col-cust-id-real", "STAGING_TRANSACTIONS", "CUSTOMERID", "TEXT"),
            ],
        )
        input_ = SchemaMappingInput(request_context=_request_context(), payload=payload)

        output = await agent.run(input_)

        assert output.result.tables == ["STAGING_TRANSACTIONS"]
        assert output.result.joins == []
        assert {c.column_name for c in output.result.columns} == {"TRANSACTIONID", "CUSTOMERID"}
        assert all(c.table_name == "STAGING_TRANSACTIONS" for c in output.result.columns)
        customer_column = next(
            c for c in output.result.columns if c.column_name == "CUSTOMERID"
        )
        assert customer_column.catalog_column_id == "col-cust-id-real"

    async def test_genuinely_needed_second_table_is_never_collapsed(self) -> None:
        """A real, needed attribute (RISKLEVEL, which no other resolved
        table has) must never be collapsed away -- the redundant-key
        collapse only ever touches a table whose ENTIRE contribution is a
        single, duplicated key column."""

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
                _catalog_entry("col-1b", "TRANSACTIONS", "CUSTOMERID", "TEXT"),
                _catalog_entry("col-2", "CUSTOMER_INFORMATION", "RISKLEVEL", "VARCHAR"),
                _catalog_entry("col-2b", "CUSTOMER_INFORMATION", "CUSTOMERID", "TEXT"),
            ],
        )
        input_ = SchemaMappingInput(request_context=_request_context(), payload=payload)

        output = await agent.run(input_)

        assert set(output.result.tables) == {"TRANSACTIONS", "CUSTOMER_INFORMATION"}
        assert len(output.result.joins) == 1


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

    async def test_two_different_relationships_proposing_the_same_table_pair_via_different_keys_join_neither(
        self,
    ) -> None:
        """FOURTH REAL BUG, live-reproduced: "What is the total transaction
        value for the Technology sector?" resolved TRANSACTIONS (via
        "transaction value") and ASSET_INFORMATION (via "sector") only --
        no MARKETS table this time. Once item 91's implied-table relaxation
        let `TRANSACTIONS` fire EVERY relationship it realizes
        unconditionally, BOTH "Transaction happens in Market"
        (key=MARKETID) and "Transaction involves Asset" (key=ISIN)
        independently found `ASSET_INFORMATION` as their sole candidate
        (it genuinely has both columns, for unrelated reasons) -- each
        passes the single-relationship ambiguity guard above on its own.
        Live-verified this produced a real, silently WRONG answer:
        joining via MARKETID fanned every transaction out against every
        Technology asset sharing its market, inflating the true total
        (independently confirmed via a correct ISIN join) from
        $44,664,559.45 to $22,818,053,245.26 -- over 500x too large. Since
        which relationship is actually relevant to THIS question can't be
        determined from either proposal alone, neither may be joined."""

        agent = SchemaMappingAgent()

        payload = SchemaMappingPayload(
            intent="metric_lookup",
            concept_resolutions=[
                ConceptResolution(
                    term="transaction value",
                    resolved=True,
                    catalog_column_id="col-1",
                    column_name="TOTALVALUE",
                    preferred=True,
                ),
                ConceptResolution(
                    term="sector",
                    resolved=True,
                    catalog_column_id="col-2",
                    column_name="SECTOR",
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
                RelationshipResolution(
                    subject_label="Transaction",
                    predicate="INVOLVES",
                    object_label="Asset",
                    realizing_table="TRANSACTIONS",
                    subject_key_column="ISIN",
                    object_key_column="ISIN",
                ),
            ],
            semantic_matches=[],
            catalog_inventory=[
                _catalog_entry("col-1", "TRANSACTIONS", "TOTALVALUE", "NUMBER"),
                _catalog_entry("col-1b", "TRANSACTIONS", "MARKETID", "TEXT"),
                _catalog_entry("col-1c", "TRANSACTIONS", "ISIN", "TEXT"),
                _catalog_entry("col-2", "ASSET_INFORMATION", "SECTOR", "TEXT"),
                _catalog_entry("col-2b", "ASSET_INFORMATION", "MARKETID", "TEXT"),
                _catalog_entry("col-2c", "ASSET_INFORMATION", "ISIN", "TEXT"),
            ],
        )
        input_ = SchemaMappingInput(request_context=_request_context(), payload=payload)

        output = await agent.run(input_)

        assert set(output.result.tables) == {"TRANSACTIONS", "ASSET_INFORMATION"}
        # Two different relationships proposed the SAME table pair via
        # DIFFERENT keys (MARKETID vs. ISIN) -- neither is trustworthy, so
        # no join is emitted, rather than silently picking one and risking
        # the exact wrong-data fan-out this guard exists to prevent.
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


class TestBridgeTableJoin:
    async def test_two_hop_bridge_through_unresolved_asset_information(self) -> None:
        """FIFTH REAL BUG, live-reproduced: "What is the average closing
        price for assets on Euronext - Growth Paris?" resolves only
        `CLOSE_PRICES` (closing price) and `MARKETS` (market name) --
        `ASSET_INFORMATION` is never independently resolved (no term needs
        any of its own columns). Ontology still returns three relevant
        relationships: "Asset traded in Market" (realizing_table
        ASSET_INFORMATION/STAGING_ASSET_INFORMATION -- both catalog
        registrations present, exercising the bridge/STAGING_-preference
        path too), "Asset has ClosingPrice" (realizing_table CLOSE_PRICES,
        key ISIN), and "Asset has LimitPrice" (realizing_table
        LIMIT_PRICES, key ISIN -- a same-key coincidental collision that
        must NOT be mistaken for the real bridge, since LIMIT_PRICES has
        no relationship reaching MARKETS at all). The real join path is
        CLOSE_PRICES --[ISIN]--> ASSET_INFORMATION --[MARKETID]--> MARKETS."""

        agent = SchemaMappingAgent()

        payload = SchemaMappingPayload(
            intent="metric_lookup",
            concept_resolutions=[
                ConceptResolution(
                    term="average closing price",
                    resolved=True,
                    catalog_column_id="col-close",
                    column_name="CLOSEPRICE",
                    preferred=True,
                ),
                ConceptResolution(
                    term="Euronext - Growth Paris",
                    resolved=True,
                    catalog_column_id="col-market",
                    column_name="NAME",
                    preferred=True,
                ),
            ],
            relationship_resolutions=[
                RelationshipResolution(
                    subject_label="Asset",
                    predicate="TRADED_IN",
                    object_label="Market",
                    realizing_table="ASSET_INFORMATION",
                    subject_key_column="MARKETID",
                    object_key_column="MARKETID",
                ),
                RelationshipResolution(
                    subject_label="Asset",
                    predicate="HAS_CLOSING_PRICE",
                    object_label="Price",
                    realizing_table="CLOSE_PRICES",
                    subject_key_column="ISIN",
                    object_key_column="ISIN",
                ),
                RelationshipResolution(
                    subject_label="Asset",
                    predicate="HAS_LIMIT_PRICE",
                    object_label="Price",
                    realizing_table="LIMIT_PRICES",
                    subject_key_column="ISIN",
                    object_key_column="ISIN",
                ),
            ],
            semantic_matches=[],
            catalog_inventory=[
                _catalog_entry("col-close", "CLOSE_PRICES", "CLOSEPRICE", "NUMBER", "FAR_TRANS"),
                _catalog_entry("col-close-isin", "CLOSE_PRICES", "ISIN", "TEXT", "FAR_TRANS"),
                _catalog_entry("col-market", "MARKETS", "NAME", "TEXT", "FAR_TRANS"),
                _catalog_entry("col-market-id", "MARKETS", "MARKETID", "TEXT", "FAR_TRANS"),
                _catalog_entry(
                    "col-ai-isin", "ASSET_INFORMATION", "ISIN", "TEXT", "FAR_TRANS"
                ),
                _catalog_entry(
                    "col-ai-market", "ASSET_INFORMATION", "MARKETID", "TEXT", "FAR_TRANS"
                ),
                _catalog_entry(
                    "col-sai-isin",
                    "STAGING_ASSET_INFORMATION",
                    "ISIN",
                    "TEXT",
                    "STAGING",
                ),
                _catalog_entry(
                    "col-sai-market",
                    "STAGING_ASSET_INFORMATION",
                    "MARKETID",
                    "TEXT",
                    "STAGING",
                ),
                _catalog_entry("col-lp-isin", "LIMIT_PRICES", "ISIN", "TEXT", "FAR_TRANS"),
            ],
        )
        input_ = SchemaMappingInput(request_context=_request_context(), payload=payload)

        output = await agent.run(input_)

        assert set(output.result.tables) == {"CLOSE_PRICES", "MARKETS"}

        joins_by_pair = {
            frozenset({j.left_table, j.right_table}): j for j in output.result.joins
        }
        assert len(output.result.joins) == 2
        assert frozenset({"CLOSE_PRICES", "STAGING_ASSET_INFORMATION"}) in joins_by_pair
        assert frozenset({"STAGING_ASSET_INFORMATION", "MARKETS"}) in joins_by_pair

        hop1 = joins_by_pair[frozenset({"CLOSE_PRICES", "STAGING_ASSET_INFORMATION"})]
        assert hop1.left_column == "ISIN"
        assert hop1.right_column == "ISIN"
        assert hop1.left_schema == "FAR_TRANS"
        assert hop1.right_schema == "STAGING"

        hop2 = joins_by_pair[frozenset({"STAGING_ASSET_INFORMATION", "MARKETS"})]
        assert hop2.left_column == "MARKETID"
        assert hop2.right_column == "MARKETID"

    async def test_no_unnecessary_bridge_when_anchor_already_connected_via_pass_1(
        self,
    ) -> None:
        """SIXTH REAL BUG, live-reproduced: "What is the total transaction
        volume by market?" (the session's flagship worked example)
        already connects `TRANSACTIONS` to `MARKETS` directly via
        `"Transaction happens in Market"`. `"Transaction involves Asset"`
        (also realizing_table `TRANSACTIONS`, key `ISIN`) independently
        has zero direct candidates (`MARKETS` has no `ISIN` column), which
        used to trigger an unnecessary bridge via `ASSET_INFORMATION`,
        adding a real but pointless extra join to already-correct SQL.
        Must produce exactly the one direct join, no bridge."""

        agent = SchemaMappingAgent()

        payload = SchemaMappingPayload(
            intent="comparison",
            concept_resolutions=[
                ConceptResolution(
                    term="transaction volume",
                    resolved=True,
                    catalog_column_id="col-value",
                    column_name="TOTALVALUE",
                    preferred=True,
                ),
                ConceptResolution(
                    term="market",
                    resolved=True,
                    catalog_column_id="col-market-name",
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
                RelationshipResolution(
                    subject_label="Transaction",
                    predicate="INVOLVES",
                    object_label="Asset",
                    realizing_table="TRANSACTIONS",
                    subject_key_column="ISIN",
                    object_key_column="ISIN",
                ),
                RelationshipResolution(
                    subject_label="Asset",
                    predicate="TRADED_IN",
                    object_label="Market",
                    realizing_table="ASSET_INFORMATION",
                    subject_key_column="MARKETID",
                    object_key_column="MARKETID",
                ),
            ],
            semantic_matches=[],
            catalog_inventory=[
                _catalog_entry("col-value", "TRANSACTIONS", "TOTALVALUE", "NUMBER"),
                _catalog_entry("col-txn-market", "TRANSACTIONS", "MARKETID", "TEXT"),
                _catalog_entry("col-txn-isin", "TRANSACTIONS", "ISIN", "TEXT"),
                _catalog_entry("col-market-name", "MARKETS", "NAME", "TEXT"),
                _catalog_entry("col-market-id", "MARKETS", "MARKETID", "TEXT"),
                _catalog_entry("col-ai-isin", "ASSET_INFORMATION", "ISIN", "TEXT"),
                _catalog_entry("col-ai-market", "ASSET_INFORMATION", "MARKETID", "TEXT"),
            ],
        )
        input_ = SchemaMappingInput(request_context=_request_context(), payload=payload)

        output = await agent.run(input_)

        assert set(output.result.tables) == {"TRANSACTIONS", "MARKETS"}
        assert len(output.result.joins) == 1
        join = output.result.joins[0]
        assert {join.left_table, join.right_table} == {"TRANSACTIONS", "MARKETS"}
        assert join.left_column == "MARKETID"
        assert join.right_column == "MARKETID"

    async def test_no_bridge_when_no_relationship_reaches_the_gap(self) -> None:
        """Two resolved tables sharing no key at all, and no relationship
        in `relationship_resolutions` bridges the gap -- must stay
        unjoined rather than inventing a connection."""

        agent = SchemaMappingAgent()

        payload = SchemaMappingPayload(
            intent="metric_lookup",
            concept_resolutions=[
                ConceptResolution(
                    term="closing price",
                    resolved=True,
                    catalog_column_id="col-close",
                    column_name="CLOSEPRICE",
                    preferred=True,
                ),
                ConceptResolution(
                    term="market name",
                    resolved=True,
                    catalog_column_id="col-market",
                    column_name="NAME",
                    preferred=True,
                ),
            ],
            relationship_resolutions=[],
            semantic_matches=[],
            catalog_inventory=[
                _catalog_entry("col-close", "CLOSE_PRICES", "CLOSEPRICE", "NUMBER"),
                _catalog_entry("col-close-isin", "CLOSE_PRICES", "ISIN", "TEXT"),
                _catalog_entry("col-market", "MARKETS", "NAME", "TEXT"),
                _catalog_entry("col-market-id", "MARKETS", "MARKETID", "TEXT"),
            ],
        )
        input_ = SchemaMappingInput(request_context=_request_context(), payload=payload)

        output = await agent.run(input_)

        assert output.result.joins == []

    async def test_ambiguous_bridge_candidate_joins_neither(self) -> None:
        """Two DIFFERENT relationships each independently propose a
        different bridge resolution for the same gap (different bridge
        table, or the same bridge table reaching a different second
        table) -- which one is correct can't be determined, so no bridge
        join is emitted, matching every other "never guess" guard in this
        method."""

        agent = SchemaMappingAgent()

        payload = SchemaMappingPayload(
            intent="metric_lookup",
            concept_resolutions=[
                ConceptResolution(
                    term="closing price",
                    resolved=True,
                    catalog_column_id="col-close",
                    column_name="CLOSEPRICE",
                    preferred=True,
                ),
                ConceptResolution(
                    term="market name",
                    resolved=True,
                    catalog_column_id="col-market",
                    column_name="NAME",
                    preferred=True,
                ),
                ConceptResolution(
                    term="sector",
                    resolved=True,
                    catalog_column_id="col-sector",
                    column_name="SECTOR",
                    preferred=True,
                ),
            ],
            relationship_resolutions=[
                # Bridge candidate A: ASSET_INFORMATION reaches MARKETS via
                # MARKETID and has ISIN too.
                RelationshipResolution(
                    subject_label="Asset",
                    predicate="TRADED_IN",
                    object_label="Market",
                    realizing_table="ASSET_INFORMATION",
                    subject_key_column="MARKETID",
                    object_key_column="MARKETID",
                ),
                RelationshipResolution(
                    subject_label="Asset",
                    predicate="HAS_CLOSING_PRICE",
                    object_label="Price",
                    realizing_table="CLOSE_PRICES",
                    subject_key_column="ISIN",
                    object_key_column="ISIN",
                ),
                # Bridge candidate B: a second, independent bridge that ALSO
                # reaches a resolved table (SECTOR_INFO) via a different key,
                # while also carrying ISIN -- a genuine second candidate.
                RelationshipResolution(
                    subject_label="Asset",
                    predicate="IN_SECTOR",
                    object_label="Sector",
                    realizing_table="SECTOR_BRIDGE",
                    subject_key_column="SECTORID",
                    object_key_column="SECTORID",
                ),
                RelationshipResolution(
                    subject_label="Asset",
                    predicate="HAS_SECTOR_BRIDGE",
                    object_label="Sector",
                    realizing_table="SECTOR_BRIDGE",
                    subject_key_column="ISIN",
                    object_key_column="ISIN",
                ),
            ],
            semantic_matches=[],
            catalog_inventory=[
                _catalog_entry("col-close", "CLOSE_PRICES", "CLOSEPRICE", "NUMBER"),
                _catalog_entry("col-close-isin", "CLOSE_PRICES", "ISIN", "TEXT"),
                _catalog_entry("col-market", "MARKETS", "NAME", "TEXT"),
                _catalog_entry("col-market-id", "MARKETS", "MARKETID", "TEXT"),
                _catalog_entry("col-sector", "SECTOR_INFO", "SECTOR", "TEXT"),
                _catalog_entry("col-sector-id", "SECTOR_INFO", "SECTORID", "TEXT"),
                _catalog_entry("col-ai-isin", "ASSET_INFORMATION", "ISIN", "TEXT"),
                _catalog_entry("col-ai-market", "ASSET_INFORMATION", "MARKETID", "TEXT"),
                _catalog_entry("col-sb-isin", "SECTOR_BRIDGE", "ISIN", "TEXT"),
                _catalog_entry("col-sb-sector", "SECTOR_BRIDGE", "SECTORID", "TEXT"),
            ],
        )
        input_ = SchemaMappingInput(request_context=_request_context(), payload=payload)

        output = await agent.run(input_)

        # CLOSE_PRICES<->MARKETS has two distinct, conflicting bridge
        # candidates (via ASSET_INFORMATION or via SECTOR_BRIDGE) -- neither
        # should be guessed.
        joins_by_pair = {
            frozenset({j.left_table, j.right_table}) for j in output.result.joins
        }
        assert frozenset({"CLOSE_PRICES", "ASSET_INFORMATION"}) not in joins_by_pair
        assert frozenset({"CLOSE_PRICES", "SECTOR_BRIDGE"}) not in joins_by_pair
        assert frozenset({"ASSET_INFORMATION", "MARKETS"}) not in joins_by_pair


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
