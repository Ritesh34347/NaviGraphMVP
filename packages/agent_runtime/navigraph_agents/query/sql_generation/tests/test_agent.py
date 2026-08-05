"""Real unit tests for the SQL Generation agent.

Uses `FakeLLMClient` exclusively -- no network access, no API key required.
`asyncio_mode = "auto"` is set in packages/agent_runtime/pyproject.toml, so
these `async def test_...` functions run without an explicit
`@pytest.mark.asyncio` decorator.
"""

from __future__ import annotations

import json

import pytest
from navigraph_shared.contracts import RequestContext
from navigraph_shared.llm import FakeLLMClient
from pydantic import ValidationError

from navigraph_agents.query.sql_generation.agent import SqlGenerationAgent
from navigraph_agents.query.sql_generation.contracts import (
    IntentLabel,
    JoinSpec,
    ResolvedColumnRef,
    ResolvedDataSource,
    SchemaMappingResult,
    SqlGenerationInput,
    SqlGenerationPayload,
)

# The real, live-verified worked example from schema_mapping's own worked
# example: "What is the total transaction volume by market?" resolves to
# STAGING_TRANSACTIONS.UNITS (measure) and STAGING_TRANSACTIONS.MARKETID
# (dimension), no joins.
_MARKET_VOLUME_COLUMNS = [
    ResolvedColumnRef(
        term="units traded",
        catalog_column_id="col_units",
        table_name="STAGING_TRANSACTIONS",
        schema_name="STAGING",
        column_name="UNITS",
        data_type="NUMBER",
        role="measure",
    ),
    ResolvedColumnRef(
        term="market",
        catalog_column_id="col_market",
        table_name="STAGING_TRANSACTIONS",
        schema_name="STAGING",
        column_name="MARKETID",
        data_type="TEXT",
        role="dimension",
    ),
]

_MARKET_VOLUME_DATA_SOURCES = [
    ResolvedDataSource(
        table_name="STAGING_TRANSACTIONS",
        data_source_id="ds_snowflake_prod",
        source_type="snowflake",
        reachable=True,
    ),
]


def _make_input(
    *,
    question: str = "What is the total transaction volume by market?",
    intent: IntentLabel = "metric_lookup",
    tables: list[str] | None = None,
    columns: list[ResolvedColumnRef] | None = None,
    joins: list[JoinSpec] | None = None,
    resolved_data_sources: list[ResolvedDataSource] | None = None,
) -> SqlGenerationInput:
    cols = columns if columns is not None else _MARKET_VOLUME_COLUMNS
    return SqlGenerationInput(
        request_context=RequestContext(
            tenant_id="tenant-acme",
            user_id="user-1",
            trace_id="trace-1",
            roles=["analyst"],
        ),
        payload=SqlGenerationPayload(
            original_question=question,
            intent=intent,
            schema_mapping=SchemaMappingResult(
                tables=tables if tables is not None else ["STAGING_TRANSACTIONS"],
                columns=cols,
                joins=joins or [],
            ),
            resolved_data_sources=(
                resolved_data_sources
                if resolved_data_sources is not None
                else _MARKET_VOLUME_DATA_SOURCES
            ),
        ),
    )


# ---------------------------------------------------------------------------
# (a) The worked example: no predicate phrase -> zero LLM calls, exact SQL
# skeleton.
# ---------------------------------------------------------------------------


async def test_worked_example_produces_exact_sql_skeleton_with_no_llm_call() -> None:
    fake_llm = FakeLLMClient(response="should never be read")
    agent = SqlGenerationAgent(llm_client=fake_llm)

    output = await agent.run(_make_input())

    # The single most important assertion for the no-predicate-phrase path.
    assert fake_llm.calls == []

    assert output.errors == []
    assert output.confidence == 1.0
    assert output.result.predicate_resolutions == []
    assert output.result.unresolved_predicates == []

    assert len(output.result.statements) == 1
    statement = output.result.statements[0]
    assert statement.data_source_id == "ds_snowflake_prod"
    assert statement.sql == (
        "SELECT STAGING_TRANSACTIONS.MARKETID, SUM(STAGING_TRANSACTIONS.UNITS) AS UNITS_TOTAL\n"
        "FROM STAGING.STAGING_TRANSACTIONS\n"
        "GROUP BY STAGING_TRANSACTIONS.MARKETID"
    )
    assert statement.params == {}
    assert statement.referenced_tables == ["STAGING_TRANSACTIONS"]
    assert set(statement.referenced_columns) == {
        "STAGING_TRANSACTIONS.MARKETID",
        "STAGING_TRANSACTIONS.UNITS",
    }

    assert len(output.lineage_events) == 1
    assert output.lineage_events[0].agent_name == "query.sql_generation"

    assert output.metadata.model_version is None
    assert output.metadata.prompt_version is None
    assert output.metadata.tokens_input is None
    assert output.metadata.tokens_output is None
    assert output.metadata.latency_ms >= 0


