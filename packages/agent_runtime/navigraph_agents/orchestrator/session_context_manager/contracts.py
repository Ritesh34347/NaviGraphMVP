"""Contracts for the Session/Context Manager agent.

Follows the same pattern as `navigraph_agents.query.caching.contracts`: a
small payload model, an `AgentInput` subclass wrapping it plus a
`request_context`, a result model, and an `AgentOutput` subclass wrapping
that result. Fully deterministic -- no LLM call, no `prompts/` directory.
Has exactly one external client dependency, a minimal cache-client protocol
(see `agent.py`'s module docstring for the real Redis-compatibility
assumption, mirrored from Caching).
"""

from __future__ import annotations

from typing import Literal

from navigraph_shared.contracts import AgentInput, AgentOutput
from pydantic import BaseModel, ConfigDict, Field

from navigraph_agents.understanding.intent_understanding.contracts import IntentLabel


class ConversationTurn(BaseModel):
    """Mirrors `understanding.conversation.contracts.ConversationTurn`
    field-for-field -- duplicated here rather than cross-imported, per the
    established sibling-agent convention (see
    `schema_mapping.contracts.CatalogInventoryEntry`'s docstring for the
    full rationale: agent-to-agent contract dependencies should go through
    the Coordinator's data flow, not direct Python imports between sibling
    agent packages)."""

    model_config = ConfigDict(extra="forbid")

    turn_id: str
    raw_question: str
    resolved_question: str
    intent: IntentLabel | None = None
    entities: list[str] = Field(default_factory=list)


class SessionContextManagerPayload(BaseModel):
    """Which session to operate on, which operation to perform, and (for
    `append_turn`) the new turn to append."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    operation: Literal["get", "append_turn"]
    # Required iff operation == "append_turn"; ignored for "get".
    new_turn: ConversationTurn | None = None


class SessionContextManagerInput(AgentInput):
    """Input contract for the Session/Context Manager agent.

    Inherits the mandatory, non-optional `request_context: RequestContext`
    field from `AgentInput` -- `request_context.tenant_id` is a load-bearing
    part of the cache key itself, not merely context; see `agent.py`.
    """

    payload: SessionContextManagerPayload


class SessionContextManagerResult(BaseModel):
    """The conversation history for this session after the requested
    operation, plus its length."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    conversation_history: list[ConversationTurn] = Field(default_factory=list)
    turn_count: int


class SessionContextManagerOutput(AgentOutput):
    """Output contract for the Session/Context Manager agent."""

    result: SessionContextManagerResult
