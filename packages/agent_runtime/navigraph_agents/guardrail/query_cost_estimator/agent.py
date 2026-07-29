"""Query Cost/Row-Limit Estimator agent implementation.

Fully deterministic: no LLM call, no `prompts/` directory, no external
client dependency at all -- this agent is a pure function of its input,
exactly like `navigraph_agents.query.sql_optimization` and
`navigraph_agents.query.execution_planning`. For every `OptimizedSql`
statement it receives, it checks the statement's `estimated_row_count`
(populated, best-effort, by SQL Optimization) against the effective
per-role row limit for the calling `request_context.roles`, and either
passes the statement through unchanged into `approved` or turns it into a
non-recoverable `AgentError` in `rejected` -- mirroring Execution
Planning's "never both, no post-hoc filtering step" split for those two
lists. Unlike Execution Planning, though, this agent ALSO records exactly
one `CostEstimate` per input statement in `estimates` regardless of
outcome, since the estimate itself (not just the pass/fail verdict) is a
real audit artifact.

Cost/capacity control is a distinct concern from authorization: the
per-role row limits below are a real, conservative Python dict, not pushed
through OPA/Rego -- see DECISIONS.md. Policy Authorization (a sibling
Guardrail agent, built concurrently) is what enforces who may access what;
this agent only enforces how much a role may pull back in one query.

Follows the same structural pattern as
`navigraph_agents.understanding.intent_understanding.agent`: open an OTel
span, never raise, always emit a `LineageEvent` and `AgentMetadata` with
`latency_ms` populated.
"""

from __future__ import annotations

import time

from navigraph_shared.contracts import AgentError, AgentMetadata, LineageEvent
from navigraph_shared.telemetry import (
    get_tracer,
    record_agent_error,
    record_agent_invocation,
)
from opentelemetry.trace import Tracer

from navigraph_agents.guardrail.query_cost_estimator.contracts import (
    CostEstimate,
    OptimizedSql,
    QueryCostEstimatorInput,
    QueryCostEstimatorOutput,
    QueryCostEstimatorResult,
)

AGENT_NAME = "guardrail.query_cost_estimator"

# Real, conservative per-role row-limit table -- a Python dict, not pushed
# through OPA/Rego (cost/capacity policy is a distinct concern from
# authorization -- see DECISIONS.md). These exact numbers are placeholders
# pending real business-requirement confirmation, not derived from any
# stated requirement.
ROLE_ROW_LIMITS: dict[str, int] = {"analyst": 5_000, "pii_viewer": 5_000, "admin": 10_000}
DEFAULT_ROLE_ROW_LIMIT = 1_000
# Mirrors execution_planning.agent.MAX_ROWS_CAP -- duplicated, not
# imported (sibling-package convention). This agent's effective per-role
# limit is always <= this global cap, never looser than it.
MAX_ROWS_CAP = 10_000


def _effective_row_limit(roles: list[str]) -> int:
    """The row limit this call's roles are held to: the MOST PERMISSIVE of
    whatever `ROLE_ROW_LIMITS` entries apply, capped at `MAX_ROWS_CAP`.

    A caller with multiple roles (e.g. `["analyst", "admin"]`) is treated
    as "has any of these roles" -- the admin limit wins over the analyst
    one -- which is the sensible reading of a multi-role principal, not a
    security gap: this table is a cost-control convenience, not an
    authorization boundary (that's Policy Authorization's job). An empty
    `roles` list, or a list of roles with no entry in `ROLE_ROW_LIMITS`,
    falls back to `DEFAULT_ROLE_ROW_LIMIT`.
    """

    role_limits = (ROLE_ROW_LIMITS.get(role, DEFAULT_ROLE_ROW_LIMIT) for role in roles)
    most_permissive = max(role_limits, default=DEFAULT_ROLE_ROW_LIMIT)
    return min(most_permissive, MAX_ROWS_CAP)


class QueryCostEstimatorAgent:
    """Checks each optimized statement's estimated row count against the
    caller's effective per-role row limit. Pure function of its input --
    no external client dependency."""

    def __init__(self, tracer: Tracer | None = None) -> None:
        self._tracer = tracer or get_tracer("navigraph-agent-runtime")

    async def run(self, input: QueryCostEstimatorInput) -> QueryCostEstimatorOutput:
        start = time.perf_counter()
        request_context = input.request_context
        payload = input.payload

        with self._tracer.start_as_current_span("agent.query_cost_estimator.run") as span:
            span.set_attribute("navigraph.tenant_id", request_context.tenant_id)
            span.set_attribute("navigraph.trace_id", request_context.trace_id)
            span.set_attribute("navigraph.agent_name", AGENT_NAME)

            effective_limit = _effective_row_limit(request_context.roles)

            approved, estimates, rejected = self._estimate_statements(
                payload.statements, effective_limit=effective_limit, roles=request_context.roles
            )

            result = QueryCostEstimatorResult(
                approved=approved,
                estimates=estimates,
                rejected=rejected,
            )

            confidence = 0.0 if rejected else 1.0

            lineage_event = LineageEvent(
                agent_name=AGENT_NAME,
                input_summary=f"statements={len(payload.statements)}",
                output_summary=(
                    f"approved={len(approved)} rejected={len(rejected)} "
                    f"effective_limit={effective_limit}"
                ),
                tenant_id=request_context.tenant_id,
                trace_id=request_context.trace_id,
            )

            latency_ms = (time.perf_counter() - start) * 1000.0
            metadata = AgentMetadata(latency_ms=latency_ms)

            span.set_attribute("navigraph.approved_count", len(approved))
            span.set_attribute("navigraph.rejected_count", len(rejected))
            span.set_attribute("navigraph.effective_row_limit", effective_limit)

        record_agent_invocation(AGENT_NAME, latency_ms=latency_ms, success=not rejected)
        for error in rejected:
            record_agent_error(AGENT_NAME, error_code=error.code, recoverable=error.recoverable)

        return QueryCostEstimatorOutput(
            result=result,
            confidence=confidence,
            lineage_events=[lineage_event],
            errors=[],
            metadata=metadata,
        )

    @staticmethod
    def _estimate_statements(
        statements: list[OptimizedSql],
        *,
        effective_limit: int,
        roles: list[str],
    ) -> tuple[list[OptimizedSql], list[CostEstimate], list[AgentError]]:
        approved: list[OptimizedSql] = []
        estimates: list[CostEstimate] = []
        rejected: list[AgentError] = []

        for statement in statements:
            estimated_row_count = statement.estimated_row_count

            # Can't-estimate is a real, honestly-flagged limitation -- SQL
            # Optimization doesn't always populate a row estimate -- not
            # itself treated as a security violation, so it passes through.
            if estimated_row_count is None:
                within_limit = True
            else:
                within_limit = estimated_row_count <= effective_limit

            estimates.append(
                CostEstimate(
                    data_source_id=statement.data_source_id,
                    estimated_row_count=estimated_row_count,
                    role_row_limit=effective_limit,
                    within_limit=within_limit,
                )
            )

            if within_limit:
                approved.append(statement)
                continue

            # Structurally cannot also become approved: this branch never
            # touches `approved`.
            rejected.append(
                AgentError(
                    code="row_limit_exceeded",
                    message=(
                        f"estimated {estimated_row_count} rows exceeds the "
                        f"{effective_limit}-row limit for roles {roles}"
                    ),
                    recoverable=False,
                )
            )

        return approved, estimates, rejected
