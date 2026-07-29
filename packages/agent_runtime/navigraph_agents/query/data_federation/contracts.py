"""Contracts for the Data Federation agent.

Follows the same pattern as
`navigraph_agents.understanding.intent_understanding.contracts`: a small
payload model, an `AgentInput` subclass wrapping it plus a `request_context`,
a result model, and an `AgentOutput` subclass wrapping that result. Fully
deterministic -- no LLM call, no `prompts/` directory. Unlike
`navigraph_agents.understanding.schema_mapping` and
`navigraph_agents.query.execution_planning`, this agent DOES have an
external client dependency (a real `Connector` per data source, and a real
`navigraph_federation.trino_client.TrinoClient` for the Trino route) -- see
`agent.py`.

A note on the local `ExecutionPlan` type below: it mirrors the shape of
`navigraph_agents.query.execution_planning.contracts.ExecutionPlan`
field-for-field, a type owned by the sibling Execution Planning agent
package built in this same phase in parallel. It is duplicated here on
purpose, not imported across the package boundary -- see
`navigraph_agents.understanding.schema_mapping.contracts.CatalogInventoryEntry`'s
docstring for the full rationale: agent-to-agent contract dependencies
should go through the Coordinator's data flow, not direct Python imports
between sibling agent packages, so no agent package should ever need to
import another agent package's contracts module directly.
"""

from __future__ import annotations

from typing import Any, Literal

from navigraph_shared.contracts import AgentInput, AgentOutput
from pydantic import BaseModel, ConfigDict, Field


class ExecutionPlan(BaseModel):
    """Mirrors
    `navigraph_agents.query.execution_planning.contracts.ExecutionPlan`
    field-for-field -- duplicated rather than cross-imported, see this
    module's docstring."""

    model_config = ConfigDict(extra="forbid")

    data_source_id: str
    route: Literal["direct_connector", "trino"]
    sql: str
    params: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int
    max_rows: int
    read_only_verified: bool


class DataFederationPayload(BaseModel):
    """The execution plans Execution Planning produced, ready to run."""

    model_config = ConfigDict(extra="forbid")

    plans: list[ExecutionPlan]


class DataFederationInput(AgentInput):
    """Input contract for the Data Federation agent.

    Inherits the mandatory, non-optional `request_context: RequestContext`
    field from `AgentInput`.
    """

    payload: DataFederationPayload


class SourceQueryResult(BaseModel):
    """The real result of executing one `ExecutionPlan` against its data
    source, whichever route (`direct_connector` or `trino`) actually ran
    it."""

    model_config = ConfigDict(extra="forbid")

    data_source_id: str
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    route_used: Literal["direct_connector", "trino"]
    execution_latency_ms: float


class DataFederationResult(BaseModel):
    """Every source's individual result, plus the single combined result
    set a caller actually wants back."""

    model_config = ConfigDict(extra="forbid")

    per_source_results: list[SourceQueryResult]
    final_columns: list[str]
    final_rows: list[dict[str, Any]]
    final_row_count: int
    # True only if 2+ DISTINCT data sources were actually queried
    # successfully this run -- a single-source result (the only case
    # exercisable against a real backend today, see agent.py's module
    # docstring) is never "federated", even if multiple plans targeted the
    # same data source.
    federated: bool


class DataFederationOutput(AgentOutput):
    """Output contract for the Data Federation agent."""

    result: DataFederationResult