# ---------------------------------------------------------------------------
# (a2) Real bug found live (LIMITATIONS.md item 38): a "how many X" question
# must produce COUNT(*), never a SUM over a resolved measure column.
# ---------------------------------------------------------------------------

_TRANSACTION_COUNT_COLUMNS = [
    ResolvedColumnRef(
        term="customer",
        catalog_column_id="col_customer",
        table_name="STAGING_TRANSACTIONS",
        schema_name="STAGING",
        column_name="CUSTOMERID",
        data_type="TEXT",
        role="dimension",
    ),
]


async def test_how_many_question_produces_count_star_not_sum() -> None:
    """The real gq_002 scenario: "How many transactions has each customer
    made?" resolves only a dimension column (CUSTOMERID), no measure
    column at all -- the fix must still produce a real COUNT(*), not a
    bare, ungrouped SELECT."""

    fake_llm = FakeLLMClient(response="should never be read")
    agent = SqlGenerationAgent(llm_client=fake_llm)

    output = await agent.run(
        _make_input(
            question="How many transactions has each customer made?",
            intent="metric_lookup",
            columns=_TRANSACTION_COUNT_COLUMNS,
        )
    )

    assert fake_llm.calls == []
    assert output.errors == []
    statement = output.result.statements[0]
    assert statement.sql == (
        "SELECT STAGING_TRANSACTIONS.CUSTOMERID, COUNT(*) AS RECORD_COUNT\n"
        "FROM STAGING.STAGING_TRANSACTIONS\n"
        "GROUP BY STAGING_TRANSACTIONS.CUSTOMERID"
    )


async def test_how_many_question_ignores_a_spuriously_resolved_measure_column() -> None:
    """Even if schema_mapping (or an upstream mis-resolution) attaches a
    real `role="measure"` column to a "how many" question, it must not be
    summed -- COUNT(*) always wins for this question shape."""

    fake_llm = FakeLLMClient(response="should never be read")
    agent = SqlGenerationAgent(llm_client=fake_llm)

    output = await agent.run(
        _make_input(
            question="How many transactions has each customer made?",
            intent="metric_lookup",
            columns=_TRANSACTION_COUNT_COLUMNS + [_MARKET_VOLUME_COLUMNS[0]],  # adds UNITS as measure
        )
    )

    statement = output.result.statements[0]
    assert "SUM" not in statement.sql
    assert "COUNT(*) AS RECORD_COUNT" in statement.sql


# ---------------------------------------------------------------------------
# (a3) Real bug found live (LIMITATIONS.md item 80): a question shaped
# nothing like "how many X" (so `_is_count_question` never fires) can still
# resolve an identifier column (e.g. TRANSACTIONID) as a `role="measure"`
# column -- summing it produces a nonsensical, enormous total. An
# identifier column must always use COUNT, regardless of phrasing; a real
# additive measure resolved in the SAME query must still use SUM.
# ---------------------------------------------------------------------------

_ID_AND_VALUE_MEASURE_COLUMNS = [
    ResolvedColumnRef(
        term="transaction count",
        catalog_column_id="col_transaction_id",
        table_name="STAGING_TRANSACTIONS",
        schema_name="STAGING",
        column_name="TRANSACTIONID",
        data_type="NUMBER",
        role="measure",
    ),
    ResolvedColumnRef(
        term="transaction value",
        catalog_column_id="col_total_value",
        table_name="STAGING_TRANSACTIONS",
        schema_name="STAGING",
        column_name="TOTALVALUE",
        data_type="NUMBER",
        role="measure",
    ),
]


async def test_identifier_shaped_measure_column_is_counted_not_summed() -> None:
    """The real, live-reproduced item 80 scenario: "How does the transaction
    count and value on 2018-01-02 compare to prior weeks..." doesn't trip
    `_is_count_question` (no "how many"/"number of"/"count of" phrase), so
    Semantic Retrieval's real match of "transaction count" to TRANSACTIONID
    reaches `_aggregation_function` as a normal measure column. It must be
    counted, not summed -- while a real additive measure (TOTALVALUE)
    resolved in the same query must still be summed.
    """

    fake_llm = FakeLLMClient(response="should never be read")
    agent = SqlGenerationAgent(llm_client=fake_llm)

    output = await agent.run(
        _make_input(
            question=(
                "How does the transaction count and value on 2018-01-02 "
                "compare to the same day in prior weeks or the prior month average?"
            ),
            intent="comparison",
            columns=_ID_AND_VALUE_MEASURE_COLUMNS,
        )
    )

    statement = output.result.statements[0]
    assert "SUM(STAGING_TRANSACTIONS.TRANSACTIONID)" not in statement.sql
    assert "COUNT(STAGING_TRANSACTIONS.TRANSACTIONID) AS TRANSACTIONID_TOTAL" in statement.sql
    assert "SUM(STAGING_TRANSACTIONS.TOTALVALUE) AS TOTALVALUE_TOTAL" in statement.sql


