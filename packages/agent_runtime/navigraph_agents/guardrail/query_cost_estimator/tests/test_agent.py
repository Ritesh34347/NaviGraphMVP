"""Real unit tests for the Query Cost/Row-Limit Estimator agent.

No mocking needed -- the agent is a pure function of its input, so these
are real end-to-end tests of `QueryCostEstimatorAgent.run` against
constructed `QueryCostEstimatorInput` payloads. `asyncio_mode = "auto"` is
set at the workspace root `packages/pyproject.toml`, so `async def
test_...` functions run without an explicit `@pytest.mark.asyncio`
decorator.
"""

from __future__ import annotations

from navigraph_shared.contracts import RequestContext

from navigraph_agents.guardrail.query_cost_estimator.agent import (
    DEFAULT_ROLE_ROW_LIMIT,
    MAX_ROWS_CAP,
    ROLE_ROW_LIMITS,
    QueryCostEstimatorAgent,
)
from navigraph_agents.guardrail.query_cost_estimator.contracts import (
    OptimizedSql,
    QueryCostEstimatorInput,
    QueryCostEstimatorPayload,
)


def _request_context(roles: list[str]) -> RequestContext:
    return RequestContext(
        tenant_id="tenant-acme",
        user_id="user-1",
        trace_id="trace-1",
        roles=roles,
    )


def _make_input(
    statements: list[OptimizedSql],
    roles: list[str],
) -> QueryCostEstimatorInput:
    return QueryCostEstimatorInput(
        request_context=_request_context(roles),
        payload=QueryCostEstimatorPayload(statements=statements),
    )


def _optimized(
    estimated_row_count: int | None,
    data_source_id: str = "ds-1",
) -> OptimizedSql:
    return OptimizedSql(
        data_source_id=data_source_id,
        sql="SELECT 1 FROM T LIMIT 10",
        params={},
        applied_rules=["inject_limit", "audit_comment"],
        estimated_row_count=estimated_row_count,
    )


class TestUnestimable:
    async def test_none_estimate_is_approved(self) -> None:
        agent = QueryCostEstimatorAgent()
        output = await agent.run(_make_input([_optimized(None)], roles=["analyst"]))

        assert output.result.approved == [_optimized(None)]
        assert output.result.rejected == []
        assert len(output.result.estimates) == 1
        assert output.result.estimates[0].within_limit is True
        assert output.result.estimates[0].estimated_row_count is None


class TestWithinLimit:
    async def test_well_within_role_limit_is_approved(self) -> None:
        agent = QueryCostEstimatorAgent()
        output = await agent.run(_make_input([_optimized(10)], roles=["analyst"]))

        assert len(output.result.approved) == 1
        assert output.result.rejected == []
        estimate = output.result.estimates[0]
        assert estimate.within_limit is True
        assert estimate.role_row_limit == ROLE_ROW_LIMITS["analyst"]


class TestOverLimit:
    async def test_over_analyst_limit_is_rejected(self) -> None:
        agent = QueryCostEstimatorAgent()
        over_limit = ROLE_ROW_LIMITS["analyst"] + 1
        output = await agent.run(_make_input([_optimized(over_limit)], roles=["analyst"]))

        assert output.result.approved == []
        assert len(output.result.rejected) == 1
        error = output.result.rejected[0]
        assert error.code == "row_limit_exceeded"
        assert error.recoverable is False

        estimate = output.result.estimates[0]
        assert estimate.within_limit is False
        assert estimate.estimated_row_count == over_limit
        assert estimate.role_row_limit == ROLE_ROW_LIMITS["analyst"]

    async def test_confidence_is_zero_when_anything_rejected(self) -> None:
        agent = QueryCostEstimatorAgent()
        over_limit = ROLE_ROW_LIMITS["analyst"] + 1
        output = await agent.run(_make_input([_optimized(over_limit)], roles=["analyst"]))

        assert output.confidence == 0.0


class TestEmptyRoles:
    async def test_empty_roles_uses_default_limit(self) -> None:
        agent = QueryCostEstimatorAgent()
        just_over_default = DEFAULT_ROLE_ROW_LIMIT + 1
        output = await agent.run(_make_input([_optimized(just_over_default)], roles=[]))

        assert output.result.estimates[0].role_row_limit == DEFAULT_ROLE_ROW_LIMIT
        assert output.result.estimates[0].within_limit is False
        assert output.result.rejected[0].code == "row_limit_exceeded"

    async def test_empty_roles_within_default_limit_is_approved(self) -> None:
        agent = QueryCostEstimatorAgent()
        output = await agent.run(_make_input([_optimized(DEFAULT_ROLE_ROW_LIMIT)], roles=[]))

        assert output.result.estimates[0].within_limit is True
        assert len(output.result.approved) == 1


