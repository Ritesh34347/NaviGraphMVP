"""Real unit tests for the Execution Planning agent.

No mocking needed -- the agent is a pure function of its input, so these
are real end-to-end tests of `ExecutionPlanningAgent.run` against
constructed `ExecutionPlanningInput` payloads. This is the single most
important test file in this phase: the whole point of this agent is that
unsafe SQL structurally cannot reach `ExecutionPlanningResult.plans`, so
the injection-rejection tests below assert on `plans` being empty (and
`rejected` being populated) directly, not just on "some error occurred".

`asyncio_mode = "auto"` is set at the workspace root
`packages/pyproject.toml`, so `async def test_...` functions run without
an explicit `@pytest.mark.asyncio` decorator.
"""

from __future__ import annotations

from navigraph_shared.contracts import RequestContext

from navigraph_agents.query.execution_planning.agent import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_ROWS_CAP,
    ExecutionPlanningAgent,
)
from navigraph_agents.query.execution_planning.contracts import (
    ExecutionPlanningInput,
    ExecutionPlanningPayload,
    OptimizedSql,
)


def _request_context() -> RequestContext:
    return RequestContext(
        tenant_id="tenant-acme",
        user_id="user-1",
        trace_id="trace-1",
        roles=["analyst"],
    )


def _make_input(statements: list[OptimizedSql]) -> ExecutionPlanningInput:
    return ExecutionPlanningInput(
        request_context=_request_context(),
        payload=ExecutionPlanningPayload(statements=statements),
    )


def _optimized(
    sql: str,
    data_source_id: str = "ds-1",
    applied_rules: list[str] | None = None,
) -> OptimizedSql:
    return OptimizedSql(
        data_source_id=data_source_id,
        sql=sql,
        params={},
        applied_rules=applied_rules or ["audit_comment"],
        estimated_row_count=None,
    )


def _with_audit_comment(sql: str, trace_id: str = "trace-1", tenant_id: str = "tenant-acme") -> str:
    return f"-- navigraph trace_id={trace_id} tenant_id={tenant_id}\n{sql}"


class TestLegitimateStatementsAccepted:
    async def test_legitimate_single_table_select_passes_with_correct_plan_fields(self) -> None:
        agent = ExecutionPlanningAgent()
        sql = _with_audit_comment(
            "SELECT MARKETID, SUM(UNITS) AS UNITS_TOTAL FROM STAGING.STAGING_TRANSACTIONS "
            "GROUP BY MARKETID\nLIMIT 10000"
        )
        output = await agent.run(_make_input([_optimized(sql, data_source_id="ds-snowflake-1")]))

        assert output.result.rejected == []
        assert len(output.result.plans) == 1

        plan = output.result.plans[0]
        assert plan.data_source_id == "ds-snowflake-1"
        assert plan.route == "direct_connector"
        assert plan.sql == sql
        assert plan.timeout_seconds == DEFAULT_TIMEOUT_SECONDS
        assert plan.max_rows == 10000
        assert plan.read_only_verified is True
        assert output.result.requires_cross_source_join is False
        assert output.confidence == 1.0

    async def test_legitimate_multi_table_select_with_join_passes(self) -> None:
        agent = ExecutionPlanningAgent()
        sql = _with_audit_comment(
            "SELECT t.MARKETID, c.RISKLEVEL FROM STAGING.STAGING_TRANSACTIONS t "
            "JOIN STAGING.CUSTOMER_INFORMATION c ON t.CUSTOMERID = c.CUSTOMERID "
            "LIMIT 10000"
        )
        output = await agent.run(_make_input([_optimized(sql, data_source_id="ds-1")]))

        assert output.result.rejected == []
        assert len(output.result.plans) == 1
        assert output.result.plans[0].read_only_verified is True

    async def test_with_cte_select_is_accepted(self) -> None:
        agent = ExecutionPlanningAgent()
        sql = _with_audit_comment(
            "WITH cte AS (SELECT MARKETID, UNITS FROM STAGING.STAGING_TRANSACTIONS) "
            "SELECT * FROM cte LIMIT 10000"
        )
        output = await agent.run(_make_input([_optimized(sql)]))

        assert output.result.rejected == []
        assert len(output.result.plans) == 1
        assert output.result.plans[0].sql == sql

    async def test_audit_comment_is_stripped_before_validation_not_causing_false_rejection(
        self,
    ) -> None:
        # If the audit comment weren't stripped, the first "keyword" found
        # would be gibberish and this would be wrongly rejected.
        agent = ExecutionPlanningAgent()
        sql = _with_audit_comment("SELECT 1 FROM T LIMIT 10")
        assert sql.startswith("-- navigraph")

        output = await agent.run(_make_input([_optimized(sql)]))

        assert output.result.rejected == []
        assert len(output.result.plans) == 1