# ---------------------------------------------------------------------------
# (b) A real predicate phrase + a canned valid LLM response -> a correctly
# bind-parameterized GeneratedSql. The literal value must appear in `params`,
# never in the `sql` string.
# ---------------------------------------------------------------------------


async def test_predicate_phrase_is_bind_parameterized_not_interpolated() -> None:
    fake_llm = FakeLLMClient(
        response=json.dumps(
            {
                "predicates": [
                    {
                        "raw_phrase": "market XATH last quarter",
                        "column": "MARKETID",
                        "operator": "=",
                        "value": "XATH",
                        "rationale": "the question filters to a single named market",
                    }
                ]
            }
        )
    )
    agent = SqlGenerationAgent(llm_client=fake_llm)

    output = await agent.run(
        _make_input(
            question="What was transaction volume last quarter for market XATH?",
        )
    )

    # The trigger phrase ("last ", "quarter") must have caused exactly one
    # real LLM call.
    assert len(fake_llm.calls) == 1

    assert output.errors == []
    assert output.confidence == 1.0
    assert len(output.result.predicate_resolutions) == 1
    predicate = output.result.predicate_resolutions[0]
    assert predicate.column == "MARKETID"
    assert predicate.operator == "="
    assert predicate.resolved_value == "XATH"
    assert output.result.unresolved_predicates == []

    assert len(output.result.statements) == 1
    statement = output.result.statements[0]

    # The literal value is bound as a parameter...
    assert statement.params == {"predicate_0": "XATH"}
    # ...and appears nowhere in the SQL text itself.
    assert "XATH" not in statement.sql
    assert "WHERE STAGING_TRANSACTIONS.MARKETID = %(predicate_0)s" in statement.sql


# ---------------------------------------------------------------------------
# (b2) REAL BUG, found live: a resolved dimension column whose `.term` names
# a specific VALUE of that column (e.g. "Mobile App" resolving to
# CHANNEL_NAME) rather than the column itself must also trigger the
# predicate-resolution LLM call, even with zero temporal trigger words --
# see `_resolved_via_named_value`'s own docstring for the full live incident.
# ---------------------------------------------------------------------------


async def test_named_value_dimension_triggers_predicate_resolution_with_no_temporal_words() -> None:
    fake_llm = FakeLLMClient(
        response=json.dumps(
            {
                "predicates": [
                    {
                        "raw_phrase": "the Mobile App",
                        "column": "CHANNEL_NAME",
                        "operator": "=",
                        "value": "Mobile App",
                        "rationale": "names a specific channel value, not the dimension itself",
                    }
                ]
            }
        )
    )
    agent = SqlGenerationAgent(llm_client=fake_llm)

    revenue_by_channel_columns = [
        ResolvedColumnRef(
            term="revenue",
            catalog_column_id="col_line_total",
            table_name="FACT_ORDER_ITEMS",
            schema_name="CORE",
            column_name="LINE_TOTAL",
            data_type="NUMBER",
            role="measure",
        ),
        ResolvedColumnRef(
            term="Mobile App",
            catalog_column_id="col_channel_name",
            table_name="DIM_CHANNEL",
            schema_name="CORE",
            column_name="CHANNEL_NAME",
            data_type="TEXT",
            role="dimension",
        ),
    ]

    output = await agent.run(
        _make_input(
            question="How much revenue came from the Mobile App?",
            columns=revenue_by_channel_columns,
            tables=["FACT_ORDER_ITEMS", "DIM_CHANNEL"],
            joins=[
                JoinSpec(
                    left_table="DIM_CHANNEL",
                    left_column="CHANNEL_ID",
                    right_table="FACT_ORDER_ITEMS",
                    right_column="CHANNEL_ID",
                    relationship_concept="OrderItem uses Channel",
                )
            ],
            resolved_data_sources=[
                ResolvedDataSource(
                    table_name="FACT_ORDER_ITEMS",
                    data_source_id="ds_snowflake_prod",
                    source_type="snowflake",
                    reachable=True,
                ),
                ResolvedDataSource(
                    table_name="DIM_CHANNEL",
                    data_source_id="ds_snowflake_prod",
                    source_type="snowflake",
                    reachable=True,
                ),
            ],
        )
    )

    # No temporal trigger word anywhere in the question -- the LLM call only
    # happens because CHANNEL_NAME was resolved via the value "Mobile App",
    # not via a generic "channel" reference.
    assert len(fake_llm.calls) == 1
    assert len(output.result.predicate_resolutions) == 1
    predicate = output.result.predicate_resolutions[0]
    assert predicate.column == "CHANNEL_NAME"
    assert predicate.operator == "="
    assert predicate.resolved_value == "Mobile App"

    statement = output.result.statements[0]
    assert statement.params == {"predicate_0": "Mobile App"}
    assert "Mobile App" not in statement.sql
    assert "WHERE DIM_CHANNEL.CHANNEL_NAME = %(predicate_0)s" in statement.sql


