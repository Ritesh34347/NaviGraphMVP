"""Contracts for the Request Orchestrator agent.

Follows the same pattern as every other agent: a small payload model, an
`AgentInput` subclass wrapping it plus a `request_context`, a result model,
and an `AgentOutput` subclass wrapping that result.

Unlike every leaf agent in this codebase, this agent's entire job is to
CALL roughly 22 other real agents in sequence -- it is the direct
successor to `eval/pipeline_chain.py::run_full_pipeline` (now retired, see
`agent.py`'s module docstring), which already established the precedent
that a CALLER of many sibling agents imports each one's real `Input`/
`Payload`/`Result` types directly, rather than mirroring them locally. That
precedent is different from (and does not violate) the usual
"sibling agents mirror, don't cross-import, each other's contract types"
convention -- that convention exists to stop two LEAF agents coupling to
each other's internals; it was never meant to stop the one real caller
that has to construct every downstream agent's actual input shape to
invoke it at all. See `agent.py` for the real cross-imports.
"""

from __future__ import annotations

from typing import Any, Literal

from navigraph_shared.contracts import AgentInput, AgentOutput
from pydantic import BaseModel, ConfigDict, Field

from navigraph_agents.understanding.intent_understanding.contracts import IntentLabel


class RequestOrchestratorPayload(BaseModel):
    """The one real entry point into the whole pipeline. `data_source_id`
    and `session_id` are both optional -- omitted, the Orchestrator
    resolves/mints them itself (see `agent.py`'s module docstring for the
    exact real resolution rules)."""

    model_config = ConfigDict(extra="forbid")

    question: str
    data_source_id: str | None = None
    session_id: str | None = None


class RequestOrchestratorInput(AgentInput):
    """Input contract for the Request Orchestrator agent.

    Inherits the mandatory, non-optional `request_context: RequestContext`
    field from `AgentInput` -- `request_context.tenant_id` drives
    `data_source_id` auto-resolution when the caller omits one, and
    `request_context.claims["tenant_id"]` must match `request_context.tenant_id`
    for Policy Authorization's real OPA policy to authorize the request
    (the same requirement every existing pipeline integration test already
    satisfies by hand).
    """

    payload: RequestOrchestratorPayload


class RequestOrchestratorResult(BaseModel):
    """The one real, complete answer to a conversational-BI question --
    OR a real clarification request, OR a real structured failure. Exactly
    one of the three `outcome` values applies; the fields that matter are
    documented per-outcome below. `session_id` is ALWAYS populated
    (echoed back if the caller supplied one, freshly minted otherwise) so
    the caller can pass it on the next real request to get real,
    persisted conversation history."""

    model_config = ConfigDict(extra="forbid")

    outcome: Literal["answered", "needs_clarification", "failed"]
    session_id: str
    resolved_question: str | None = None
    actual_intent: IntentLabel | None = None
    unmapped_terms: list[str] = Field(default_factory=list)

    # outcome == "answered"
    final_columns: list[str] = Field(default_factory=list)
    final_rows: list[dict[str, Any]] = Field(default_factory=list)
    final_row_count: int = 0
    chart: dict[str, Any] | None = None
    anomalies: list[dict[str, Any]] = Field(default_factory=list)
    narrative: str | None = None
    narrative_errors: list[str] = Field(default_factory=list)
    follow_up_suggestions: list[str] = Field(default_factory=list)
    generated_sql: str | None = None
    sql_params: dict[str, Any] = Field(default_factory=dict)

    # outcome == "needs_clarification"
    clarifying_question: str | None = None

    # outcome == "failed"
    failure_stage: str | None = None
    failure_reason: str | None = None


class RequestOrchestratorOutput(AgentOutput):
    """Output contract for the Request Orchestrator agent."""

    result: RequestOrchestratorResult