class TestInjectionRejection:
    """The core safety-gate tests: SQL that must never become an
    ExecutionPlan."""

    async def test_stacked_query_injection_is_rejected_and_never_appears_in_plans(self) -> None:
        agent = ExecutionPlanningAgent()
        sql = _with_audit_comment("SELECT * FROM T; DROP TABLE T")

        output = await agent.run(_make_input([_optimized(sql, data_source_id="ds-1")]))

        assert output.result.plans == []
        assert len(output.result.rejected) == 1
        rejection = output.result.rejected[0]
        assert rejection.code == "rejected_unsafe_statement"
        assert rejection.recoverable is False
        assert "ds-1" in rejection.message
        assert output.confidence == 0.0

    async def test_bare_delete_statement_is_rejected(self) -> None:
        agent = ExecutionPlanningAgent()
        sql = _with_audit_comment("DELETE FROM T")

        output = await agent.run(_make_input([_optimized(sql)]))

        assert output.result.plans == []
        assert len(output.result.rejected) == 1
        assert "DELETE" in output.result.rejected[0].message

    async def test_bare_insert_statement_is_rejected(self) -> None:
        agent = ExecutionPlanningAgent()
        sql = _with_audit_comment("INSERT INTO T VALUES (1)")

        output = await agent.run(_make_input([_optimized(sql)]))

        assert output.result.plans == []
        assert len(output.result.rejected) == 1
        assert "INSERT" in output.result.rejected[0].message

    async def test_bare_update_statement_is_rejected(self) -> None:
        agent = ExecutionPlanningAgent()
        sql = _with_audit_comment("UPDATE T SET X = 1")

        output = await agent.run(_make_input([_optimized(sql)]))

        assert output.result.plans == []
        assert len(output.result.rejected) == 1

    async def test_bare_create_statement_is_rejected(self) -> None:
        agent = ExecutionPlanningAgent()
        sql = _with_audit_comment("CREATE TABLE T (X INT)")

        output = await agent.run(_make_input([_optimized(sql)]))

        assert output.result.plans == []
        assert len(output.result.rejected) == 1

    async def test_bare_alter_statement_is_rejected(self) -> None:
        agent = ExecutionPlanningAgent()
        sql = _with_audit_comment("ALTER TABLE T ADD COLUMN Y INT")

        output = await agent.run(_make_input([_optimized(sql)]))

        assert output.result.plans == []
        assert len(output.result.rejected) == 1

    async def test_bare_grant_statement_is_rejected(self) -> None:
        agent = ExecutionPlanningAgent()
        sql = _with_audit_comment("GRANT SELECT ON T TO ROLE ANALYST")

        output = await agent.run(_make_input([_optimized(sql)]))

        assert output.result.plans == []
        assert len(output.result.rejected) == 1

    async def test_comment_that_looks_like_a_second_statement_is_not_rejected(self) -> None:
        # Everything after `--` on that line is a comment, not a second
        # statement -- this must NOT be rejected.
        agent = ExecutionPlanningAgent()
        sql = _with_audit_comment("SELECT * FROM T -- ; DROP TABLE")

        output = await agent.run(_make_input([_optimized(sql)]))

        assert output.result.rejected == []
        assert len(output.result.plans) == 1

    async def test_semicolon_inside_string_literal_is_not_mistaken_for_stacking(self) -> None:
        agent = ExecutionPlanningAgent()
        sql = _with_audit_comment("SELECT ';' AS separator FROM T")

        output = await agent.run(_make_input([_optimized(sql)]))

        assert output.result.rejected == []
        assert len(output.result.plans) == 1

    async def test_trailing_semicolon_with_only_whitespace_after_is_not_rejected(self) -> None:
        agent = ExecutionPlanningAgent()
        sql = _with_audit_comment("SELECT * FROM T;   \n")

        output = await agent.run(_make_input([_optimized(sql)]))

        assert output.result.rejected == []
        assert len(output.result.plans) == 1


