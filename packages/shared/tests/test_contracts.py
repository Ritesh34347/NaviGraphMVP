"""Contract-level tests for the shared agent I/O models.

The single most important test in this file is
`test_request_context_requires_tenant_id` -- it is the first tiny piece of
evidence that tenant isolation is structurally non-optional in this
codebase: you cannot construct a `RequestContext` (and therefore cannot
construct any `AgentInput`) without a `tenant_id`.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from navigraph_shared.contracts import (
    AgentError,
    AgentInput,
    AgentMetadata,
    AgentOutput,
    LineageEvent,
    RequestContext,
)


def _make_request_context(**overrides) -> RequestContext:
    defaults = {
        "tenant_id": "tenant-acme",
        "user_id": "user-123",
        "trace_id": "trace-abc",
        "roles": ["analyst"],
    }
    defaults.update(overrides)
    return RequestContext(**defaults)


def test_request_context_constructs_with_required_fields() -> None:
    ctx = _make_request_context()
    assert ctx.tenant_id == "tenant-acme"
    assert ctx.user_id == "user-123"
    assert ctx.trace_id == "trace-abc"
    assert ctx.roles == ["analyst"]
    assert ctx.claims == {}


def test_request_context_requires_tenant_id() -> None:
    """This is the seed of the tenant-isolation discipline: omitting
    tenant_id must fail loudly at construction time, not silently default
    to something unscoped."""

    with pytest.raises(ValidationError) as exc_info:
        RequestContext(user_id="user-123", trace_id="trace-abc")  # type: ignore[call-arg]

    errors = exc_info.value.errors()
    assert any(err["loc"] == ("tenant_id",) for err in errors)


def test_request_context_requires_user_id_and_trace_id_too() -> None:
    with pytest.raises(ValidationError):
        RequestContext(tenant_id="tenant-acme")  # type: ignore[call-arg]


def test_agent_input_requires_request_context() -> None:
    with pytest.raises(ValidationError):
        AgentInput()  # type: ignore[call-arg]


def test_agent_input_constructs_with_request_context() -> None:
    ctx = _make_request_context()
    agent_input = AgentInput(request_context=ctx)
    assert agent_input.request_context.tenant_id == "tenant-acme"


def test_agent_output_constructs_with_required_fields() -> None:
    metadata = AgentMetadata(latency_ms=12.5, model_version="claude-sonnet-5")
    output = AgentOutput(result={"answer": 42}, metadata=metadata)

    assert output.result == {"answer": 42}
    assert output.confidence is None
    assert output.lineage_events == []
    assert output.errors == []
    assert output.metadata.latency_ms == 12.5


def test_agent_output_requires_metadata() -> None:
    with pytest.raises(ValidationError):
        AgentOutput(result="x")  # type: ignore[call-arg]


def test_lineage_event_carries_tenancy_and_trace() -> None:
    event = LineageEvent(
        agent_name="understanding.intent_understanding",
        input_summary="question: what is revenue?",
        output_summary="intent: metric_lookup",
        tenant_id="tenant-acme",
        trace_id="trace-abc",
    )
    assert event.event_id.startswith("lineage_")
    assert event.tenant_id == "tenant-acme"
    assert event.trace_id == "trace-abc"


def test_agent_error_shape() -> None:
    error = AgentError(code="llm_json_parse_error", message="malformed JSON", recoverable=True)
    assert error.recoverable is True


def test_full_agent_output_with_lineage_and_errors() -> None:
    ctx = _make_request_context()
    metadata = AgentMetadata(
        latency_ms=42.0,
        model_version="claude-sonnet-5",
        prompt_version="v1",
        tokens_input=10,
        tokens_output=5,
    )
    lineage = LineageEvent(
        agent_name="understanding.intent_understanding",
        input_summary="q",
        output_summary="r",
        tenant_id=ctx.tenant_id,
        trace_id=ctx.trace_id,
    )
    error = AgentError(code="fallback", message="used fallback intent", recoverable=True)

    output = AgentOutput(
        result={"intent": "unknown"},
        confidence=0.0,
        lineage_events=[lineage],
        errors=[error],
        metadata=metadata,
    )

    assert output.lineage_events[0].tenant_id == "tenant-acme"
    assert output.errors[0].code == "fallback"
