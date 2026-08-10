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

TENANT-CONFIGURABLE THRESHOLDS (Phase 5 of the configurable-platform
build plan): `session_factory`, if given, lets a tenant override
`ROLE_ROW_LIMITS`/`DEFAULT_ROLE_ROW_LIMIT`/`MAX_ROWS_CAP` via a real
`TenantGuardrailConfig` row -- additive-only, fails SAFE (not closed) to
these exact hardcoded defaults for any tenant with no row, any field left
`NULL` in that row, or if the lookup itself fails for any reason (no
`session_factory` given, the catalog unreachable). This agent is
constructed once, at agent-runtime startup, and reused for every request
(see `main.py`/`RequestOrchestratorAgent.__init__`) -- so the override
lookup happens fresh inside `run()`, per request, cached per tenant with
a TTL, the same "singleton agent, live per-request resolution" shape
`navigraph_gateway.identity.TenantVerifierResolver` already established
for Phase 4's per-tenant identity verifier.
"""

from __future__ import annotations

import time

from navigraph_catalog.db import session_scope
from navigraph_shared.contracts import AgentError, AgentMetadata, LineageEvent
from navigraph_shared.telemetry import (
    get_tracer,
    record_agent_error,
    record_agent_invocation,
)
from opentelemetry.trace import Tracer
from sqlalchemy.orm import Session, sessionmaker

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
# stated requirement. A tenant may override these via a real
# `TenantGuardrailConfig` row (see the module docstring's "TENANT-
# CONFIGURABLE THRESHOLDS" note) -- these three names stay real, mutable
# module globals (not, e.g., wrapped in a settings object) specifically so
# `_effective_row_limit`'s own default parameter values keep tracking
# them by reference; existing tests rely on mutating these in place.
ROLE_ROW_LIMITS: dict[str, int] = {"analyst": 5_000, "pii_viewer": 5_000, "admin": 10_000}
DEFAULT_ROLE_ROW_LIMIT = 1_000
# Mirrors execution_planning.agent.MAX_ROWS_CAP -- duplicated, not
# imported (sibling-package convention). This agent's effective per-role
# limit is always <= this global cap, never looser than it.
MAX_ROWS_CAP = 10_000

# TTL for the per-tenant threshold-override cache -- mirrors
# `TenantVerifierResolver`'s identical rationale: a live catalog query on
# every single request would be a real, needless cost for a value that
# changes at most as often as an operator runs `navigraph_admin.py
# guardrail set-thresholds`.
_GUARDRAIL_CONFIG_CACHE_TTL_SECONDS = 300.0


def _effective_row_limit(
    roles: list[str],
    *,
    role_row_limits: dict[str, int] = ROLE_ROW_LIMITS,
    default_role_row_limit: int = DEFAULT_ROLE_ROW_LIMIT,
    max_rows_cap: int = MAX_ROWS_CAP,
) -> int:
    """The row limit this call's roles are held to: the MOST PERMISSIVE of
    whatever `role_row_limits` entries apply, capped at `max_rows_cap`.

    A caller with multiple roles (e.g. `["analyst", "admin"]`) is treated
    as "has any of these roles" -- the admin limit wins over the analyst
    one -- which is the sensible reading of a multi-role principal, not a
    security gap: this table is a cost-control convenience, not an
    authorization boundary (that's Policy Authorization's job). An empty
    `roles` list, or a list of roles with no entry in `role_row_limits`,
    falls back to `default_role_row_limit`.

    `role_row_limits`/`default_role_row_limit`/`max_rows_cap` default to
    the module-level globals of the same name -- callers with no tenant
    override (or no `session_factory` at all) get today's exact,
    unchanged behavior by simply not passing these.
    """

    role_limits = (role_row_limits.get(role, default_role_row_limit) for role in roles)
    most_permissive = max(role_limits, default=default_role_row_limit)
    return min(most_permissive, max_rows_cap)


class QueryCostEstimatorAgent:
    """Checks each optimized statement's estimated row count against the
    caller's effective per-role row limit. No external client dependency
    UNLESS `session_factory` is given, in which case it looks up (and
    caches) each tenant's threshold overrides -- see the module
    docstring."""

    def __init__(
        self,
        tracer: Tracer | None = None,
        *,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        self._tracer = tracer or get_tracer("navigraph-agent-runtime")
        self._session_factory = session_factory
        self._guardrail_config_cache: dict[str, tuple[tuple[dict, int, int], float]] = {}

    def _resolve_thresholds(self, tenant_id: str) -> tuple[dict[str, int], int, int]:
        """Returns `(role_row_limits, default_role_row_limit, max_rows_cap)`
        for `tenant_id` -- its real override, PARTIALLY merged over the
        hardcoded defaults (a tenant overriding just one role doesn't
        need to repeat every other role's default), or the hardcoded
        defaults verbatim if this tenant has none, any lookup step fails,
        or no `session_factory` was given at all. Fails SAFE, never
        closed -- a catalog outage must never change query-cost
        enforcement, only fall back to already-shipped, global defaults.
        Cached per tenant with a TTL; see `_GUARDRAIL_CONFIG_CACHE_TTL_SECONDS`.
        """

        defaults = (ROLE_ROW_LIMITS, DEFAULT_ROLE_ROW_LIMIT, MAX_ROWS_CAP)
        if self._session_factory is None:
            return defaults

        now = time.monotonic()
        cached = self._guardrail_config_cache.get(tenant_id)
        if cached is not None and now < cached[1]:
            return cached[0]

        resolved = defaults
        try:
            from navigraph_catalog.api import get_tenant_guardrail_config

            with session_scope(self._session_factory) as session:
                config = get_tenant_guardrail_config(session, tenant_id=tenant_id)
            if config is not None:
                resolved = (
                    {**ROLE_ROW_LIMITS, **(config.role_row_limits or {})},
                    config.default_role_row_limit or DEFAULT_ROLE_ROW_LIMIT,
                    config.max_rows_cap or MAX_ROWS_CAP,
                )
        except Exception:  # noqa: BLE001 -- deliberately blind, see this method's "fails SAFE" docstring note
            resolved = defaults

        self._guardrail_config_cache[tenant_id] = (
            resolved,
            now + _GUARDRAIL_CONFIG_CACHE_TTL_SECONDS,
        )
        return resolved

    async def run(self, input: QueryCostEstimatorInput) -> QueryCostEstimatorOutput:
        start = time.perf_counter()
        request_context = input.request_context
        payload = input.payload

        with self._tracer.start_as_current_span("agent.query_cost_estimator.run") as span:
            span.set_attribute("navigraph.tenant_id", request_context.tenant_id)
            span.set_attribute("navigraph.trace_id", request_context.trace_id)
            span.set_attribute("navigraph.agent_name", AGENT_NAME)

            role_row_limits, default_role_row_limit, max_rows_cap = self._resolve_thresholds(
                request_context.tenant_id
            )
            effective_limit = _effective_row_limit(
                request_context.roles,
                role_row_limits=role_row_limits,
                default_role_row_limit=default_role_row_limit,
                max_rows_cap=max_rows_cap,
            )

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
