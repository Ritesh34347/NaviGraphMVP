"""Contracts for the SQL Optimization agent.

Follows the same pattern as
`navigraph_agents.understanding.intent_understanding.contracts`: a small
payload model, an `AgentInput` subclass wrapping it plus a
`request_context`, a result model, and an `AgentOutput` subclass wrapping
that result. Fully deterministic -- no LLM call, no `prompts/` directory,
no external client dependency at all: this agent is a pure function of its
input, exactly like `navigraph_agents.understanding.schema_mapping`.

A note on the local `GeneratedSql` type below: it mirrors the shape of
`navigraph_agents.query.sql_generation.contracts.GeneratedSql`, a type
owned by a sibling agent package (SQL Generation) being built by a
parallel workstream. It is duplicated here on purpose, not imported across
the package boundary -- see
`navigraph_agents.understanding.schema_mapping.contracts.CatalogInventoryEntry`'s
docstring for the full rationale: agent-to-agent contract dependencies
should go through the Coordinator's data flow, not direct Python imports
between sibling agent packages, so no agent package should ever need to
import another agent package's contracts module directly. That holds
regardless of build order -- it is not a stopgap for SQL Generation not
existing yet when this package was written.
"""

from __future__ import annotations

from typing import Any

from navigraph_shared.contracts import AgentInput, AgentOutput
from pydantic import BaseModel, ConfigDict, Field


class GeneratedSql(BaseModel):
    """Mirrors `navigraph_agents.query.sql_generation.contracts.GeneratedSql`
    field-for-field -- duplicated rather than cross-imported, see this
    module's docstring."""

    model_config = ConfigDict(extra="forbid")

    data_source_id: str
    sql: str
    params: dict[str, Any] = Field(default_factory=dict)
    referenced_tables: list[str]
    referenced_columns: list[str]


class SqlOptimizationPayload(BaseModel):
    """The statements SQL Generation produced, plus the tenant/trace
    identifiers this agent stamps into its audit comment and an optional
    row-count-estimate lookup used for the large-unfiltered-scan advisory
    warning.

    `tenant_id`/`trace_id` are also available via `request_context` on the
    enclosing `AgentInput`, but are repeated here explicitly so this
    agent's `run` method only ever has to reach into its declared
    `payload` for everything it needs -- keeping it a pure function of its
    declared inputs rather than implicitly depending on a sibling field on
    the input envelope.
    """

    model_config = ConfigDict(extra="forbid")

    statements: list[GeneratedSql]
    tenant_id: str
    trace_id: str
    # table_name -> row-count estimate. Optional and best-effort: mirrors
    # `navigraph_catalog.models.CatalogTable.row_count_estimate`, but this
    # agent has no catalog client dependency of its own (matching Schema
    # Mapping's "no external client" design) -- whatever estimates the
    # caller already has on hand for the referenced tables are passed in
    # directly instead. A missing or `None` entry simply means "no
    # estimate available", not "zero rows".
    table_row_count_estimates: dict[str, int | None] = Field(default_factory=dict)


class SqlOptimizationInput(AgentInput):
    """Input contract for the SQL Optimization agent.

    Inherits the mandatory, non-optional `request_context: RequestContext`
    field from `AgentInput`.
    """

    payload: SqlOptimizationPayload


class OptimizedSql(BaseModel):
    """One statement after optimization: the rewritten SQL, whichever
    rules actually fired on it, and (if computable) an estimated row
    count."""

    model_config = ConfigDict(extra="forbid")

    data_source_id: str
    sql: str
    params: dict[str, Any] = Field(default_factory=dict)
    applied_rules: list[str]
    estimated_row_count: int | None = None


class SqlOptimizationResult(BaseModel):
    """The optimized statements, plus advisory (never blocking) warnings
    about statements likely to scan a very large table with no filter."""

    model_config = ConfigDict(extra="forbid")

    statements: list[OptimizedSql]
    warnings: list[str] = Field(default_factory=list)


class SqlOptimizationOutput(AgentOutput):
    """Output contract for the SQL Optimization agent."""

    result: SqlOptimizationResult
