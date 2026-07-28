"""Shared contract types for every NaviGraph agent.

These are the REAL, load-bearing Pydantic v2 models that every agent's
`contracts.py` builds on. The single most important structural invariant in
this codebase lives here: every `AgentInput` subclass MUST carry a
non-optional `request_context: RequestContext` field, and `RequestContext`
itself REQUIRES a `tenant_id`. That is not a style preference -- it is the
first piece of the tenant-isolation discipline that the rest of the platform
(guardrails, lineage, RBAC) depends on. See `tests/test_contracts.py` for the
test that pins this down.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_event_id() -> str:
    return f"lineage_{uuid.uuid4().hex}"


class RequestContext(BaseModel):
    """Carries tenant/user/trace identity through every agent call.

    `tenant_id` is intentionally required with no default -- constructing a
    `RequestContext` without it raises `pydantic.ValidationError`. Every
    downstream contract (AgentInput, LineageEvent) either embeds this object
    or repeats `tenant_id` directly, so tenancy is structurally impossible to
    omit by accident.
    """

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    user_id: str
    trace_id: str
    roles: list[str] = Field(default_factory=list)
    claims: dict[str, Any] = Field(default_factory=dict)


class LineageEvent(BaseModel):
    """One auditable step in an agent's reasoning/execution trail."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=_new_event_id)
    agent_name: str
    timestamp: datetime = Field(default_factory=_utcnow)
    input_summary: str
    output_summary: str
    tenant_id: str
    trace_id: str


class AgentMetadata(BaseModel):
    """Operational metadata attached to every agent response."""

    model_config = ConfigDict(extra="forbid")

    latency_ms: float
    model_version: str | None = None
    prompt_version: str | None = None
    tokens_input: int | None = None
    tokens_output: int | None = None


class AgentError(BaseModel):
    """A recoverable-or-not error surfaced by an agent without raising."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    recoverable: bool


class AgentInput(BaseModel):
    """Base class every agent's concrete *Input model inherits from.

    Subclasses add their own payload fields (see
    `navigraph_agents.understanding.intent_understanding.contracts.IntentUnderstandingInput`
    for the reference implementation) but MUST NOT make `request_context`
    optional -- doing so would defeat the tenant-isolation guarantee.
    """

    model_config = ConfigDict(extra="forbid")

    request_context: RequestContext


class AgentOutput(BaseModel):
    """Base class every agent's concrete *Output model inherits from."""

    model_config = ConfigDict(extra="forbid")

    result: Any
    confidence: float | None = None
    lineage_events: list[LineageEvent] = Field(default_factory=list)
    errors: list[AgentError] = Field(default_factory=list)
    metadata: AgentMetadata
