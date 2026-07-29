"""Contracts for the Grounded Narrative Generation agent.

Follows the same pattern as
`navigraph_agents.understanding.semantic_retrieval.contracts`: a small
payload model, an `AgentInput` subclass wrapping it plus a
`request_context`, a result model, and an `AgentOutput` subclass wrapping
that result.
"""

from __future__ import annotations

from typing import Any, Literal

from navigraph_shared.contracts import AgentInput, AgentOutput
from pydantic import BaseModel, ConfigDict, Field


class ChartSpec(BaseModel):
    """Mirrors chart_selection.contracts.ChartSpec -- duplicated per the
    established sibling-package convention (see
    schema_mapping.contracts.CatalogInventoryEntry's docstring for the
    full rationale)."""

    model_config = ConfigDict(extra="forbid")

    chart_type: Literal["bar", "line", "table", "single_value"]
    x_column: str | None = None
    y_column: str | None = None
    rationale: str


class AnomalyFinding(BaseModel):
    """Mirrors anomaly_outlier_highlighter.contracts.AnomalyFinding."""

    model_config = ConfigDict(extra="forbid")

    row_index: int
    group_value: str
    measure_value: float
    z_score: float
    mean: float
    stdev: float


class NarrativeGenerationPayload(BaseModel):
    """The original question, the final result set the narrative must be
    grounded in, the chart chosen to display it, and any anomaly findings
    surfaced alongside it."""

    model_config = ConfigDict(extra="forbid")

    original_question: str
    final_columns: list[str]
    final_rows: list[dict[str, Any]]
    final_row_count: int
    chart: ChartSpec
    anomalies: list[AnomalyFinding] = Field(default_factory=list)


class NarrativeGenerationInput(AgentInput):
    """Input contract for the Grounded Narrative Generation agent.

    Inherits the mandatory, non-optional `request_context: RequestContext`
    field from `AgentInput`.
    """

    payload: NarrativeGenerationPayload


class NarrativeCitation(BaseModel):
    """One `[N]` bracket marker in the narrative, pointing at the exact
    real value it was derived from."""

    model_config = ConfigDict(extra="forbid")

    citation_id: int
    row_index: int
    column: str
    cited_value: str


class NarrativeGenerationResult(BaseModel):
    """The generated narrative plus its citations (only citations that
    survived validation against the real result set / anomaly data) and any
    numeric tokens found in the narrative that could not be verified against
    real data at all."""

    model_config = ConfigDict(extra="forbid")

    narrative: str
    citations: list[NarrativeCitation] = Field(default_factory=list)
    unverifiable_numbers: list[str] = Field(default_factory=list)


class NarrativeGenerationOutput(AgentOutput):
    """Output contract for the Grounded Narrative Generation agent."""

    result: NarrativeGenerationResult
