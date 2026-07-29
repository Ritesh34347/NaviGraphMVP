"""Contracts for the Execution Planning agent.

Follows the same pattern as
`navigraph_agents.understanding.intent_understanding.contracts`: a small
payload model, an `AgentInput` subclass wrapping it plus a
`request_context`, a result model, and an `AgentOutput` subclass wrapping
that result. Fully deterministic -- no LLM call, no `prompts/` directory,
no external client dependency at all: this agent is a pure function of its
input, exactly like `navigraph_agents.understanding.schema_mapping`.

A note on the local `OptimizedSql` type below: it mirrors the shape of
`navigraph_agents.query.sql_optimization.contracts.OptimizedSql`, a type
owned by the sibling SQL Optimization agent package built in this same
phase. It is duplicated here on purpose, not imported across the package
boundary -- see
`navigraph_agents.understanding.schema_mapping.contracts.CatalogInventoryEntry`'s
docstring for the full rationale: agent-to-agent contract dependencies
should go through the Coordinator's data flow, not direct Python imports
between sibling agent packages, so no agent package should ever need to
import another agent package's contracts module directly.
"""

from __future__ import annotations

from typing import Any, Literal

from navigraph_shared.contracts import AgentError, AgentInput, AgentOutput
from pydantic import BaseModel, ConfigDict, Field


class OptimizedSql(BaseModel):
    """Mirrors
    `navigraph_agents.query.sql_optimization.contracts.OptimizedSql`
    field-for-field -- duplicated rather than cross-imported, see this
    module's docstring."""

    model_config = ConfigDict(extra="forbid")

    data_source_id: str
    sql: str
    params: dict[str, Any] = Field(default_factory=dict)
    applied_rules: list[str]
    estimated_row_count: int | None = None


class ExecutionPlanningPayload(BaseModel):
    """The optimized statements SQL Optimization produced."""

    model_config = ConfigDict(extra="forbid")

    statements: list[OptimizedSql]


class ExecutionPlanningInput(AgentInput):
    """Input contract for the Execution Planning agent.

    Inherits the mandatory, non-optional `request_context: RequestContext`
    field from `AgentInput`.
    """

    payload: ExecutionPlanningPayload


class ExecutionPlan(BaseModel):
    """A statement that passed the read-only-SELECT safety check, ready
    to be handed to an executor."""

    model_config = ConfigDict(extra="forbid")

    data_source_id: str
    # "trino" exists as a contract option for a future cross-source
    # federation routing phase, but this phase's confirmed default (and
    # only value this agent currently assigns) is "direct_connector" --
    # see agent.py's module docstring.
    route: Literal["direct_connector", "trino"]
    sql: str
    params: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int
    max_rows: int
    read_only_verified: bool


class ExecutionPlanningResult(BaseModel):
    """The plans this agent produced, whether any of them span more than
    one data source, and any statements that failed the safety check --
    a rejected statement never appears in `plans`, only in `rejected`."""

    model_config = ConfigDict(extra="forbid")

    plans: list[ExecutionPlan]
    requires_cross_source_join: bool
    rejected: list[AgentError] = Field(default_factory=list)


class ExecutionPlanningOutput(AgentOutput):
    """Output contract for the Execution Planning agent."""

    result: ExecutionPlanningResult