async def test_generic_dimension_reference_does_not_trigger_predicate_resolution() -> None:
    """The counterpart to the test above: a plain "by channel" phrasing
    (`term="channel"` resolving to `CHANNEL_NAME`) must NOT trigger the LLM
    call -- `_resolved_via_named_value` correctly recognizes "channel" as a
    real substring of "channelname" and treats it as a genuine dimension
    reference, not a named filter value."""

    fake_llm = FakeLLMClient(response="should never be read")
    agent = SqlGenerationAgent(llm_client=fake_llm)

    revenue_by_channel_columns = [
        ResolvedColumnRef(
            term="revenue",
            catalog_column_id="col_line_total",
            table_name="FACT_ORDER_ITEMS",
            schema_name="CORE",
            column_name="LINE_TOTAL",
            data_type="NUMBER",
            role="measure",
        ),
        ResolvedColumnRef(
            term="channel",
            catalog_column_id="col_channel_name",
            table_name="DIM_CHANNEL",
            schema_name="CORE",
            column_name="CHANNEL_NAME",
            data_type="TEXT",
            role="dimension",
        ),
    ]

    output = await agent.run(
        _make_input(
            question="What is the total revenue by channel?",
            columns=revenue_by_channel_columns,
            tables=["FACT_ORDER_ITEMS", "DIM_CHANNEL"],
            joins=[
                JoinSpec(
                    left_table="DIM_CHANNEL",
                    left_column="CHANNEL_ID",
                    right_table="FACT_ORDER_ITEMS",
                    right_column="CHANNEL_ID",
                    relationship_concept="OrderItem uses Channel",
                )
            ],
            resolved_data_sources=[
                ResolvedDataSource(
                    table_name="FACT_ORDER_ITEMS",
                    data_source_id="ds_snowflake_prod",
                    source_type="snowflake",
                    reachable=True,
                ),
                ResolvedDataSource(
                    table_name="DIM_CHANNEL",
                    data_source_id="ds_snowflake_prod",
                    source_type="snowflake",
                    reachable=True,
                ),
            ],
        )
    )

    assert len(fake_llm.calls) == 0
    assert output.result.predicate_resolutions == []
    statement = output.result.statements[0]
    assert "WHERE" not in statement.sql


async def test_compound_named_value_phrase_triggers_predicate_resolution() -> None:
    """REAL BUG #2, found live re-testing bug #1's own fix: Intent
    Understanding extracts the COMPOUND phrase ("Gold loyalty tier"), not
    the bare value ("Gold") -- confirmed via a direct diagnostic call.
    A bidirectional substring check was wrongly satisfied here (the column
    name "LOYALTYTIER" is a real suffix of the term "GOLDLOYALTYTIER"), so
    this must use the one-directional check instead: "goldloyaltytier" is
    NOT contained in "loyaltytier" (it's the other way around), so this
    correctly triggers."""

    fake_llm = FakeLLMClient(
        response=json.dumps(
            {
                "predicates": [
                    {
                        "raw_phrase": "the Gold loyalty tier",
                        "column": "LOYALTY_TIER",
                        "operator": "=",
                        "value": "Gold",
                        "rationale": "names a specific loyalty tier value, not the dimension itself",
                    }
                ]
            }
        )
    )
    agent = SqlGenerationAgent(llm_client=fake_llm)

    columns = [
        ResolvedColumnRef(
            term="revenue",
            catalog_column_id="col_line_total",
            table_name="FACT_ORDER_ITEMS",
            schema_name="CORE",
            column_name="LINE_TOTAL",
            data_type="NUMBER",
            role="measure",
        ),
        ResolvedColumnRef(
            term="Gold loyalty tier",
            catalog_column_id="col_loyalty_tier",
            table_name="DIM_CUSTOMER",
            schema_name="CORE",
            column_name="LOYALTY_TIER",
            data_type="TEXT",
            role="dimension",
        ),
    ]

    output = await agent.run(
        _make_input(
            question="How much revenue came from customers in the Gold loyalty tier?",
            columns=columns,
            tables=["FACT_ORDER_ITEMS", "DIM_CUSTOMER"],
            joins=[
                JoinSpec(
                    left_table="DIM_CUSTOMER",
                    left_column="CUSTOMER_ID",
                    right_table="FACT_ORDER_ITEMS",
                    right_column="CUSTOMER_ID",
                    relationship_concept="OrderItem involves Customer",
                )
            ],
            resolved_data_sources=[
                ResolvedDataSource(
                    table_name="FACT_ORDER_ITEMS",
                    data_source_id="ds_snowflake_prod",
                    source_type="snowflake",
                    reachable=True,
                ),
                ResolvedDataSource(
                    table_name="DIM_CUSTOMER",
                    data_source_id="ds_snowflake_prod",
                    source_type="snowflake",
                    reachable=True,
                ),
            ],
        )
    )

    assert len(fake_llm.calls) == 1
    assert len(output.result.predicate_resolutions) == 1
    predicate = output.result.predicate_resolutions[0]
    assert predicate.column == "LOYALTY_TIER"
    assert predicate.resolved_value == "Gold"
    statement = output.result.statements[0]
    assert statement.params == {"predicate_0": "Gold"}
    assert "WHERE DIM_CUSTOMER.LOYALTY_TIER = %(predicate_0)s" in statement.sql


