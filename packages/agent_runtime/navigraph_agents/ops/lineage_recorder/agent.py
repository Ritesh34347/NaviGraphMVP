"""Lineage Recorder agent implementation.

Fully deterministic: no LLM call, no `prompts/` directory. Persists the real
`lineage_events` list from one upstream agent's own invocation into the
real, live lineage store (`navigraph_lineage.api.record_events`) --
idempotently, matched by `event_id`.

Session-access design: the constructor takes a `sessionmaker[Session]`
("session factory"), matching
`navigraph_agents.understanding.metadata_discovery.agent.MetadataDiscoveryAgent`'s
identical constructor pattern -- `run()` opens one `session_scope` per
invocation.

This agent emits its OWN `LineageEvent` too (`agent_name="ops.lineage_recorder"`)
-- recording lineage is itself a lineage-worthy act, and `AgentOutput.lineage_events`
must never be empty per this project's agent-contract convention.
"""

from __future__ import annotations

import time

from navigraph_lineage.api import record_events
from navigraph_lineage.db import session_scope
from navigraph_shared.contracts import AgentError, AgentMetadata, LineageEvent
from navigraph_shared.telemetry import (
    get_tracer,
    record_agent_error,
    record_agent_invocation,
)
from opentelemetry.trace import Tracer
from sqlalchemy.orm import Session, sessionmaker

from navigraph_agents.ops.lineage_recorder.contracts import (
    LineageRecorderInput,
    LineageRecorderOutput,
    LineageRecorderResult,
)

AGENT_NAME = "ops.lineage_recorder"


class LineageRecorderAgent:
    """Idempotently persists one upstream agent's real `lineage_events`
    into the real lineage store."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        tracer: Tracer | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._tracer = tracer or get_tracer("navigraph-agent-runtime")

    async def run(self, input: LineageRecorderInput) -> LineageRecorderOutput:
        start = time.perf_counter()
        request_context = input.request_context
        events = input.payload.events
        trace_id = events[0].trace_id

        errors: list[AgentError] = []
        recorded_count = 0
        duplicate_count = 0

        with self._tracer.start_as_current_span("agent.lineage_recorder.run") as span:
            span.set_attribute("navigraph.tenant_id", request_context.tenant_id)
            span.set_attribute("navigraph.trace_id", request_context.trace_id)
            span.set_attribute("navigraph.agent_name", AGENT_NAME)
            span.set_attribute("navigraph.events_submitted", len(events))

            try:
                with session_scope(self._session_factory) as session:
                    record_result = record_events(session, events=events)
                recorded_count = record_result.recorded_count
                duplicate_count = record_result.duplicate_count
            except Exception as exc:  # noqa: BLE001 - never let a DB-side failure crash the agent
                errors.append(
                    AgentError(
                        code="lineage_persistence_failed",
                        message=f"Failed to persist lineage events: {exc}",
                        recoverable=False,
                    )
                )

            result = LineageRecorderResult(
                recorded_count=recorded_count,
                duplicate_count=duplicate_count,
                trace_id=trace_id,
            )

            confidence = 0.0 if errors else 1.0

            lineage_event = LineageEvent(
                agent_name=AGENT_NAME,
                input_summary=f"events_submitted={len(events)} trace_id={trace_id!r}",
                output_summary=(
                    f"recorded={recorded_count} duplicate={duplicate_count}"
                ),
                tenant_id=request_context.tenant_id,
                trace_id=request_context.trace_id,
            )

            latency_ms = (time.perf_counter() - start) * 1000.0
            metadata = AgentMetadata(latency_ms=latency_ms)

            span.set_attribute("navigraph.recorded_count", recorded_count)
            span.set_attribute("navigraph.duplicate_count", duplicate_count)

        record_agent_invocation(AGENT_NAME, latency_ms=latency_ms, success=not errors)
        for error in errors:
            record_agent_error(AGENT_NAME, error_code=error.code, recoverable=error.recoverable)

        return LineageRecorderOutput(
            result=result,
            confidence=confidence,
            lineage_events=[lineage_event],
            errors=errors,
            metadata=metadata,
        )
