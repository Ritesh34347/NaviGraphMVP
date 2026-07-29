"""Contracts for the Semantic Retrieval agent.

Follows the same pattern as
`navigraph_agents.understanding.intent_understanding.contracts`: a small
payload model, an `AgentInput` subclass wrapping it plus a
`request_context`, a result model, and an `AgentOutput` subclass wrapping
that result.
"""

from __future__ import annotations

from navigraph_shared.contracts import AgentInput, AgentOutput
from pydantic import BaseModel, ConfigDict, Field


class RetrievalCandidate(BaseModel):
    """One catalog column the LLM is allowed to match an unresolved term
    against. `candidates` in `SemanticRetrievalPayload` is the entire closed
    universe of valid answers -- the agent must never accept a match outside
    this list."""

    model_config = ConfigDict(extra="forbid")

    catalog_column_id: str
    table_name: str
    column_name: str
    business_name: str | None = None
    synonyms: list[str] = Field(default_factory=list)
    description: str | None = None


class SemanticRetrievalPayload(BaseModel):
    """The question, the business terms in it that couldn't be resolved by
    cheaper means (e.g. exact/fuzzy string match), and the closed candidate
    list to match them against."""

    model_config = ConfigDict(extra="forbid")

    question: str
    unresolved_terms: list[str]
    candidates: list[RetrievalCandidate]


class SemanticRetrievalInput(AgentInput):
    """Input contract for the Semantic Retrieval agent.

    Inherits the mandatory, non-optional `request_context: RequestContext`
    field from `AgentInput`.
    """

    payload: SemanticRetrievalPayload


class TermMatch(BaseModel):
    """The outcome of trying to match one unresolved term against the
    candidate list."""

    model_config = ConfigDict(extra="forbid")

    term: str
    matched: bool
    catalog_column_id: str | None = None
    table_name: str | None = None
    column_name: str | None = None
    rationale: str | None = None


class SemanticRetrievalResult(BaseModel):
    """One `TermMatch` per unresolved term in the input, in the same order."""

    model_config = ConfigDict(extra="forbid")

    matches: list[TermMatch]


class SemanticRetrievalOutput(AgentOutput):
    """Output contract for the Semantic Retrieval agent."""

    result: SemanticRetrievalResult
