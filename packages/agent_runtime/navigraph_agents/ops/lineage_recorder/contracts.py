"""Contracts for the Lineage Recorder agent.

Follows the same pattern as
`navigraph_agents.understanding.intent_understanding.contracts`: a small
payload model, an `AgentInput` subclass wrapping it plus a
`request_context`, a result model, and an `AgentOutput` subclass wrapping
that result. Fully deterministic -- no LLM call, no `prompts/` directory.

`LineageEvent` is imported DIRECTLY from `navigraph_shared.contracts`
rather than duplicated field-for-field like every sibling-agent mirror
type in this codebase. This is the one deliberate exception to the
"duplicate, don't cross-import" convention documented in
`schema_mapping.contracts.CatalogInventoryEntry`'s docstring: that
convention exists to keep agent packages from depending on a SIBLING
AGENT's own contract types. `LineageEvent` is not a sibling agent's
contract -- it is the shared platform type every agent already imports
from `navigraph_shared.contracts` and emits on every `AgentOutput`.
Recording it is this agent's entire purpose, so importing the canonical
type is correct, not a layering violation.
"""

from __future__ import annotations

from navigraph_shared.contracts import AgentInput, AgentOutput, LineageEvent
from pydantic import BaseModel, ConfigDict, Field


class LineageRecorderPayload(BaseModel):
    """The real `lineage_events` list from one upstream agent's own
    `AgentOutput` -- this agent is invoked once per upstream agent
    invocation, immediately after that agent returns (matching the
    pipeline diagram's own "lineage recorded at every stage" phrasing),
    not once at the end of an entire request."""

    model_config = ConfigDict(extra="forbid")

    events: list[LineageEvent] = Field(min_length=1)


class LineageRecorderInput(AgentInput):
    """Input contract for the Lineage Recorder agent.

    Inherits the mandatory, non-optional `request_context: RequestContext`
    field from `AgentInput`.
    """

    payload: LineageRecorderPayload


class LineageRecorderResult(BaseModel):
    """How many of `payload.events` were newly persisted vs. already
    present (idempotent no-ops, matched by `event_id`) -- re-recording the
    same event (e.g. a retried call) is never an error, always a real,
    honestly-counted no-op."""

    model_config = ConfigDict(extra="forbid")

    recorded_count: int
    duplicate_count: int
    trace_id: str


class LineageRecorderOutput(AgentOutput):
    """Output contract for the Lineage Recorder agent."""

    result: LineageRecorderResult
