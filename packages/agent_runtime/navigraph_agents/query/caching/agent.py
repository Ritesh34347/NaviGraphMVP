"""Caching agent implementation.

Fully deterministic: no LLM call, no `prompts/` directory. Builds a real,
tenant-scoped cache key for a `(sql, params, data_source_id)` query
fingerprint and either looks up or stores a `DataFederationResult` (already
serialized to a plain JSON-able dict by the caller) against it, via an
injected cache client.

REDIS-CLIENT-PROTOCOL ASSUMPTION (read this before wiring a real client in):
`redis` is NOT a declared dependency of `packages/agent_runtime` (adding it
would require editing that package's `pyproject.toml`, which is outside
this phase's scope -- see this repo's task boundaries). Instead, the
constructor accepts ANY object satisfying a minimal structural protocol,
`CacheClientProtocol` below: `get(key) -> bytes | None` and `set(key, value,
ex=ttl) -> Any`. A real `redis.Redis` instance satisfies this protocol
exactly as-is (`redis.Redis.get`/`redis.Redis.set` have this same shape,
`ex=` included) -- so wiring a real one in is a pure dependency-injection
choice made entirely by the caller (the verification/integration
workstream that DOES have permission to add `redis` to `agent_runtime`'s
dependencies), not something this agent's own code needs to change for.
This module's own unit tests inject a fake, in-memory dict-backed client
satisfying the same protocol, never a real Redis connection.

CACHE-KEY DESIGN (tenant isolation is load-bearing here, not incidental):
`f"navigraph:v1:{tenant_id}:query_cache:policy={policy_version}:{hash}"`.
`tenant_id` is a LITERAL, readable prefix segment of the key -- deliberately
NOT folded into the hash alongside everything else -- so that:

1. Two tenants' cache entries can never collide even if the rest of a
   query's fingerprint (`sql`, `params`, `data_source_id`) is byte-for-byte
   identical (e.g. the exact same generated SQL against two tenants' own,
   separately-registered "REVENUE" tables) -- this is verified directly in
   `tests/test_agent.py` by asserting two different `tenant_id`s produce two
   different keys for otherwise-identical inputs, not merely inferred from
   the format string.
2. A future operational need (e.g. "flush every cached entry for tenant
   X") can be satisfied with a `SCAN`-based prefix match
   (`navigraph:v1:{tenant_id}:*`) without needing to reverse a hash first.

The hash itself covers a CANONICAL (sorted-keys JSON) representation of
`(sql, params, data_source_id)`, so two logically-identical requests whose
`params` dict happens to have been built in a different key order still
hash to the same value and hit the same cache entry.

ERROR CONTRACT: any exception raised by the injected client (e.g. Redis
unreachable, timeout, serialization failure) is caught and turned into a
RECOVERABLE `AgentError(code="cache_backend_unavailable", recoverable=True)`
-- explicitly the opposite default from most of this codebase's other
non-recoverable errors, because a cache-layer failure must never block the
pipeline: the caller can always re-execute against the real data source (a
`lookup` miss) or simply accept the result as non-cached (a `store`
failure). `hit`/`stored` both come back `False` in that case, never a raised
Python exception.
"""

from __future__ import annotations

import hashlib
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

from navigraph_agents.query.caching.contracts import (
    CachingInput,
    CachingOutput,
    CachingResult,
)

AGENT_NAME = "query.caching"

_CACHE_KEY_PREFIX = "navigraph:v1"
_CACHE_KEY_NAMESPACE = "query_cache"


class CacheClientProtocol(Protocol):
    """Minimal structural protocol this agent needs from a cache client.

    A real `redis.Redis` instance satisfies this exactly -- see this
    module's docstring's "REDIS-CLIENT-PROTOCOL ASSUMPTION" section for why
    this agent depends on this protocol rather than on `redis` directly.
    """

    def get(self, key: str) -> bytes | None: ...

    def set(self, key: str, value: bytes, ex: int | None = None) -> Any: ...


def _canonical_fingerprint(*, sql: str, params: dict[str, Any], data_source_id: str) -> str:
    """A canonical (sorted-keys JSON) representation of the query being
    cached, so equivalent requests hash identically regardless of `params`
    dict insertion order."""

    return json.dumps(
        {"sql": sql, "params": params, "data_source_id": data_source_id},
        sort_keys=True,
        default=str,
    )


