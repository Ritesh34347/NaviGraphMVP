"""Session/Context Manager agent implementation.

Fully deterministic: no LLM call, no `prompts/` directory. Reads or appends
to a real, tenant-scoped conversation history for a given `session_id`, via
an injected cache client -- structurally identical in spirit to
`navigraph_agents.query.caching.agent.CachingAgent`, just keyed on a session
rather than a query fingerprint.

REDIS-CLIENT-PROTOCOL ASSUMPTION (read this before wiring a real client in):
exactly the same assumption as Caching's -- `redis` is NOT a declared
dependency of `packages/agent_runtime` (adding it would require editing
that package's `pyproject.toml`, outside this phase's scope). The
constructor accepts ANY object satisfying `CacheClientProtocol` below:
`get(key) -> bytes | None` and `set(key, value, ex=ttl) -> Any`. This
protocol is duplicated here rather than imported from
`navigraph_agents.query.caching.agent`, matching this codebase's
established sibling-agent convention of duplicating small shared shapes
instead of creating cross-package Python imports (see
`schema_mapping.contracts.CatalogInventoryEntry`'s docstring for the full
rationale). This module's own unit tests inject a fake, in-memory
dict-backed client satisfying the same protocol, never a real Redis
connection.

CACHE-KEY DESIGN: `f"navigraph:v1:{tenant_id}:session:{session_id}"`, mirroring
`caching.agent.build_cache_key`'s exact scheme -- `tenant_id` is a literal,
readable prefix segment, not folded into a hash, for the identical
tenant-isolation reasons documented there: two tenants' sessions can never
collide even if a `session_id` happens to be reused/guessed across tenants
(e.g. a client-generated UUID collision, or a replayed session id from a
different tenant's request), and a future "flush all sessions for tenant X"
operational need can be satisfied with a `SCAN`-based prefix match.

SLIDING TTL: every successful `append_turn` re-writes the key with
`ex=_SESSION_TTL_SECONDS`, refreshing the expiration on each turn -- a
session that is actively being used never expires mid-conversation; only a
session that's gone quiet for the full TTL window is reclaimed.

ERROR CONTRACT: mirrors `caching/agent.py`'s exactly. Any exception raised
by the injected client (unreachable, timeout) OR a JSON/shape deserialization
failure reading back a corrupt stored value is caught and turned into a
RECOVERABLE `AgentError(code="session_backend_unavailable", recoverable=True)`
-- a session-store failure must never fail the whole request; the caller
falls back to an empty `conversation_history` and can still proceed
(treating this turn as if it were the first). Separately, `append_turn`
called with no `new_turn` is a genuine caller-contract violation, not a
backend failure -- recorded as a NON-recoverable
`AgentError(code="invalid_append_turn_payload", recoverable=False)`, mirroring
Caching's `invalid_store_payload` split between caller-contract violations
and backend failures.
"""

from __future__ import annotations

import json
import time
from typing import Any, Protocol

from navigraph_shared.contracts import AgentError, AgentMetadata, LineageEvent
from navigraph_shared.telemetry import (
    get_tracer,
    record_agent_error,
    record_agent_invocation,
)
from opentelemetry.trace import Tracer

from navigraph_agents.orchestrator.session_context_manager.contracts import (
    ConversationTurn,
    SessionContextManagerInput,
    SessionContextManagerOutput,
    SessionContextManagerResult,
)

AGENT_NAME = "orchestrator.session_context_manager"

_CACHE_KEY_PREFIX = "navigraph:v1"
_CACHE_KEY_NAMESPACE = "session"

# 30 minutes, sliding -- a real, unvalidated placeholder pending real usage
# data, same category as guardrail.query_cost_estimator's ROLE_ROW_LIMITS.
_SESSION_TTL_SECONDS = 1800

# Kept deliberately separate from ConversationAgent's own private
# prompt-window cap -- storage capacity and prompt-window size are
# different concerns: this is how much history the Session/Context Manager
# is willing to retain in the cache at all, not how much of it any one
# downstream agent chooses to feed into a prompt.
_MAX_STORED_TURNS = 20


class CacheClientProtocol(Protocol):
    """Minimal structural protocol this agent needs from a cache client.

    A real `redis.Redis` instance satisfies this exactly -- see this
    module's docstring's "REDIS-CLIENT-PROTOCOL ASSUMPTION" section.
    Duplicated from `navigraph_agents.query.caching.agent.CacheClientProtocol`
    rather than imported, per this codebase's sibling-agent convention.
    """

    def get(self, key: str) -> bytes | None: ...

    def set(self, key: str, value: bytes, ex: int | None = None) -> Any: ...


