"""Contracts for the Intent Understanding agent.

This is the reference implementation of the agent contract pattern every
future agent (built in later phases) should follow: a small payload model,
an `AgentInput` subclass wrapping it plus a `request_context`, a result
model with a controlled vocabulary, and an `AgentOutput` subclass wrapping
that result.
"""

from __future__ import annotations

from typing import Literal

from navigraph_shared.contracts import AgentInput, AgentOutput
from pydantic import BaseModel, ConfigDict, Field

# Controlled vocabulary for classified intents. "unknown" is the required
# safe fallback when the LLM's classification is missing, malformed, or
# doesn't match one of the known values -- see agent.py's error handling.
IntentLabel = Literal[
    "metric_lookup",
    "trend_analysis",
    "comparison",
    "anomaly_investigation",
    "unknown",
]


class IntentUnderstandingPayload(BaseModel):
    """The question text this agent classifies."""

    model_config = ConfigDict(extra="forbid")

    question: str


class IntentUnderstandingInput(AgentInput):
    """Input contract for the Intent Understanding agent.

    Inherits the mandatory, non-optional `request_context: RequestContext`
    field from `AgentInput` -- this agent cannot be invoked without a
    tenant-scoped request context.
    """

    payload: IntentUnderstandingPayload


class IntentUnderstandingResult(BaseModel):
    """The classified intent, extracted entities, and the original question."""

    model_config = ConfigDict(extra="forbid")

    intent: IntentLabel
    entities: list[str] = Field(default_factory=list)
    raw_question: str


class IntentUnderstandingOutput(AgentOutput):
    """Output contract for the Intent Understanding agent."""

    result: IntentUnderstandingResult