def build_cache_key(
    *,
    tenant_id: str,
    policy_version: str,
    sql: str,
    params: dict[str, Any],
    data_source_id: str,
) -> str:
    """Build this project's approved cache-key shape.

    `tenant_id` is a literal, readable prefix segment -- NOT merely folded
    into the hash -- per this module's docstring's tenant-isolation design
    note. Exposed as a module-level function (not just inlined in `run()`)
    so a caller that only needs to compute the key a `store`/`lookup` will
    use (e.g. for a cache-invalidation tool) can do so without constructing
    a full `CachingAgent`.
    """

    fingerprint = _canonical_fingerprint(sql=sql, params=params, data_source_id=data_source_id)
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
    return (
        f"{_CACHE_KEY_PREFIX}:{tenant_id}:{_CACHE_KEY_NAMESPACE}:"
        f"policy={policy_version}:{digest}"
    )


class CachingAgent:
    """Looks up or stores a query result against this project's real cache
    key design, via an injected cache client."""

    def __init__(
        self,
        cache_client: CacheClientProtocol,
        tracer: Tracer | None = None,
    ) -> None:
        self._client = cache_client
        self._tracer = tracer or get_tracer("navigraph-agent-runtime")

    async def run(self, input: CachingInput) -> CachingOutput:
        start = time.perf_counter()
        request_context = input.request_context
        payload = input.payload

        cache_key = build_cache_key(
            tenant_id=request_context.tenant_id,
            policy_version=payload.policy_version,
            sql=payload.sql,
            params=payload.params,
            data_source_id=payload.data_source_id,
        )

        errors: list[AgentError] = []
        hit = False
        cached_value: dict[str, Any] | None = None
        stored = False

        with self._tracer.start_as_current_span("agent.caching.run") as span:
            span.set_attribute("navigraph.tenant_id", request_context.tenant_id)
            span.set_attribute("navigraph.trace_id", request_context.trace_id)
            span.set_attribute("navigraph.agent_name", AGENT_NAME)
            span.set_attribute("navigraph.cache_key", cache_key)
            span.set_attribute("navigraph.operation", payload.operation)

            try:
                if payload.operation == "lookup":
                    hit, cached_value = self._lookup(cache_key)
                else:
                    stored = self._store(cache_key, payload.value, payload.ttl_seconds)
            except ValueError as exc:
                # A caller-side contract violation (e.g. "store" with no
                # `value`) -- not a cache-backend failure, so NOT the
                # recoverable code below. Genuinely non-recoverable: no
                # retry against the same malformed request would help.
                errors.append(
                    AgentError(
                        code="invalid_store_payload",
                        message=str(exc),
                        recoverable=False,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - a cache-layer failure must never block the pipeline
                errors.append(
                    AgentError(
                        code="cache_backend_unavailable",
                        message=f"Cache backend call failed: {exc}",
                        recoverable=True,
                    )
                )
                hit = False
                stored = False
                cached_value = None

            result = CachingResult(
                cache_key=cache_key,
                hit=hit,
                cached_value=cached_value,
                stored=stored,
            )

            # A recoverable cache-layer error is still a degraded-but-usable
            # outcome (the caller falls back to non-cached execution), so it
            # gets partial, not zero, confidence -- unlike the non-recoverable
            # `invalid_store_payload` path, which reflects a genuinely
            # malformed request.
            if any(not error.recoverable for error in errors):
                confidence = 0.0
            elif errors:
                confidence = 0.5
            else:
                confidence = 1.0

            lineage_event = LineageEvent(
                agent_name=AGENT_NAME,
                input_summary=f"operation={payload.operation} cache_key={cache_key}",
                output_summary=f"hit={hit} stored={stored} errors={len(errors)}",
                tenant_id=request_context.tenant_id,
                trace_id=request_context.trace_id,
            )

            latency_ms = (time.perf_counter() - start) * 1000.0

            metadata = AgentMetadata(latency_ms=latency_ms)

            span.set_attribute("navigraph.cache_hit", hit)
            span.set_attribute("navigraph.cache_stored", stored)

        record_agent_invocation(AGENT_NAME, latency_ms=latency_ms, success=not errors)
        for error in errors:
            record_agent_error(AGENT_NAME, error_code=error.code, recoverable=error.recoverable)

        return CachingOutput(
            result=result,
            confidence=confidence,
            lineage_events=[lineage_event],
            errors=errors,
            metadata=metadata,
        )

    def _lookup(self, cache_key: str) -> tuple[bool, dict[str, Any] | None]:
        raw = self._client.get(cache_key)
        if raw is None:
            return False, None
        return True, json.loads(raw)

    def _store(self, cache_key: str, value: dict[str, Any] | None, ttl_seconds: int) -> bool:
        if value is None:
            raise ValueError("CachingPayload.value is required when operation='store'")

        serialized = json.dumps(value).encode("utf-8")
        self._client.set(cache_key, serialized, ex=ttl_seconds)
        return True