def build_cache_key(*, tenant_id: str, session_id: str) -> str:
    """Build this agent's real cache-key shape.

    `tenant_id` is a literal, readable prefix segment -- NOT folded into a
    hash -- per this module's docstring's tenant-isolation design note.
    Exposed as a module-level function (not just inlined in `run()`),
    mirroring `caching.agent.build_cache_key`, so a caller that only needs
    to compute the key a `get`/`append_turn` will use (e.g. an
    operational session-flush tool) can do so without constructing a full
    `SessionContextManagerAgent`.
    """

    return f"{_CACHE_KEY_PREFIX}:{tenant_id}:{_CACHE_KEY_NAMESPACE}:{session_id}"


class SessionContextManagerAgent:
    """Reads or appends to a tenant-scoped conversation history, via an
    injected cache client."""

    def __init__(
        self,
        cache_client: CacheClientProtocol,
        tracer: Tracer | None = None,
    ) -> None:
        self._client = cache_client
        self._tracer = tracer or get_tracer("navigraph-agent-runtime")

    async def run(self, input: SessionContextManagerInput) -> SessionContextManagerOutput:
        start = time.perf_counter()
        request_context = input.request_context
        payload = input.payload

        cache_key = build_cache_key(
            tenant_id=request_context.tenant_id,
            session_id=payload.session_id,
        )

        errors: list[AgentError] = []
        conversation_history: list[ConversationTurn] = []

        with self._tracer.start_as_current_span("agent.session_context_manager.run") as span:
            span.set_attribute("navigraph.tenant_id", request_context.tenant_id)
            span.set_attribute("navigraph.trace_id", request_context.trace_id)
            span.set_attribute("navigraph.agent_name", AGENT_NAME)
            span.set_attribute("navigraph.cache_key", cache_key)
            span.set_attribute("navigraph.operation", payload.operation)
            span.set_attribute("navigraph.session_id", payload.session_id)

            if payload.operation == "append_turn" and payload.new_turn is None:
                # A genuine caller-contract violation -- not a backend
                # failure, so NOT the recoverable code below. No retry
                # against the same malformed request would help.
                errors.append(
                    AgentError(
                        code="invalid_append_turn_payload",
                        message="operation='append_turn' requires new_turn",
                        recoverable=False,
                    )
                )
            else:
                try:
                    if payload.operation == "get":
                        conversation_history = self._read_history(cache_key)
                    else:
                        conversation_history = self._append_turn(cache_key, payload.new_turn)
                except Exception as exc:  # noqa: BLE001 - a session-store failure must never block
                    errors.append(
                        AgentError(
                            code="session_backend_unavailable",
                            message=f"Session cache backend call failed: {exc}",
                            recoverable=True,
                        )
                    )
                    conversation_history = []

            result = SessionContextManagerResult(
                session_id=payload.session_id,
                conversation_history=conversation_history,
                turn_count=len(conversation_history),
            )

            # A recoverable backend error is still a degraded-but-usable
            # outcome (the caller falls back to no history), so it gets
            # partial, not zero, confidence -- unlike the non-recoverable
            # `invalid_append_turn_payload` path, which reflects a
            # genuinely malformed request.
            if any(not error.recoverable for error in errors):
                confidence = 0.0
            elif errors:
                confidence = 0.5
            else:
                confidence = 1.0

            lineage_event = LineageEvent(
                agent_name=AGENT_NAME,
                input_summary=f"operation={payload.operation} session_id={payload.session_id}",
                output_summary=f"turn_count={result.turn_count} errors={len(errors)}",
                tenant_id=request_context.tenant_id,
                trace_id=request_context.trace_id,
            )

            latency_ms = (time.perf_counter() - start) * 1000.0
            metadata = AgentMetadata(latency_ms=latency_ms)

            span.set_attribute("navigraph.turn_count", result.turn_count)

        record_agent_invocation(AGENT_NAME, latency_ms=latency_ms, success=not errors)
        for error in errors:
            record_agent_error(AGENT_NAME, error_code=error.code, recoverable=error.recoverable)

        return SessionContextManagerOutput(
            result=result,
            confidence=confidence,
            lineage_events=[lineage_event],
            errors=errors,
            metadata=metadata,
        )

    def _read_history(self, cache_key: str) -> list[ConversationTurn]:
        """A missing key (cache miss) is never an error -- it just means
        this is the first turn of the conversation."""

        raw = self._client.get(cache_key)
        if raw is None:
            return []
        data = json.loads(raw)
        return [ConversationTurn(**entry) for entry in data]

    def _append_turn(
        self, cache_key: str, new_turn: ConversationTurn | None
    ) -> list[ConversationTurn]:
        assert new_turn is not None  # guaranteed by the caller in run()

        history = self._read_history(cache_key)
        history.append(new_turn)
        # Keep the tail -- the most recent `_MAX_STORED_TURNS`, not the
        # oldest.
        truncated = history[-_MAX_STORED_TURNS:]

        serialized = json.dumps([turn.model_dump() for turn in truncated]).encode("utf-8")
        # Sliding TTL: refreshed on every write, so an actively-used session
        # never expires mid-conversation.
        self._client.set(cache_key, serialized, ex=_SESSION_TTL_SECONDS)

        return truncated
