"""Contracts for the Query Cost/Row-Limit Estimator agent.

Follows the same pattern as
`navigraph_agents.understanding.intent_understanding.contracts`: a small
payload model, an `AgentInput` subclass wrapping it plus a
`request_context`, a result model, and an `AgentOutput` subclass wrapping
that result. Fully deterministic -- no LLM call, no `prompts/` directory,
no external client dependency at all: this agent is a pure function of its
input, exactly like `navigraph_agents.understanding.schema_mapping` and
`navigraph_agents.query.sql_optimization`.

A note on the local `OptimizedSql` type below: it mirrors the shape of
`navigraph_agents.query.sql_optimization.contracts.OptimizedSql`, a type
owned by the sibling SQL Optimization agent package. It is duplicated here
on purpose, not imported across the package boundary -- see
`navigraph_agents.understanding.schema_mapping.contracts.CatalogInventoryEntry`'s
docstring for the full rationale: agent-to-agent contract dependencies
should go through the Coordinator's data flow, not direct Python imports
between sibling agent packages, so no agent package should ever need to
import another agent package's contracts module directly.
"""

from __future__ import annotations

from typing import Any

from navigraph_shared.contracts import AgentError, AgentInput, AgentOutput
from pydantic import BaseModel, ConfigDict, Field


class OptimizedSql(BaseModel):
    """Mirrors sql_optimization.contracts.OptimizedSql field-for-field --
    duplicated rather than cross-imported, per the established
    sibling-package convention (see
    schema_mapping.contracts.CatalogInventoryEntry's docstring for the
    full rationale)."""

    model_config = ConfigDict(extra="forbid")

    data_source_id: str
    sql: str
    params: dict[str, Any] = Field(default_factory=dict)
    applied_rules: list[str]
    estimated_row_count: int | None = None


class QueryCostEstimatorPayload(BaseModel):
    """The optimized statements SQL Optimization produced, whose row-count
    estimates (if any) this agent checks against the caller's per-role row
    limit."""

    model_config = ConfigDict(extra="forbid")

    statements: list[OptimizedSql]


class QueryCostEstimatorInput(AgentInput):
    """Input contract for the Query Cost/Row-Limit Estimator agent.

    Inherits the mandatory, non-optional `request_context: RequestContext`
    field from `AgentInput` -- `request_context.roles` is load-bearing here
    since it drives which per-role row limit applies.
    """

    payload: QueryCostEstimatorPayload


class CostEstimate(BaseModel):
    """One statement's cost-estimate outcome: the row estimate it carried
    in, the effective per-role limit it was checked against, and whether
    it fell within that limit. Produced for every statement regardless of
    outcome -- an audit record, not just a pass/fail signal."""

    model_config = ConfigDict(extra="forbid")

    data_source_id: str
    estimated_row_count: int | None
    role_row_limit: int
    within_limit: bool


class QueryCostEstimatorResult(BaseModel):
    """The statements cleared for execution, one `CostEstimate` per input
    statement (for audit, regardless of outcome), and any statement
    rejected for exceeding its effective role row limit."""

    model_config = ConfigDict(extra="forbid")

    approved: list[OptimizedSql]
    estimates: list[CostEstimate]
    rejected: list[AgentError] = Field(default_factory=list)
    # Reserved for a future policy-driven per-role/per-intent limit variant
    # (mirrors CachingPayload.policy_version's "populate an existing field
    # later, not a redesign" precedent) -- always "v1" today, this agent
    # never varies it.
    cost_policy_version: str = "v1"


class QueryCostEstimatorOutput(AgentOutput):
    """Output contract for the Query Cost/Row-Limit Estimator agent."""

    result: QueryCostEstimatorResult