# ---------------------------------------------------------------------------
# (c) A hallucinated/invalid `column` in the LLM response must be rejected,
# not silently trusted -- mirrors SemanticRetrievalAgent's hallucination
# rejection test exactly.
# ---------------------------------------------------------------------------


async def test_hallucinated_column_is_rejected_not_silently_trusted() -> None:
    fake_llm = FakeLLMClient(
        response=json.dumps(
            {
                "predicates": [
                    {
                        "raw_phrase": "last quarter",
                        "column": "TOTALLY_MADE_UP_COLUMN",
                        "operator": "=",
                        "value": "Q1",
                        "rationale": "this column does not exist in the candidate list",
                    }
                ]
            }
        )
    )
    agent = SqlGenerationAgent(llm_client=fake_llm)

    output = await agent.run(
        _make_input(question="What was transaction volume last quarter?")
    )

    assert output.result.predicate_resolutions == []
    assert output.result.unresolved_predicates == ["last quarter"]

    assert len(output.errors) == 1
    assert output.errors[0].code == "llm_returned_invalid_column"
    assert output.errors[0].recoverable is True
    assert "TOTALLY_MADE_UP_COLUMN" in output.errors[0].message

    # A recoverable-only error still yields a (degraded) SQL statement, just
    # with no WHERE clause -- the hallucinated predicate is dropped, not
    # used.
    assert output.confidence == 0.5
    assert len(output.result.statements) == 1
    statement = output.result.statements[0]
    assert "WHERE" not in statement.sql
    assert statement.params == {}


# ---------------------------------------------------------------------------
# (d) A multi-table case with a real JoinSpec produces a correct
# `JOIN ... ON ...` clause.
# ---------------------------------------------------------------------------

# Real relationship seed data from navigraph_kg.ontology.RELATIONSHIP_CONCEPTS:
# "Customer has RiskLevel" realizes via CUSTOMER_INFORMATION.CUSTOMERID /
# .RISKLEVEL, in the FAR_TRANS schema (navigraph_kg's live-verified schema).
_JOIN_COLUMNS = [
    ResolvedColumnRef(
        term="units traded",
        catalog_column_id="col_units",
        table_name="TRANSACTIONS",
        schema_name="FAR_TRANS",
        column_name="UNITS",
        data_type="NUMBER",
        role="measure",
    ),
    ResolvedColumnRef(
        term="customer risk level",
        catalog_column_id="col_risk",
        table_name="CUSTOMER_INFORMATION",
        schema_name="FAR_TRANS",
        column_name="RISKLEVEL",
        data_type="TEXT",
        role="dimension",
    ),
]

_JOIN_SPEC = JoinSpec(
    left_table="TRANSACTIONS",
    left_column="CUSTOMERID",
    right_table="CUSTOMER_INFORMATION",
    right_column="CUSTOMERID",
    relationship_concept="Customer HAS RiskLevel",
)

_JOIN_DATA_SOURCES = [
    ResolvedDataSource(
        table_name="TRANSACTIONS",
        data_source_id="ds_snowflake_prod",
        source_type="snowflake",
        reachable=True,
    ),
    ResolvedDataSource(
        table_name="CUSTOMER_INFORMATION",
        data_source_id="ds_snowflake_prod",
        source_type="snowflake",
        reachable=True,
    ),
]


