"""Contracts for the Data Source Discovery agent.

Follows the same pattern as
`navigraph_agents.understanding.intent_understanding.contracts`: a small
payload model, an `AgentInput` subclass wrapping it plus a `request_context`,
a result model, and an `AgentOutput` subclass wrapping that result. Fully
deterministic -- no LLM call, no `prompts/` directory -- this agent is a
thin resolution + real-connectivity-probe layer over `navigraph_catalog.api`
and `navigraph_connectors`.

`DataSourceDiscoveryPayload.tables` mirrors
`navigraph_agents.understanding.schema_mapping.contracts.SchemaMappingResult.tables`
(bare table names, e.g. `"STAGING_TRANSACTIONS"`) -- duplicated here rather
than cross-imported, matching the established convention (see
`schema_mapping.contracts`'s module docstring): agent-to-agent contract
dependencies should go through a future Coordinator's data flow, not direct
Python imports between sibling agent packages.
"""

from __future__ import annotations

from navigraph_shared.contracts import AgentInput, AgentOutput
from pydantic import BaseModel, ConfigDict, Field


class DataSourceDiscoveryPayload(BaseModel):
    """The bare table names (verbatim from Schema Mapping's
    `SchemaMappingResult.tables`) to resolve to concrete data sources."""

    model_config = ConfigDict(extra="forbid")

    tables: list[str]


class DataSourceDiscoveryInput(AgentInput):
    """Input contract for the Data Source Discovery agent.

    Inherits the mandatory, non-optional `request_context: RequestContext`
    field from `AgentInput` -- this agent cannot be invoked without a
    tenant-scoped request context.
    """

    payload: DataSourceDiscoveryPayload


class ResolvedDataSource(BaseModel):
    """One requested table, resolved to the `DataSource` that owns it, plus
    that data source's real connectivity-probe outcome.

    One entry is produced per requested table even when several tables
    share the same data source -- see `agent.py`'s docstring for why the
    connectivity probe itself is still only performed once per distinct
    `data_source_id`, not once per table.
    """

    model_config = ConfigDict(extra="forbid")

    table_name: str
    data_source_id: str
    source_type: str
    reachable: bool
    connection_test_latency_ms: float | None = None
    connection_test_message: str | None = None


class DataSourceDiscoveryResult(BaseModel):
    """Every requested table resolved to a data source (with its
    connectivity outcome), whether more than one distinct data source was
    involved, and any requested table that could not be resolved at all."""

    model_config = ConfigDict(extra="forbid")

    resolved: list[ResolvedDataSource]
    is_multi_source: bool
    unresolved_tables: list[str] = Field(default_factory=list)


class DataSourceDiscoveryOutput(AgentOutput):
    """Output contract for the Data Source Discovery agent.

    IMPORTANT: unlike every other agent built so far, an `AgentError` in
    `errors` here can be `recoverable=False` for a reason other than "this
    agent is broken" -- see `agent.py`'s module docstring for the
    deliberate, non-recoverable `"data_source_unreachable"` error case a
    caller MUST check for and halt the pipeline on, rather than treating it
    as an ordinary soft failure to shrug off.
    """

    result: DataSourceDiscoveryResult
