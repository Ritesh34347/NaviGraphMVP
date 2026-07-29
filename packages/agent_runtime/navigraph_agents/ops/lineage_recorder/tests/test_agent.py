"""Real unit tests for the Lineage Recorder agent, DB-free.

Mirrors `understanding/metadata_discovery/tests/test_agent.py`'s "mock the
session layer, assert on shape" convention: `navigraph_lineage.api.record_events`
and `navigraph_lineage.db.session_scope` are patched at the point they're
imported into `agent.py`, so no live Postgres is needed for this unit tier.
The real idempotent-insert-against-a-live-database behavior is exercised by
`tests/integration/lineage_pipeline/`.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from navigraph_lineage.api import RecordEventsResult
from navigraph_shared.contracts import LineageEvent, RequestContext

from navigraph_agents.ops.lineage_recorder.agent import LineageRecorderAgent
from navigraph_agents.ops.lineage_recorder.contracts import (
    LineageRecorderInput,
    LineageRecorderPayload,
)

_AGENT_MODULE = "navigraph_agents.ops.lineage_recorder.agent"


def _event(event_id: str, agent_name: str = "understanding.conversation") -> LineageEvent:
    return LineageEvent(
        event_id=event_id,
        agent_name=agent_name,
        timestamp=datetime(2026, 7, 29, tzinfo=timezone.utc),
        input_summary="input",
        output_summary="output",
        tenant_id="navikenz-poc",
        trace_id="trace-1",
    )


def _make_input(events: list[LineageEvent]) -> LineageRecorderInput:
    return LineageRecorderInput(
        request_context=RequestContext(
            tenant_id="navikenz-poc",
            user_id="user-1",
            trace_id="trace-1",
            roles=["analyst"],
        ),
        payload=LineageRecorderPayload(events=events),
    )


@contextmanager
def _fake_session_scope(_factory):
    yield MagicMock()


async def test_new_events_are_recorded_and_lineage_event_emitted() -> None:
    events = [_event("e1"), _event("e2")]

    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        patch(
            f"{_AGENT_MODULE}.record_events",
            return_value=RecordEventsResult(recorded_count=2, duplicate_count=0),
        ) as mock_record,
    ):
        agent = LineageRecorderAgent(session_factory=MagicMock())
        output = await agent.run(_make_input(events))

    mock_record.assert_called_once()
    assert output.result.recorded_count == 2
    assert output.result.duplicate_count == 0
    assert output.result.trace_id == "trace-1"
    assert output.errors == []
    assert output.confidence == 1.0
    # This agent must emit its OWN lineage event too, per the universal
    # "lineage_events must never be empty" agent-contract rule.
    assert len(output.lineage_events) == 1
    assert output.lineage_events[0].agent_name == "ops.lineage_recorder"


async def test_duplicate_events_are_reported_not_treated_as_errors() -> None:
    events = [_event("e1"), _event("e2"), _event("e3")]

    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        patch(
            f"{_AGENT_MODULE}.record_events",
            return_value=RecordEventsResult(recorded_count=1, duplicate_count=2),
        ),
    ):
        agent = LineageRecorderAgent(session_factory=MagicMock())
        output = await agent.run(_make_input(events))

    assert output.result.recorded_count == 1
    assert output.result.duplicate_count == 2
    assert output.errors == []
    assert output.confidence == 1.0


async def test_persistence_failure_is_a_non_recoverable_agent_error() -> None:
    events = [_event("e1")]

    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        patch(f"{_AGENT_MODULE}.record_events", side_effect=RuntimeError("connection refused")),
    ):
        agent = LineageRecorderAgent(session_factory=MagicMock())
        output = await agent.run(_make_input(events))

    assert output.result.recorded_count == 0
    assert output.result.duplicate_count == 0
    assert len(output.errors) == 1
    assert output.errors[0].code == "lineage_persistence_failed"
    assert output.errors[0].recoverable is False
    assert output.confidence == 0.0
    # Even on failure, this agent still emits its own lineage event.
    assert len(output.lineage_events) == 1


async def test_trace_id_is_taken_from_the_submitted_events() -> None:
    events = [_event("e1"), _event("e2")]

    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        patch(
            f"{_AGENT_MODULE}.record_events",
            return_value=RecordEventsResult(recorded_count=2, duplicate_count=0),
        ),
    ):
        agent = LineageRecorderAgent(session_factory=MagicMock())
        output = await agent.run(_make_input(events))

    assert output.result.trace_id == "trace-1"