async def test_multi_table_join_produces_correct_join_clause() -> None:
    # `_JOIN_COLUMNS`'s "customer risk level" -> `RISKLEVEL` resolution is a
    # real, accepted false positive of `_resolved_via_named_value` (the
    # compound term is longer than, and not contained in, "risklevel" --
    # see that helper's own docstring, "REAL BUG #2", for why the
    # one-directional check accepts this tradeoff). A valid, empty response
    # reflects the resulting (harmless) extra LLM call.
    fake_llm = FakeLLMClient(response=json.dumps({"predicates": []}))
    agent = SqlGenerationAgent(llm_client=fake_llm)

    output = await agent.run(
        _make_input(
            question="What is total units traded by customer risk level?",
            tables=["CUSTOMER_INFORMATION", "TRANSACTIONS"],
            columns=_JOIN_COLUMNS,
            joins=[_JOIN_SPEC],
            resolved_data_sources=_JOIN_DATA_SOURCES,
        )
    )

    assert output.errors == []

    assert len(output.result.statements) == 1
    statement = output.result.statements[0]
    assert statement.sql == (
        "SELECT CUSTOMER_INFORMATION.RISKLEVEL, SUM(TRANSACTIONS.UNITS) AS UNITS_TOTAL\n"
        "FROM FAR_TRANS.CUSTOMER_INFORMATION\n"
        "JOIN FAR_TRANS.TRANSACTIONS ON TRANSACTIONS.CUSTOMERID = CUSTOMER_INFORMATION.CUSTOMERID\n"
        "GROUP BY CUSTOMER_INFORMATION.RISKLEVEL"
    )
    assert set(statement.referenced_tables) == {"CUSTOMER_INFORMATION", "TRANSACTIONS"}


# ---------------------------------------------------------------------------
# (c2) FIFTH REAL BUG (schema_mapping's `_build_joins`): a 2-hop bridge join
# through a table that contributes no selected column at all (e.g.
# STAGING_ASSET_INFORMATION, needed only to connect CLOSE_PRICES to
# MARKETS). Proves the bridge table's schema comes from `JoinSpec.left_
# schema`/`right_schema` (schema_mapping's own catalog-derived source of
# truth), since `schema_by_table`'s `columns`-only derivation has no entry
# for a table that was never independently resolved.
# ---------------------------------------------------------------------------

_BRIDGE_COLUMNS = [
    ResolvedColumnRef(
        term="average closing price",
        catalog_column_id="col_close",
        table_name="CLOSE_PRICES",
        schema_name="FAR_TRANS",
        column_name="CLOSEPRICE",
        data_type="NUMBER",
        role="measure",
    ),
    ResolvedColumnRef(
        term="Euronext - Growth Paris",
        catalog_column_id="col_market",
        table_name="MARKETS",
        schema_name="FAR_TRANS",
        column_name="NAME",
        data_type="TEXT",
        role="dimension",
    ),
]

_BRIDGE_JOINS = [
    JoinSpec(
        left_table="CLOSE_PRICES",
        left_column="ISIN",
        right_table="STAGING_ASSET_INFORMATION",
        right_column="ISIN",
        left_schema="FAR_TRANS",
        right_schema="STAGING",
        relationship_concept="Asset HAS_CLOSING_PRICE Price (bridge)",
    ),
    JoinSpec(
        left_table="MARKETS",
        left_column="MARKETID",
        right_table="STAGING_ASSET_INFORMATION",
        right_column="MARKETID",
        left_schema="FAR_TRANS",
        right_schema="STAGING",
        relationship_concept="bridge via STAGING_ASSET_INFORMATION",
    ),
]

_BRIDGE_DATA_SOURCES = [
    ResolvedDataSource(
        table_name="CLOSE_PRICES",
        data_source_id="ds_snowflake_prod",
        source_type="snowflake",
        reachable=True,
    ),
    ResolvedDataSource(
        table_name="MARKETS",
        data_source_id="ds_snowflake_prod",
        source_type="snowflake",
        reachable=True,
    ),
]


