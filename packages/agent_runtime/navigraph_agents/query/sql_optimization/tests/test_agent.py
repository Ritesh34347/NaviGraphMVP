"""Real unit tests for the SQL Optimization agent.

No mocking needed -- the agent is a pure function of its input, so these
are real end-to-end tests of `SqlOptimizationAgent.run` against
constructed `SqlOptimizationInput` payloads. `asyncio_mode = "auto"` is
set at the workspace root `packages/pyproject.toml`, so `async def
test_...` functions run without an explicit `@pytest.mark.asyncio`
decorator.
"""

from __future__ import annotations

from navigraph_shared.contracts import RequestContext

from navigraph_agents.query.sql_optimization.agent import (
    DEFAULT_LIMIT,
    LARGE_TABLE_ROW_THRESHOLD,
    SqlOptimizationAgent,
)
from navigraph_agents.query.sql_optimization.contracts import (
    GeneratedSql,
    SqlOptimizationInput,
    SqlOptimizationPayload,
)


def _request_context() -> RequestContext:
    return RequestContext(
        tenant_id="tenant-acme",
        user_id="user-1",
        trace_id="trace-1",
        roles=["analyst"],
    )


def _make_input(
    statements: list[GeneratedSql],
    table_row_count_estimates: dict[str, int | None] | None = None,
) -> SqlOptimizationInput:
    return SqlOptimizationInput(
        request_context=_request_context(),
        payload=SqlOptimizationPayload(
            statements=statements,
            tenant_id="tenant-acme",
            trace_id="trace-1",
            table_row_count_estimates=table_row_count_estimates or {},
        ),
    )


def _generated(
    sql: str,
    data_source_id: str = "ds-1",
    referenced_tables: list[str] | None = None,
    referenced_columns: list[str] | None = None,
) -> GeneratedSql:
    return GeneratedSql(
        data_source_id=data_source_id,
        sql=sql,
        params={},
        referenced_tables=referenced_tables or ["STAGING.STAGING_TRANSACTIONS"],
        referenced_columns=referenced_columns or ["MARKETID", "UNITS"],
    )


class TestLimitInjection:
    async def test_limit_injected_when_absent(self) -> None:
        agent = SqlOptimizationAgent()
        sql = "SELECT MARKETID, SUM(UNITS) AS UNITS_TOTAL FROM STAGING.STAGING_TRANSACTIONS GROUP BY MARKETID"
        output = await agent.run(_make_input([_generated(sql)]))

        statement = output.result.statements[0]
        assert f"LIMIT {DEFAULT_LIMIT}" in statement.sql
        assert "inject_limit" in statement.applied_rules

    async def test_limit_not_reinjected_or_duplicated_when_already_present(self) -> None:
        agent = SqlOptimizationAgent()
        sql = "SELECT MARKETID FROM STAGING.STAGING_TRANSACTIONS LIMIT 500"
        output = await agent.run(_make_input([_generated(sql)]))

        statement = output.result.statements[0]
        assert statement.sql.count("LIMIT") == 1
        assert "LIMIT 500" in statement.sql
        assert "inject_limit" not in statement.applied_rules

    async def test_limit_not_reinjected_when_present_with_trailing_semicolon(self) -> None:
        agent = SqlOptimizationAgent()
        sql = "SELECT MARKETID FROM STAGING.STAGING_TRANSACTIONS LIMIT 500;"
        output = await agent.run(_make_input([_generated(sql)]))

        statement = output.result.statements[0]
        assert statement.sql.count("LIMIT") == 1
        assert "inject_limit" not in statement.applied_rules


class TestAuditComment:
    async def test_audit_comment_always_prepended(self) -> None:
        agent = SqlOptimizationAgent()
        sql = "SELECT MARKETID FROM STAGING.STAGING_TRANSACTIONS LIMIT 500"
        output = await agent.run(_make_input([_generated(sql)]))

        statement = output.result.statements[0]
        assert statement.sql.startswith(
            "-- navigraph trace_id=trace-1 tenant_id=tenant-acme\n"
        )
        assert "audit_comment" in statement.applied_rules

    async def test_audit_comment_uses_payload_tenant_and_trace_not_request_context(self) -> None:
        # payload.tenant_id/trace_id deliberately differ from request_context
        # to prove the agent stamps the *payload*'s values, per its docstring.
        agent = SqlOptimizationAgent()
        input_ = SqlOptimizationInput(
            request_context=RequestContext(
                tenant_id="tenant-other",
                user_id="user-1",
                trace_id="trace-other",
                roles=[],
            ),
            payload=SqlOptimizationPayload(
                statements=[_generated("SELECT 1 FROM T LIMIT 10")],
                tenant_id="tenant-payload",
                trace_id="trace-payload",
            ),
        )
        output = await agent.run(input_)

        assert "trace_id=trace-payload" in output.result.statements[0].sql
        assert "tenant_id=tenant-payload" in output.result.statements[0].sql


