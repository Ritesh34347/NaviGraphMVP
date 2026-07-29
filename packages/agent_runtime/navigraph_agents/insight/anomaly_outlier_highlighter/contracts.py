"""Contracts for the Anomaly/Outlier Highlighter agent.

Follows the same pattern as
`navigraph_agents.understanding.intent_understanding.contracts`: a small
payload model, an `AgentInput` subclass wrapping it plus a
`request_context`, a result model, and an `AgentOutput` subclass wrapping
that result. Fully deterministic -- no LLM call, no `prompts/` directory,
no external client dependency at all: this agent is a pure function of its
input, exactly like `navigraph_agents.insight.chart_selection` and
`navigraph_agents.guardrail.query_cost_estimator`.

A note on the local `ChartSpec` type below: it mirrors the shape of
`navigraph_agents.insight.chart_selection.contracts.ChartSpec`, a type
owned by the sibling Chart Selection agent package. It is duplicated here
on purpose, not imported across the package boundary -- see
`navigraph_agents.understanding.schema_mapping.contracts.CatalogInventoryEntry`'s
docstring for the full rationale: agent-to-agent contract dependencies
should go through the Coordinator's data flow, not direct Python imports
between sibling agent packages.
"""

from __future__ import annotations

from typing import Any, Literal

from navigraph_shared.contracts import AgentInput, AgentOutput
from pydantic import BaseModel, ConfigDict, Field


class ChartSpec(BaseModel):
    """Mirrors chart_selection.contracts.ChartSpec field-for-field --
    duplicated rather than cross-imported, per the established
    sibling-package convention."""

    model_config = ConfigDict(extra="forbid")

    chart_type: Literal["bar", "line", "table", "single_value"]
    x_column: str | None = None
    y_column: str | None = None
    rationale: str


class AnomalyDetectionPayload(BaseModel):
    """The real, already-executed result set (as produced by Data
    Federation) plus the `ChartSpec` Chart Selection assigned to it --
    `chart.y_column` (if resolved) is the measure this agent runs z-score
    detection over, and `chart.x_column` (if resolved) is used purely to
    label each finding's `group_value`."""

    model_config = ConfigDict(extra="forbid")

    final_columns: list[str]
    final_rows: list[dict[str, Any]]
    final_row_count: int
    chart: ChartSpec


class AnomalyDetectionInput(AgentInput):
    """Input contract for the Anomaly/Outlier Highlighter agent.

    Inherits the mandatory, non-optional `request_context: RequestContext`
    field from `AgentInput`.
    """

    payload: AnomalyDetectionPayload


class AnomalyFinding(BaseModel):
    """One result row whose measure value's z-score exceeded the
    detection threshold."""

    model_config = ConfigDict(extra="forbid")

    row_index: int
    group_value: str
    measure_value: float
    z_score: float
    mean: float
    stdev: float


class AnomalyDetectionResult(BaseModel):
    """The anomalies found (if any), or a `skipped_reason` explaining why
    detection did not run at all -- a real, honest degraded case, not a
    failure."""

    model_config = ConfigDict(extra="forbid")

    method: Literal["z_score"] = "z_score"
    threshold: float = 2.0
    anomalies: list[AnomalyFinding] = Field(default_factory=list)
    skipped_reason: str | None = None


class AnomalyDetectionOutput(AgentOutput):
    """Output contract for the Anomaly/Outlier Highlighter agent."""

    result: AnomalyDetectionResult
