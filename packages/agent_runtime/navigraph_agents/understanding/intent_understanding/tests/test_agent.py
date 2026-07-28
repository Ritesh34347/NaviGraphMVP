"""Real unit tests for the Intent Understanding agent.

Uses `FakeLLMClient` exclusively -- no network access, no API key required.
`asyncio_mode = "auto"` is set in packages/agent_runtime/pyproject.toml, so
these `async def test_...` functions run without an explicit
`@pytest.mark.asyncio` decorator.
"""

from __future__ import annotations

import pytest
from navigraph_shared.contracts import RequestContext
from navigraph_shared.llm import FakeLLMClient
from pydantic import ValidationError

from navigraph_agents.understanding.intent_understanding.agent import (
    IntentUnderstandingAgent,
)
from navigraph_agents.understanding.intent_understanding.contracts import (
    IntentUnderstandingInput,
    IntentUnderstandingPayload,
)


def _make_input(question: str = "What was our revenue last quarter?") -> IntentUnderstandingInput:
    return IntentUnderstandingInput(
        request_context=RequestContext(
            tenant_id="tenant-acme",
            user_id="user-1",
            trace_id="trace-1",
            roles=["analyst"],
        ),
        payload=IntentUnderstandingPayload(question=question),
    )


async def test_agent_parses_valid_json_response() -> None:
    fake_llm = FakeLLMClient(
        response='{"intent": "metric_lookup", "entities": ["revenue", "last quarter"]}'
    )
    agent = IntentUnderstandingAgent(llm_client=fake_llm)

    output = await agent.run(_make_input())

    assert output.result.intent == "metric_lookup"
    assert output.result.entities == ["revenue", "last quarter"]
    assert output.result.raw_question == "What was our revenue last quarter?"
    assert output.confidence == 1.0
    assert output.errors == []

    assert len(output.lineage_events) == 1
    lineage = output.lineage_events[0]
    assert lineage.agent_name == "understanding.intent_understanding"
    assert lineage.tenant_id == "tenant-acme"
    assert lineage.trace_id == "trace-1"

    assert output.metadata.latency_ms >= 0
    assert output.metadata.model_version == "fake-model"
    assert output.metadata.prompt_version == "v1"
    assert output.metadata.tokens_input == 0
    assert output.metadata.tokens_output == 0

    # Assert on exactly what was sent to the "model".
    assert len(fake_llm.calls) == 1
    call = fake_llm.calls[0]
    assert call["messages"] == [{"role": "user", "content": "What was our revenue last quarter?"}]
    assert "metric_lookup" in call["system"]  # system prompt documents the vocabulary


async def test_agent_handles_malformed_json_gracefully() -> None:
    fake_llm = FakeLLMClient(response="this is not json at all")
    agent = IntentUnderstandingAgent(llm_client=fake_llm)

    # Must not raise.
    output = await agent.run(_make_input("asdkjasldkj"))

    assert output.result.intent == "unknown"
    assert output.result.entities == []
    assert output.confidence == 0.0
    assert len(output.errors) == 1
    assert output.errors[0].code == "llm_response_not_json"
    assert output.errors[0].recoverable is True

    # Lineage and metadata are still produced even on the fallback path.
    assert len(output.lineage_events) == 1
    assert output.metadata.latency_ms >= 0


async def test_agent_handles_unrecognized_intent_gracefully() -> None:
    fake_llm = FakeLLMClient(response='{"intent": "do_something_wild", "entities": []}')
    agent = IntentUnderstandingAgent(llm_client=fake_llm)

    output = await agent.run(_make_input())

    assert output.result.intent == "unknown"
    assert output.confidence == 0.0
    assert output.errors[0].code == "llm_response_invalid_intent"


async def test_agent_handles_llm_call_failure_gracefully() -> None:
    def _raise(system, messages, max_tokens):
        raise RuntimeError("simulated network failure")

    fake_llm = FakeLLMClient(response_fn=_raise)
    agent = IntentUnderstandingAgent(llm_client=fake_llm)

    output = await agent.run(_make_input())

    assert output.result.intent == "unknown"
    assert output.errors[0].code == "llm_call_failed"
    assert output.errors[0].recoverable is False
    assert output.metadata.model_version is None
    assert output.metadata.tokens_input is None


def test_request_context_without_tenant_id_fails_at_construction() -> None:
    """Reuses the contract-level guarantee: an IntentUnderstandingInput can
    never be constructed without a tenant-scoped RequestContext."""

    with pytest.raises(ValidationError):
        IntentUnderstandingInput(
            request_context=RequestContext(user_id="user-1", trace_id="trace-1"),  # type: ignore[call-arg]
            payload=IntentUnderstandingPayload(question="test"),
        )
