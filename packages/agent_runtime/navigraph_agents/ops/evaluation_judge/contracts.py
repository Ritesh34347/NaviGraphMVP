"""Contracts for the Evaluation Judge agent.

Follows the same pattern as
`navigraph_agents.understanding.intent_understanding.contracts`: a small
payload model, an `AgentInput` subclass wrapping it plus a
`request_context`, a result model, and an `AgentOutput` subclass wrapping
that result.

`ChartSpec` and `AnomalyFinding` mirror
`navigraph_agents.insight.chart_selection.contracts.ChartSpec` and
`navigraph_agents.insight.anomaly_outlier_highlighter.contracts.AnomalyFinding`
field-for-field -- duplicated here on purpose, not imported across the
package boundary -- see
`navigraph_agents.understanding.schema_mapping.contracts.CatalogInventoryEntry`'s
docstring for the full rationale: agent-to-agent contract dependencies
should go through the Coordinator's data flow, not direct Python imports
between sibling agent packages.

`IntentLabel` is the one exception to "mirror, don't import": it is a
controlled vocabulary, not a data shape, and
`navigraph_agents.understanding.schema_mapping.contracts` (followed by
`navigraph_agents.query.sql_generation.contracts` and
`navigraph_agents.guardrail.policy_authorization.contracts`) already
establishes the precedent of importing it directly from
`intent_understanding.contracts` rather than redefining it -- see that
module's comment on the same import for the rationale, which applies
verbatim here.
"""

from __future__ import annotations

from typing import Any, Literal

from navigraph_shared.contracts import AgentInput, AgentOutput
from pydantic import BaseModel, ConfigDict, Field

# Reused, not redefined -- see intent_understanding/contracts.py's module
# docstring for why `IntentLabel` is the single controlled vocabulary every
# agent that deals with intent must import rather than re-declare.
from navigraph_agents.understanding.intent_understanding.contracts import IntentLabel


class ChartSpec(BaseModel):
    """Mirrors insight.chart_selection.contracts.ChartSpec -- duplicated
    rather than cross-imported, per the established sibling-package
    convention (see schema_mapping.contracts.CatalogInventoryEntry's
    docstring for the full rationale)."""

    model_config = ConfigDict(extra="forbid")

    chart_type: Literal["bar", "line", "table", "single_value"]
    x_column: str | None = None
    y_column: str | None = None
    rationale: str


class AnomalyFinding(BaseModel):
    """Mirrors insight.anomaly_outlier_highlighter.contracts.AnomalyFinding."""

    model_config = ConfigDict(extra="forbid")

    row_index: int
    group_value: str
    measure_value: float
    z_score: float
    mean: float
    stdev: float


class EvaluationJudgePayload(BaseModel):
    """Everything the judge needs to score one already-completed
    conversational-BI answer: the original question, what was expected
    versus what actually happened (intent/entities), the real final data
    the narrative was supposed to be grounded in, the chart chosen, any
    anomaly findings, and the narrative itself."""

    model_config = ConfigDict(extra="forbid")

    question: str
    expected_intent: IntentLabel
    expected_entities: list[str] = Field(default_factory=list)
    actual_intent: IntentLabel
    actual_narrative: str
    final_columns: list[str]
    final_rows: list[dict[str, Any]]
    chart: ChartSpec
    anomalies: list[AnomalyFinding] = Field(default_factory=list)


class EvaluationJudgeInput(AgentInput):
    """Input contract for the Evaluation Judge agent.

    Inherits the mandatory, non-optional `request_context: RequestContext`
    field from `AgentInput`.
    """

    payload: EvaluationJudgePayload


class DimensionScore(BaseModel):
    """One scored dimension: a 1-5 integer score plus a short rationale."""

    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=1, le=5)
    rationale: str


class EvaluationJudgeResult(BaseModel):
    """The three LLM-judged dimensions (correctness, groundedness,
    narrative_quality) plus `intent_match`, which is computed directly in
    Python from `actual_intent == expected_intent` -- never asked of the
    LLM at all."""

    model_config = ConfigDict(extra="forbid")

    correctness: DimensionScore
    groundedness: DimensionScore
    narrative_quality: DimensionScore
    intent_match: bool


class EvaluationJudgeOutput(AgentOutput):
    """Output contract for the Evaluation Judge agent."""

    result: EvaluationJudgeResult
