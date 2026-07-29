"""Policy Authorization agent implementation.

Fully deterministic from this agent's own point of view: no LLM call, no
`prompts/` directory. For every `GeneratedSql` statement it receives, calls
out to the REAL policy engine (an injected `OpaClient`, evaluating
`navigraph/authz/decision` -- see `infra/opa/policies/authz.rego`) with a
real `input_document` built from the request's tenant/user/roles/claims,
the classified `intent`, and the statement's own
`data_source_id`/`referenced_tables`/`referenced_columns`. A statement OPA
allows is added to `result.authorized`; a statement OPA denies is never
added there, only to `result.rejected` (as a `policy_denied` error) -- this
mirrors `navigraph_agents.query.execution_planning.agent`'s plans/rejected
split exactly: a statement either passes through unchanged into one list,
or becomes an `AgentError` in the other, never both.

THE FAIL-CLOSED DESIGN (the single most important decision in this file,
read this before touching `_authorize_statements`): if `OpaClient.evaluate`
itself raises -- a real connection/timeout/HTTP failure talking to OPA, NOT
a policy denial -- that failure is caught exactly ONCE for the whole
`run()` call, not per statement. If OPA is unreachable for the first
statement in a batch, it will be unreachable for every other statement in
that same batch too; retrying per statement would just repeat the same
failure `len(statements)` times for no benefit, mirroring
`DataSourceDiscoveryAgent`'s "one error per distinct infra failure, not per
row" precedent. When that happens, `authorized` is reset to empty --
discarding any statements already authorized earlier in the same loop,
before the failure -- and exactly one
`AgentError(code="opa_unreachable", recoverable=False)` is appended to
`rejected` (any statement-level decisions/rejections already collected in
this same call are discarded too, for the same reason: once OPA itself is
confirmed unreachable, nothing decided against it so far in this call is
trustworthy, matching `DataSourceDiscoveryAgent._resolve_table_owners`'s
"no partial result is trustworthy once the lookup itself failed"
convention).

This is the deliberate OPPOSITE of `navigraph_agents.query.caching.agent.
CachingAgent`'s fail-OPEN convention (`cache_backend_unavailable`,
`recoverable=True`): a cache miss costs nothing security-wise -- the caller
just re-executes against the real data source or accepts a non-cached
result. Treating "the policy engine is unreachable" as an implicit allow
would silently disable tenant isolation and RBAC for every statement in
the batch. So where `CachingAgent` fails open, this agent MUST fail
closed: `authorized=[]`, never a partial or best-effort allow list.

Follows the same structural pattern as
`navigraph_agents.query.execution_planning.agent`: real OTel span, never
raises a Python exception out of `run()` itself (the OPA exception is
caught inside `_authorize_statements`), always emits a `LineageEvent` and
`AgentMetadata` with `latency_ms` populated.
"""

from __future__ import annotations

import time

from navigraph_shared.contracts import (
    AgentError,
    AgentMetadata,
    LineageEvent,
    RequestContext,
)
from navigraph_shared.opa import OpaClient
from navigraph_shared.telemetry import (
    get_tracer,
    record_agent_error,
    record_agent_invocation,
)
from opentelemetry.trace import Tracer

from navigraph_agents.guardrail.policy_authorization.contracts import (
    GeneratedSql,
    OpaDecision,
    PolicyAuthorizationInput,
    PolicyAuthorizationOutput,
    PolicyAuthorizationPayload,
    PolicyAuthorizationResult,
)

AGENT_NAME = "guardrail.policy_authorization"

# The Rego package + rule path this agent always evaluates against, exactly
# as it appears after `/v1/data/` in OPA's real HTTP Data API -- see
# `OpaClient.evaluate`'s docstring.
_OPA_PACKAGE_PATH = "navigraph/authz/decision"


