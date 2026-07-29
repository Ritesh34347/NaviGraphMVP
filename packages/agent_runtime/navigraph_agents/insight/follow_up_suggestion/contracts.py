"""Contracts for the Follow-Up Suggestion agent.

Follows the same pattern as
`navigraph_agents.understanding.semantic_retrieval.contracts`: a small
payload model, an `AgentInput` subclass wrapping it plus a
`request_context`, a result model, and an `AgentOutput` subclass wrapping
that result.
"""

from __future__ import annotations

from typing import Literal

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


class FollowUpSuggestionPayload(BaseModel):
    """The original question, the narrative already written about it, a
    summary of the shape of the final result (columns + row count, not the
    full data -- this agent proposes NEW questions, it does not need to
    re-derive facts already established by Grounded Narrative Generation),
    the chart chosen, and any anomaly findings."""

    model_config = ConfigDict(extra="forbid")

    original_question: str
    narrative: str
    final_columns: list[str]
    final_row_count: int
    chart: ChartSpec
    anomalies: list[AnomalyFinding] = Field(default_factory=list)


class FollowUpSuggestionInput(AgentInput):
    """Input contract for the Follow-Up Suggestion agent.

    Inherits the mandatory, non-optional `request_context: RequestContext`
    field from `AgentInput`.
    """

    payload: FollowUpSuggestionPayload


class FollowUpQuestion(BaseModel):
    """One proposed follow-up question. Deliberately NOT grounded against a
    closed candidate list -- see `agent.py`'s module docstring for why."""

    model_config = ConfigDict(extra="forbid")

    question: str
    rationale: str | None = None


class FollowUpSuggestionResult(BaseModel):
    """1-3 proposed follow-up questions, in the order the LLM ranked them."""

    model_config = ConfigDict(extra="forbid")

    suggestions: list[FollowUpQuestion] = Field(default_factory=list)


class FollowUpSuggestionOutput(AgentOutput):
    """Output contract for the Follow-Up Suggestion agent."""

    result: FollowUpSuggestionResult