class TestMultiRoleMostPermissive:
    async def test_multiple_roles_uses_the_most_permissive_limit(self) -> None:
        agent = QueryCostEstimatorAgent()
        # Between the analyst limit (5_000) and the admin limit (10_000):
        # rejected under analyst-only, approved once admin is also present.
        between = ROLE_ROW_LIMITS["analyst"] + 1
        assert between <= ROLE_ROW_LIMITS["admin"]

        analyst_only = await agent.run(_make_input([_optimized(between)], roles=["analyst"]))
        assert analyst_only.result.estimates[0].within_limit is False

        analyst_and_admin = await agent.run(
            _make_input([_optimized(between)], roles=["analyst", "admin"])
        )
        assert analyst_and_admin.result.estimates[0].within_limit is True
        assert analyst_and_admin.result.estimates[0].role_row_limit == ROLE_ROW_LIMITS["admin"]
        assert len(analyst_and_admin.result.approved) == 1


class TestMaxRowsCapIsAHardCeiling:
    def test_no_configured_role_limit_exceeds_the_global_cap(self) -> None:
        # Documents the invariant the module docstring/comments describe:
        # every entry in ROLE_ROW_LIMITS is already <= MAX_ROWS_CAP today.
        assert all(limit <= MAX_ROWS_CAP for limit in ROLE_ROW_LIMITS.values())

    async def test_hypothetical_high_role_limit_is_still_capped(self) -> None:
        # Simulates a role whose configured limit would exceed the global
        # cap by monkeypatching the module-level table for this test only,
        # to prove `_effective_row_limit` clamps to MAX_ROWS_CAP rather than
        # trusting ROLE_ROW_LIMITS blindly.
        import navigraph_agents.guardrail.query_cost_estimator.agent as agent_module

        original_limits = dict(agent_module.ROLE_ROW_LIMITS)
        agent_module.ROLE_ROW_LIMITS["super_admin"] = MAX_ROWS_CAP + 5_000
        try:
            agent = QueryCostEstimatorAgent()
            output = await agent.run(
                _make_input([_optimized(MAX_ROWS_CAP + 1)], roles=["super_admin"])
            )
            estimate = output.result.estimates[0]
            assert estimate.role_row_limit == MAX_ROWS_CAP
            assert estimate.within_limit is False
        finally:
            agent_module.ROLE_ROW_LIMITS.clear()
            agent_module.ROLE_ROW_LIMITS.update(original_limits)


class TestEveryStatementGetsAnEstimate:
    async def test_approved_and_rejected_statements_both_get_estimates(self) -> None:
        agent = QueryCostEstimatorAgent()
        over_limit = ROLE_ROW_LIMITS["analyst"] + 1
        statements = [
            _optimized(10, data_source_id="ds-approved"),
            _optimized(over_limit, data_source_id="ds-rejected"),
            _optimized(None, data_source_id="ds-unestimable"),
        ]
        output = await agent.run(_make_input(statements, roles=["analyst"]))

        assert len(output.result.estimates) == 3
        assert len(output.result.approved) == 2
        assert len(output.result.rejected) == 1

        estimate_by_source = {e.data_source_id: e for e in output.result.estimates}
        assert estimate_by_source["ds-approved"].within_limit is True
        assert estimate_by_source["ds-rejected"].within_limit is False
        assert estimate_by_source["ds-unestimable"].within_limit is True


class TestOutputEnvelope:
    async def test_lineage_and_metadata(self) -> None:
        agent = QueryCostEstimatorAgent()
        output = await agent.run(_make_input([_optimized(10)], roles=["analyst"]))

        assert output.errors == []
        assert len(output.lineage_events) == 1
        assert output.lineage_events[0].agent_name == "guardrail.query_cost_estimator"
        assert output.lineage_events[0].tenant_id == "tenant-acme"
        assert output.lineage_events[0].trace_id == "trace-1"
        assert output.metadata.latency_ms >= 0
        assert output.result.cost_policy_version == "v1"
