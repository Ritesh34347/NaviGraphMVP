"""Contracts for the Conversation agent.

Follows the same pattern as
`navigraph_agents.understanding.intent_understanding.contracts`: a small
payload model, an `AgentInput` subclass wrapping it plus a
`request_context`, a result model, and an `AgentOutput` subclass wrapping
that result.
"""

from __future__ import annotations

from navigraph_shared.contracts import AgentInput, AgentOutput
from pydantic import BaseModel, ConfigDict, Field

from navigraph_agents.understanding.intent_understanding.contracts import IntentLabel


class ConversationTurn(BaseModel):
    """One prior turn in the conversation, as already resolved by this agent
    (or by upstream classification) on a previous request."""

    model_config = ConfigDict(extra="forbid")

    turn_id: str
    raw_question: str
    resolved_question: str
    intent: IntentLabel | None = None
    entities: list[str] = Field(default_factory=list)


class ConversationPayload(BaseModel):
    """The new question plus however much prior conversation history the
    caller has accumulated so far. An empty `conversation_history` means
    this is the first turn of the conversation."""

    model_config = ConfigDict(extra="forbid")

    question: str
    conversation_history: list[ConversationTurn] = Field(default_factory=list)


class ConversationInput(AgentInput):
    """Input contract for the Conversation agent.

    Inherits the mandatory, non-optional `request_context: RequestContext`
    field from `AgentInput`.
    """

    payload: ConversationPayload


class ConversationResult(BaseModel):
    """The resolved, standalone question plus whether/what it referenced."""

    model_config = ConfigDict(extra="forbid")

    resolved_question: str
    is_follow_up: bool
    referenced_turn_id: str | None = None
    raw_question: str


class ConversationOutput(AgentOutput):
    """Output contract for the Conversation agent."""

    result: ConversationResult