async def test_two_hop_bridge_join_qualifies_bridge_table_schema_from_join_spec() -> None:
    fake_llm = FakeLLMClient(response=json.dumps({"predicates": []}))
    agent = SqlGenerationAgent(llm_client=fake_llm)

    output = await agent.run(
        _make_input(
            question="What is the average closing price for assets on Euronext - Growth Paris?",
            tables=["CLOSE_PRICES", "MARKETS"],
            columns=_BRIDGE_COLUMNS,
            joins=_BRIDGE_JOINS,
            resolved_data_sources=_BRIDGE_DATA_SOURCES,
        )
    )

    assert output.errors == []
    assert len(output.result.statements) == 1
    statement = output.result.statements[0]
    # The bridge table (STAGING_ASSET_INFORMATION) contributes no selected
    # column, so it appears only in the FROM/JOIN chain, correctly schema-
    # qualified via the join spec's own `left_schema`/`right_schema` --
    # never left bare (which would depend on the connection's default
    # schema and could silently resolve to the wrong registration).
    assert statement.sql == (
        "SELECT MARKETS.NAME, SUM(CLOSE_PRICES.CLOSEPRICE) AS CLOSEPRICE_TOTAL\n"
        "FROM STAGING.STAGING_ASSET_INFORMATION\n"
        "JOIN FAR_TRANS.CLOSE_PRICES ON CLOSE_PRICES.ISIN = STAGING_ASSET_INFORMATION.ISIN\n"
        "JOIN FAR_TRANS.MARKETS ON MARKETS.MARKETID = STAGING_ASSET_INFORMATION.MARKETID\n"
        "GROUP BY MARKETS.NAME"
    )
    assert set(statement.referenced_tables) == {"CLOSE_PRICES", "MARKETS"}


# ---------------------------------------------------------------------------
# (d2) Real bug found live: "What is the total transaction volume by
# market?" resolving STAGING_TRANSACTIONS + STAGING_MARKETS with an empty
# `joins` list (no curated RelationshipConcept exists yet for this table
# pair) used to silently fall back to a comma-join `FROM A, B` -- a real
# Cartesian product that made every market's GROUP BY row show the same
# grand-total sum. This must now be a real, non-recoverable AgentError
# instead of a statement that looks like a correct per-market breakdown.
# ---------------------------------------------------------------------------

_UNJOINED_MARKET_COLUMNS = [
    ResolvedColumnRef(
        term="transaction value",
        catalog_column_id="col_total_value",
        table_name="STAGING_TRANSACTIONS",
        schema_name="STAGING",
        column_name="TOTALVALUE",
        data_type="NUMBER",
        role="measure",
    ),
    ResolvedColumnRef(
        term="market",
        catalog_column_id="col_market_name",
        table_name="STAGING_MARKETS",
        schema_name="STAGING",
        column_name="NAME",
        data_type="TEXT",
        role="dimension",
    ),
]

_UNJOINED_MARKET_DATA_SOURCES = [
    ResolvedDataSource(
        table_name="STAGING_TRANSACTIONS",
        data_source_id="ds_snowflake_prod",
        source_type="snowflake",
        reachable=True,
    ),
    ResolvedDataSource(
        table_name="STAGING_MARKETS",
        data_source_id="ds_snowflake_prod",
        source_type="snowflake",
        reachable=True,
    ),
]


async def test_unjoined_multi_table_query_is_rejected_not_cartesian_joined() -> None:
    # `_UNJOINED_MARKET_COLUMNS`'s "market" -> `NAME` resolution is a real
    # false positive of `_resolved_via_named_value` (the term "market"
    # doesn't textually match the column name "NAME" -- `_resolved_via_named_value`
    # has no visibility into the real glossary synonym linking them,
    # since `ResolvedColumnRef` deliberately doesn't carry synonyms/
    # business_name -- see that helper's own docstring for why this
    # tradeoff is accepted: the extra LLM call is a real cost, not a
    # correctness risk, since a real LLM would correctly return no
    # predicates here). A valid, empty response reflects that.
    fake_llm = FakeLLMClient(response=json.dumps({"predicates": []}))
    agent = SqlGenerationAgent(llm_client=fake_llm)

    output = await agent.run(
        _make_input(
            question="What is the total transaction volume by market?",
            tables=["STAGING_TRANSACTIONS", "STAGING_MARKETS"],
            columns=_UNJOINED_MARKET_COLUMNS,
            joins=[],
            resolved_data_sources=_UNJOINED_MARKET_DATA_SOURCES,
        )
    )

    assert output.result.statements == []
    assert len(output.errors) == 1
    assert output.errors[0].code == "unjoined_table_in_multi_table_query"
    assert output.errors[0].recoverable is False
    assert output.confidence == 0.0

    # No comma-separated Cartesian-product FROM clause was ever built.
    assert "FROM STAGING.STAGING_TRANSACTIONS, STAGING.STAGING_MARKETS" not in str(
        output.errors[0].message
    )