class TestPredicateReordering:
    async def test_multi_predicate_where_reorders_equality_before_range(self) -> None:
        agent = SqlOptimizationAgent()
        sql = (
            "SELECT MARKETID FROM STAGING.STAGING_TRANSACTIONS "
            "WHERE UNITS > 10 AND MARKETID = 'US' AND REGION LIKE 'EAST%' "
            "AND STATUS = 'ACTIVE' GROUP BY MARKETID"
        )
        output = await agent.run(_make_input([_generated(sql)]))

        statement = output.result.statements[0]
        assert "reorder_predicates" in statement.applied_rules

        where_index = statement.sql.index("WHERE")
        group_by_index = statement.sql.index("GROUP BY")
        predicate_text = statement.sql[where_index:group_by_index]

        eq1 = predicate_text.index("MARKETID = 'US'")
        eq2 = predicate_text.index("STATUS = 'ACTIVE'")
        range1 = predicate_text.index("UNITS > 10")
        like1 = predicate_text.index("REGION LIKE 'EAST%'")

        # Both equality predicates come before both non-equality predicates,
        # each group keeping its original relative order.
        assert eq1 < range1
        assert eq1 < like1
        assert eq2 < range1
        assert eq2 < like1
        assert eq1 < eq2  # relative order preserved within the equality group
        assert range1 < like1  # relative order preserved within the "other" group

    async def test_single_predicate_where_is_a_noop(self) -> None:
        agent = SqlOptimizationAgent()
        sql = "SELECT MARKETID FROM STAGING.STAGING_TRANSACTIONS WHERE MARKETID = 'US'"
        output = await agent.run(_make_input([_generated(sql)]))

        statement = output.result.statements[0]
        assert "reorder_predicates" not in statement.applied_rules
        assert "WHERE MARKETID = 'US'" in statement.sql

    async def test_no_where_clause_is_a_noop(self) -> None:
        agent = SqlOptimizationAgent()
        sql = "SELECT MARKETID FROM STAGING.STAGING_TRANSACTIONS"
        output = await agent.run(_make_input([_generated(sql)]))

        statement = output.result.statements[0]
        assert "reorder_predicates" not in statement.applied_rules

    async def test_already_equality_first_is_a_noop(self) -> None:
        agent = SqlOptimizationAgent()
        sql = (
            "SELECT MARKETID FROM STAGING.STAGING_TRANSACTIONS "
            "WHERE MARKETID = 'US' AND UNITS > 10"
        )
        output = await agent.run(_make_input([_generated(sql)]))

        statement = output.result.statements[0]
        assert "reorder_predicates" not in statement.applied_rules

    async def test_top_level_or_is_a_noop(self) -> None:
        agent = SqlOptimizationAgent()
        sql = (
            "SELECT MARKETID FROM STAGING.STAGING_TRANSACTIONS "
            "WHERE UNITS > 10 OR MARKETID = 'US'"
        )
        output = await agent.run(_make_input([_generated(sql)]))

        statement = output.result.statements[0]
        assert "reorder_predicates" not in statement.applied_rules
        # Untouched (aside from the audit comment/limit rules downstream).
        assert "WHERE UNITS > 10 OR MARKETID = 'US'" in statement.sql