class TestMaxRowsCapping:
    async def test_max_rows_caps_an_oversized_limit(self) -> None:
        agent = ExecutionPlanningAgent()
        sql = _with_audit_comment("SELECT * FROM T LIMIT 999999999")

        output = await agent.run(_make_input([_optimized(sql)]))

        assert len(output.result.plans) == 1
        assert output.result.plans[0].max_rows == MAX_ROWS_CAP

    async def test_max_rows_respects_a_limit_under_the_cap(self) -> None:
        agent = ExecutionPlanningAgent()
        sql = _with_audit_comment("SELECT * FROM T LIMIT 25")

        output = await agent.run(_make_input([_optimized(sql)]))

        assert output.result.plans[0].max_rows == 25

    async def test_max_rows_defaults_to_cap_when_no_limit_present(self) -> None:
        agent = ExecutionPlanningAgent()
        sql = _with_audit_comment("SELECT * FROM T")

        output = await agent.run(_make_input([_optimized(sql)]))

        assert output.result.plans[0].max_rows == MAX_ROWS_CAP


class TestCrossSourceJoin:
    async def test_requires_cross_source_join_true_with_two_distinct_data_sources(self) -> None:
        agent = ExecutionPlanningAgent()
        statements = [
            _optimized(_with_audit_comment("SELECT * FROM T1 LIMIT 10"), data_source_id="ds-1"),
            _optimized(_with_audit_comment("SELECT * FROM T2 LIMIT 10"), data_source_id="ds-2"),
        ]

        output = await agent.run(_make_input(statements))

        assert len(output.result.plans) == 2
        assert output.result.requires_cross_source_join is True

    async def test_requires_cross_source_join_false_with_one_data_source(self) -> None:
        agent = ExecutionPlanningAgent()
        statements = [
            _optimized(_with_audit_comment("SELECT * FROM T1 LIMIT 10"), data_source_id="ds-1"),
            _optimized(_with_audit_comment("SELECT * FROM T2 LIMIT 10"), data_source_id="ds-1"),
        ]

        output = await agent.run(_make_input(statements))

        assert len(output.result.plans) == 2
        assert output.result.requires_cross_source_join is False

    async def test_requires_cross_source_join_false_when_rejected_statements_dont_count(
        self,
    ) -> None:
        # A rejected statement's data_source_id must not count toward the
        # cross-source-join calculation, since it never becomes a plan.
        agent = ExecutionPlanningAgent()
        statements = [
            _optimized(_with_audit_comment("SELECT * FROM T1 LIMIT 10"), data_source_id="ds-1"),
            _optimized(_with_audit_comment("DELETE FROM T2"), data_source_id="ds-2"),
        ]

        output = await agent.run(_make_input(statements))

        assert len(output.result.plans) == 1
        assert len(output.result.rejected) == 1
        assert output.result.requires_cross_source_join is False


class TestMixedBatch:
    async def test_one_rejected_and_one_accepted_in_same_batch(self) -> None:
        agent = ExecutionPlanningAgent()
        good_sql = _with_audit_comment("SELECT * FROM T1 LIMIT 10")
        bad_sql = _with_audit_comment("SELECT * FROM T2; DROP TABLE T2")
        statements = [
            _optimized(good_sql, data_source_id="ds-good"),
            _optimized(bad_sql, data_source_id="ds-bad"),
        ]

        output = await agent.run(_make_input(statements))

        assert len(output.result.plans) == 1
        assert output.result.plans[0].data_source_id == "ds-good"
        assert len(output.result.rejected) == 1
        assert "ds-bad" in output.result.rejected[0].message
        assert output.confidence == 0.0


class TestOutputEnvelope:
    async def test_lineage_and_metadata(self) -> None:
        agent = ExecutionPlanningAgent()
        sql = _with_audit_comment("SELECT * FROM T LIMIT 10")

        output = await agent.run(_make_input([_optimized(sql)]))

        assert output.errors == []
        assert len(output.lineage_events) == 1
        assert output.lineage_events[0].agent_name == "query.execution_planning"
        assert output.lineage_events[0].tenant_id == "tenant-acme"
        assert output.lineage_events[0].trace_id == "trace-1"
        assert output.metadata.latency_ms >= 0
        assert output.metadata.model_version is None