async def test_partially_unjoined_multi_table_query_is_also_rejected() -> None:
    """Three tables where the provided join only connects two of them --
    the trailing table must still trigger the same real error, not a
    partial comma-join appended to an otherwise-real JOIN clause.

    Uses a valid, empty-predicates canned response for the same reason as
    `test_unjoined_multi_table_query_is_rejected_not_cartesian_joined`
    above -- the "market" -> `NAME` column in this fixture is a real,
    accepted false positive of `_resolved_via_named_value`."""

    fake_llm = FakeLLMClient(response=json.dumps({"predicates": []}))
    agent = SqlGenerationAgent(llm_client=fake_llm)

    columns = _JOIN_COLUMNS + [_UNJOINED_MARKET_COLUMNS[1]]
    data_sources = _JOIN_DATA_SOURCES + [
        ResolvedDataSource(
            table_name="STAGING_MARKETS",
            data_source_id="ds_snowflake_prod",
            source_type="snowflake",
            reachable=True,
        ),
    ]

    output = await agent.run(
        _make_input(
            question="What is total units traded by customer risk level and market?",
            tables=["CUSTOMER_INFORMATION", "TRANSACTIONS", "STAGING_MARKETS"],
            columns=columns,
            joins=[_JOIN_SPEC],
            resolved_data_sources=data_sources,
        )
    )

    assert output.result.statements == []
    assert len(output.errors) == 1
    assert output.errors[0].code == "unjoined_table_in_multi_table_query"
    assert "STAGING_MARKETS" in output.errors[0].message


# ---------------------------------------------------------------------------
# Additional coverage mirroring the sibling agents' thoroughness.
# ---------------------------------------------------------------------------


async def test_malformed_llm_json_falls_back_gracefully_without_raising() -> None:
    fake_llm = FakeLLMClient(response="this is not json at all")
    agent = SqlGenerationAgent(llm_client=fake_llm)

    output = await agent.run(
        _make_input(question="What was transaction volume last quarter?")
    )

    assert output.result.predicate_resolutions == []
    assert output.result.unresolved_predicates == []
    assert len(output.errors) == 1
    assert output.errors[0].code == "llm_response_not_json"
    assert output.errors[0].recoverable is True
    assert output.confidence == 0.5
    # Still produces a (predicate-less) statement rather than nothing.
    assert len(output.result.statements) == 1


async def test_llm_call_failure_falls_back_gracefully_without_raising() -> None:
    def _raise(system, messages, max_tokens):
        raise RuntimeError("simulated network failure")

    fake_llm = FakeLLMClient(response_fn=_raise)
    agent = SqlGenerationAgent(llm_client=fake_llm)

    output = await agent.run(
        _make_input(question="What was transaction volume last quarter?")
    )

    assert output.errors[0].code == "llm_call_failed"
    assert output.errors[0].recoverable is False
    assert output.confidence == 0.0
    assert output.metadata.model_version is None
    assert output.metadata.tokens_input is None


async def test_unreachable_data_source_is_reported_and_no_sql_is_generated() -> None:
    fake_llm = FakeLLMClient(response="should never be read")
    agent = SqlGenerationAgent(llm_client=fake_llm)

    output = await agent.run(
        _make_input(
            resolved_data_sources=[
                ResolvedDataSource(
                    table_name="STAGING_TRANSACTIONS",
                    data_source_id="ds_snowflake_prod",
                    source_type="snowflake",
                    reachable=False,
                )
            ]
        )
    )

    assert output.result.statements == []
    assert any(e.code == "data_source_unreachable" for e in output.errors)
    assert output.confidence == 0.0


async def test_cross_source_query_is_reported_not_silently_generated() -> None:
    fake_llm = FakeLLMClient(response="should never be read")
    agent = SqlGenerationAgent(llm_client=fake_llm)

    output = await agent.run(
        _make_input(
            tables=["CUSTOMER_INFORMATION", "TRANSACTIONS"],
            columns=_JOIN_COLUMNS,
            joins=[_JOIN_SPEC],
            resolved_data_sources=[
                ResolvedDataSource(
                    table_name="TRANSACTIONS",
                    data_source_id="ds_snowflake_prod",
                    source_type="snowflake",
                    reachable=True,
                ),
                ResolvedDataSource(
                    table_name="CUSTOMER_INFORMATION",
                    data_source_id="ds_other_warehouse",
                    source_type="bigquery",
                    reachable=True,
                ),
            ],
        )
    )

    assert output.result.statements == []
    assert any(e.code == "cross_source_query_not_supported" for e in output.errors)
    assert output.confidence == 0.0


def test_request_context_without_tenant_id_fails_at_construction() -> None:
    """Reuses the contract-level guarantee: a SqlGenerationInput can never
    be constructed without a tenant-scoped RequestContext."""

    with pytest.raises(ValidationError):
        SqlGenerationInput(
            request_context=RequestContext(user_id="user-1", trace_id="trace-1"),  # type: ignore[call-arg]
            payload=SqlGenerationPayload(
                original_question="test",
                intent="metric_lookup",
                schema_mapping=SchemaMappingResult(
                    tables=["STAGING_TRANSACTIONS"], columns=_MARKET_VOLUME_COLUMNS
                ),
                resolved_data_sources=_MARKET_VOLUME_DATA_SOURCES,
            ),
        )