class TestRowCountWarning:
    async def test_warning_fires_for_large_unfiltered_table(self) -> None:
        agent = SqlOptimizationAgent()
        sql = "SELECT MARKETID FROM STAGING.STAGING_TRANSACTIONS"
        output = await agent.run(
            _make_input(
                [_generated(sql, referenced_tables=["STAGING.STAGING_TRANSACTIONS"])],
                table_row_count_estimates={
                    "STAGING.STAGING_TRANSACTIONS": LARGE_TABLE_ROW_THRESHOLD + 1
                },
            )
        )

        assert len(output.result.warnings) == 1
        assert "STAGING.STAGING_TRANSACTIONS" in output.result.warnings[0]

    async def test_warning_does_not_fire_when_where_clause_present(self) -> None:
        agent = SqlOptimizationAgent()
        sql = "SELECT MARKETID FROM STAGING.STAGING_TRANSACTIONS WHERE MARKETID = 'US'"
        output = await agent.run(
            _make_input(
                [_generated(sql, referenced_tables=["STAGING.STAGING_TRANSACTIONS"])],
                table_row_count_estimates={
                    "STAGING.STAGING_TRANSACTIONS": LARGE_TABLE_ROW_THRESHOLD + 1
                },
            )
        )

        assert output.result.warnings == []

    async def test_warning_does_not_fire_when_below_threshold(self) -> None:
        agent = SqlOptimizationAgent()
        sql = "SELECT MARKETID FROM STAGING.STAGING_TRANSACTIONS"
        output = await agent.run(
            _make_input(
                [_generated(sql, referenced_tables=["STAGING.STAGING_TRANSACTIONS"])],
                table_row_count_estimates={"STAGING.STAGING_TRANSACTIONS": 100},
            )
        )

        assert output.result.warnings == []

    async def test_warning_does_not_fire_when_estimate_missing(self) -> None:
        agent = SqlOptimizationAgent()
        sql = "SELECT MARKETID FROM STAGING.STAGING_TRANSACTIONS"
        output = await agent.run(
            _make_input([_generated(sql, referenced_tables=["STAGING.STAGING_TRANSACTIONS"])])
        )

        assert output.result.warnings == []


class TestAppliedRulesAccuracy:
    async def test_applied_rules_reflects_only_rules_that_fired(self) -> None:
        agent = SqlOptimizationAgent()
        # No WHERE clause (reorder is a no-op), no existing LIMIT (inject
        # fires), audit comment always fires.
        sql = "SELECT MARKETID FROM STAGING.STAGING_TRANSACTIONS"
        output = await agent.run(_make_input([_generated(sql)]))

        statement = output.result.statements[0]
        assert statement.applied_rules == ["inject_limit", "audit_comment"]

    async def test_applied_rules_all_three_fire_together(self) -> None:
        agent = SqlOptimizationAgent()
        sql = (
            "SELECT MARKETID FROM STAGING.STAGING_TRANSACTIONS "
            "WHERE UNITS > 10 AND MARKETID = 'US'"
        )
        output = await agent.run(_make_input([_generated(sql)]))

        statement = output.result.statements[0]
        assert statement.applied_rules == [
            "reorder_predicates",
            "inject_limit",
            "audit_comment",
        ]


class TestOutputEnvelope:
    async def test_lineage_and_metadata_and_confidence(self) -> None:
        agent = SqlOptimizationAgent()
        sql = "SELECT MARKETID FROM STAGING.STAGING_TRANSACTIONS"
        output = await agent.run(_make_input([_generated(sql)]))

        assert output.confidence == 1.0
        assert output.errors == []
        assert len(output.lineage_events) == 1
        assert output.lineage_events[0].agent_name == "query.sql_optimization"
        assert output.lineage_events[0].tenant_id == "tenant-acme"
        assert output.lineage_events[0].trace_id == "trace-1"
        assert output.metadata.latency_ms >= 0
        assert output.metadata.model_version is None
        assert output.metadata.prompt_version is None
        assert output.metadata.tokens_input is None
        assert output.metadata.tokens_output is None

    async def test_multiple_statements_each_optimized_independently(self) -> None:
        agent = SqlOptimizationAgent()
        statements = [
            _generated("SELECT A FROM T1", data_source_id="ds-1"),
            _generated("SELECT B FROM T2 LIMIT 50", data_source_id="ds-2"),
        ]
        output = await agent.run(_make_input(statements))

        assert len(output.result.statements) == 2
        assert output.result.statements[0].data_source_id == "ds-1"
        assert "inject_limit" in output.result.statements[0].applied_rules
        assert output.result.statements[1].data_source_id == "ds-2"
        assert "inject_limit" not in output.result.statements[1].applied_rules
