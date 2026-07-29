"""Contracts for the Clarification Coordinator agent.

Follows the same pattern as
`navigraph_agents.insight.follow_up_suggestion.contracts`: a small payload
model, an `AgentInput` subclass wrapping it plus a `request_context`, a
result model, and an `AgentOutput` subclass wrapping that result.
"""

from __future__ import annotations

from navigraph_shared.contracts import AgentInput, AgentOutput
from pydantic import BaseModel, ConfigDict, Field


class ClarificationCoordinatorPayload(BaseModel):
    """The original question, which pipeline stage failed to resolve it,
    why, and which specific terms from the question could not be mapped to
    any real data. This agent is invoked ONLY when upstream resolution came
    back completely empty -- see `agent.py`'s module docstring."""

    model_config = ConfigDict(extra="forbid")

    original_question: str
    failed_stage: str  # e.g. "understanding.schema_mapping"
    failure_reason: str
    unmapped_terms: list[str] = Field(default_factory=list)


class ClarificationCoordinatorInput(AgentInput):
    """Input contract for the Clarification Coordinator agent.

    Inherits the mandatory, non-optional `request_context: RequestContext`
    field from `AgentInput`.
    """

    payload: ClarificationCoordinatorPayload


class ClarificationCoordinatorResult(BaseModel):
    """Whether a clarifying question should be asked back, and (if so)
    what it is."""

    model_config = ConfigDict(extra="forbid")

    needs_clarification: bool
    clarifying_question: str | None = None


class ClarificationCoordinatorOutput(AgentOutput):
    """Output contract for the Clarification Coordinator agent."""

    result: ClarificationCoordinatorResult
