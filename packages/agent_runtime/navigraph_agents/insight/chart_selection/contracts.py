"""Contracts for the Chart Selection agent.

Follows the same pattern as
`navigraph_agents.understanding.intent_understanding.contracts`: a small
payload model, an `AgentInput` subclass wrapping it plus a
`request_context`, a result model, and an `AgentOutput` subclass wrapping
that result. Fully deterministic -- no LLM call, no `prompts/` directory,
no external client dependency at all: this agent is a pure function of its
input, exactly like `navigraph_agents.query.sql_optimization` and
`navigraph_agents.understanding.schema_mapping`.
"""

from __future__ import annotations

from typing import Any, Literal

from navigraph_shared.contracts import AgentInput, AgentOutput
from pydantic import BaseModel, ConfigDict, Field


class ChartColumnRef(BaseModel):
    """Mirrors schema_mapping.contracts.ResolvedColumnRef's role-bearing
    fields, duplicated per the established sibling-package convention
    (see schema_mapping.contracts.CatalogInventoryEntry's docstring for
    the full rationale: agent-to-agent contract dependencies go through
    the caller's data flow, not direct imports between sibling packages).

    PLUS one new field, `result_alias`, that no upstream Query-domain
    contract carries forward: SQL Generation's own aggregation aliasing
    (see sql_generation.agent._generate_statements/_aggregation_function
    -- a role="measure" column becomes `{column_name}_TOTAL` in the real
    SELECT list, e.g. UNITS -> UNITS_TOTAL) means this column's real
    header in DataFederationResult.final_columns diverges from its
    catalog column_name. Until a real Coordinator threads this
    structurally, the CALLER populates result_alias by replicating SQL
    Generation's own alias rule -- this agent only ever reads it, never
    computes it. See LIMITATIONS.md item 28 for the full gap this field
    documents."""

    model_config = ConfigDict(extra="forbid")

    term: str
    catalog_column_id: str
    table_name: str
    column_name: str
    data_type: str
    role: Literal["measure", "dimension", "filter"]
    result_alias: str


class ChartSelectionPayload(BaseModel):
    """The real, already-executed result set (as produced by Data
    Federation) plus the resolved columns (as produced by Schema Mapping,
    with `result_alias` threaded in by the caller) that this agent picks
    an x/y pair and a chart type from."""

    model_config = ConfigDict(extra="forbid")

    final_columns: list[str]
    final_rows: list[dict[str, Any]]
    final_row_count: int
    columns: list[ChartColumnRef]


class ChartSelectionInput(AgentInput):
    """Input contract for the Chart Selection agent.

    Inherits the mandatory, non-optional `request_context: RequestContext`
    field from `AgentInput`.
    """

    payload: ChartSelectionPayload


class ChartSpec(BaseModel):
    """The chosen chart type plus (if resolved) the result columns to plot
    it against."""

    model_config = ConfigDict(extra="forbid")

    chart_type: Literal["bar", "line", "table", "single_value"]
    x_column: str | None = None
    y_column: str | None = None
    rationale: str


class ChartSelectionResult(BaseModel):
    """The chosen `ChartSpec`, plus every column whose `result_alias`
    didn't actually appear in `final_columns` -- a real, honest signal
    that the caller's alias-threading didn't line up with the real result
    set, not silently dropped."""

    model_config = ConfigDict(extra="forbid")

    chart: ChartSpec
    unmatched_columns: list[str] = Field(default_factory=list)


class ChartSelectionOutput(AgentOutput):
    """Output contract for the Chart Selection agent."""

    result: ChartSelectionResult