class PolicyAuthorizationAgent:
    """Authorizes each generated statement against the real policy engine
    (OPA); a statement OPA denies -- or that OPA cannot even be reached
    for -- is routed to `rejected` and never becomes `authorized`."""

    def __init__(
        self,
        opa_client: OpaClient,
        tracer: Tracer | None = None,
    ) -> None:
        self._opa_client = opa_client
        self._tracer = tracer or get_tracer("navigraph-agent-runtime")

    async def run(self, input: PolicyAuthorizationInput) -> PolicyAuthorizationOutput:
        start = time.perf_counter()
        request_context = input.request_context
        payload = input.payload

        with self._tracer.start_as_current_span("agent.policy_authorization.run") as span:
            span.set_attribute("navigraph.tenant_id", request_context.tenant_id)
            span.set_attribute("navigraph.trace_id", request_context.trace_id)
            span.set_attribute("navigraph.agent_name", AGENT_NAME)

            authorized, decisions, rejected = await self._authorize_statements(
                payload, request_context
            )

            result = PolicyAuthorizationResult(
                authorized=authorized, decisions=decisions, rejected=rejected
            )

            # A rejected statement is always a non-recoverable finding by
            # construction (both `policy_denied` and `opa_unreachable` are
            # always `recoverable=False`), so confidence collapses straight
            # to 0.0 rather than a softer partial value -- mirrors
            # `ExecutionPlanningAgent`'s identical confidence rule.
            confidence = 0.0 if rejected else 1.0

            lineage_event = LineageEvent(
                agent_name=AGENT_NAME,
                input_summary=f"statements={len(payload.statements)} intent={payload.intent}",
                output_summary=(
                    f"authorized={len(authorized)} rejected={len(rejected)}"
                ),
                tenant_id=request_context.tenant_id,
                trace_id=request_context.trace_id,
            )

            latency_ms = (time.perf_counter() - start) * 1000.0
            metadata = AgentMetadata(latency_ms=latency_ms)

            span.set_attribute("navigraph.authorized_count", len(authorized))
            span.set_attribute("navigraph.rejected_count", len(rejected))

        record_agent_invocation(AGENT_NAME, latency_ms=latency_ms, success=not rejected)
        for error in rejected:
            record_agent_error(AGENT_NAME, error_code=error.code, recoverable=error.recoverable)

        return PolicyAuthorizationOutput(
            result=result,
            confidence=confidence,
            lineage_events=[lineage_event],
            errors=[],
            metadata=metadata,
        )

    async def _authorize_statements(
        self,
        payload: PolicyAuthorizationPayload,
        request_context: RequestContext,
    ) -> tuple[list[GeneratedSql], list[OpaDecision], list[AgentError]]:
        authorized: list[GeneratedSql] = []
        decisions: list[OpaDecision] = []
        rejected: list[AgentError] = []

        try:
            for statement in payload.statements:
                decision = await self._opa_client.evaluate(
                    package_path=_OPA_PACKAGE_PATH,
                    input_document={
                        "tenant_id": request_context.tenant_id,
                        "user_id": request_context.user_id,
                        "roles": request_context.roles,
                        "claims": request_context.claims,
                        "intent": payload.intent,
                        "data_source_id": statement.data_source_id,
                        "referenced_tables": statement.referenced_tables,
                        "referenced_columns": statement.referenced_columns,
                    },
                )

                if decision.allow:
                    authorized.append(statement)
                    decisions.append(
                        OpaDecision(
                            data_source_id=statement.data_source_id,
                            allow=True,
                            deny_reasons=[],
                        )
                    )
                else:
                    decisions.append(
                        OpaDecision(
                            data_source_id=statement.data_source_id,
                            allow=False,
                            deny_reasons=decision.deny_reasons,
                        )
                    )
                    rejected.append(
                        AgentError(
                            code="policy_denied",
                            message=(
                                f"OPA denied statement for "
                                f"data_source_id={statement.data_source_id}: "
                                f"{decision.deny_reasons}"
                            ),
                            recoverable=False,
                        )
                    )
        except Exception as exc:  # noqa: BLE001 - fail CLOSED, see module docstring
            # OPA itself is unreachable (connection/timeout/HTTP failure),
            # not a policy denial. Caught exactly once for the whole batch
            # -- discard EVERYTHING decided so far in this call (including
            # any statements already authorized, and any policy_denied
            # decisions already recorded) and fail closed: nothing proceeds.
            return (
                [],
                [],
                [
                    AgentError(
                        code="opa_unreachable",
                        message=f"OPA unreachable: {exc}",
                        recoverable=False,
                    )
                ],
            )

        return authorized, decisions, rejected
