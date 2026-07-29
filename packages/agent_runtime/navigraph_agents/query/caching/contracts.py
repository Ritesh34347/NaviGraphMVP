"""Contracts for the Caching agent.

Follows the same pattern as
`navigraph_agents.understanding.intent_understanding.contracts`: a small
payload model, an `AgentInput` subclass wrapping it plus a `request_context`,
a result model, and an `AgentOutput` subclass wrapping that result. Fully
deterministic -- no LLM call, no `prompts/` directory. Has exactly one
external client dependency, a minimal cache-client protocol (see
`agent.py`'s module docstring for the real Redis-compatibility assumption).
"""

from __future__ import annotations

from typing import Any, Literal

from navigraph_shared.contracts import AgentInput, AgentOutput
from pydantic import BaseModel, ConfigDict, Field


class CachingPayload(BaseModel):
    """What to look up or store, and under what cache key components."""

    model_config = ConfigDict(extra="forbid")

    operation: Literal["lookup", "store"]
    sql: str
    params: dict[str, Any] = Field(default_factory=dict)
    data_source_id: str
    # Reserved for the future Guardrail phase (policy-scoped cache
    # segmentation -- e.g. a masking/row-level-security policy version that
    # changed the shape of what a cached result is even allowed to contain)
    # -- see this project's DECISIONS.md for the broader Guardrail-domain
    # phasing this anticipates. Deliberately part of the cache KEY (not just
    # metadata) from day one: baking it in now means a real policy version
    # never requires a cache-key migration later, only a default-value
    # change from `"none"`.
    policy_version: str = "none"
    # Required for "store" (the `DataFederationResult`, serialized to a
    # plain JSON-able dict by the caller); ignored for "lookup".
    value: dict[str, Any] | None = None
    ttl_seconds: int = 300


class CachingInput(AgentInput):
    """Input contract for the Caching agent.

    Inherits the mandatory, non-optional `request_context: RequestContext`
    field from `AgentInput` -- `request_context.tenant_id` is a load-bearing
    part of the cache key itself, not merely context; see `agent.py`.
    """

    payload: CachingPayload


class CachingResult(BaseModel):
    """The real cache key used, and whichever outcome this operation had."""

    model_config = ConfigDict(extra="forbid")

    cache_key: str
    hit: bool = False
    cached_value: dict[str, Any] | None = None
    stored: bool = False


class CachingOutput(AgentOutput):
    """Output contract for the Caching agent."""

    result: CachingResult
