"""Real unit tests for the Conversation agent.

Uses `FakeLLMClient` exclusively -- no network access, no API key required.
`asyncio_mode = "auto"` is set in packages/agent_runtime/pyproject.toml, so
these `async def test_...` functions run without an explicit
`@pytest.mark.asyncio` decorator.
"""

from __future__ import annotations

from navigraph_shared.contracts import RequestContext
from navigraph_shared.llm import FakeLLMClient

from navigraph_agents.understanding.conversation.agent import ConversationAgent
from navigraph_agents.understanding.conversation.contracts import (
    ConversationInput,
    ConversationPayload,
    ConversationTurn,
)


def _make_input(
    question: str,
    history: list[ConversationTurn] | None = None,
) -> ConversationInput:
    return ConversationInput(
        request_context=RequestContext(
            tenant_id="tenant-acme",
            user_id="user-1",
            trace_id="trace-1",
            roles=["analyst"],
        ),
        payload=ConversationPayload(
            question=question,
            conversation_history=history or [],
        ),
    )


async def test_empty_history_short_circuits_with_no_llm_call() -> None:
    """The single most important behavior in this agent: when there's no
    conversation history, this must NOT call the LLM at all."""

    fake_llm = FakeLLMClient(response="should never be read")
    agent = ConversationAgent(llm_client=fake_llm)

    output = await agent.run(_make_input("What was our revenue last quarter?"))

    # Zero calls recorded -- proves the LLM was never invoked.
    assert fake_llm.calls == []

    assert output.result.resolved_question == "What was our revenue last quarter?"
    assert output.result.raw_question == "What was our revenue last quarter?"
    assert output.result.is_follow_up is False
    assert output.result.referenced_turn_id is None
    assert output.confidence == 1.0
    assert output.errors == []

    assert len(output.lineage_events) == 1
    lineage = output.lineage_events[0]
    assert lineage.agent_name == "understanding.conversation"
    assert lineage.tenant_id == "tenant-acme"
    assert lineage.trace_id == "trace-1"

    # No LLM call happened, so there's genuinely nothing to report here.
    assert output.metadata.model_version is None
    assert output.metadata.prompt_version is None
    assert output.metadata.tokens_input is None
    assert output.metadata.tokens_output is None
    assert output.metadata.latency_ms >= 0


async def test_non_empty_history_with_valid_follow_up_response_rewrites_question() -> None:
    history = [
        ConversationTurn(
            turn_id="turn_1",
            raw_question="What was total transaction volume by market last month?",
            resolved_question="What was total transaction volume by market last month?",
            intent="metric_lookup",
            entities=["transaction volume", "market", "last month"],
        )
    ]
    fake_llm = FakeLLMClient(
        response=(
            '{"is_follow_up": true, "referenced_turn_id": "turn_1", '
            '"resolved_question": "What was total transaction volume by market last quarter?"}'
        )
    )
    agent = ConversationAgent(llm_client=fake_llm)

    output = await agent.run(_make_input("what about last quarter instead?", history))

    assert output.result.is_follow_up is True
    assert output.result.referenced_turn_id == "turn_1"
    assert (
        output.result.resolved_question
        == "What was total transaction volume by market last quarter?"
    )
    assert output.result.raw_question == "what about last quarter instead?"
    assert output.confidence == 1.0
    assert output.errors == []

    assert output.metadata.model_version == "fake-model"
    assert output.metadata.prompt_version == "v1"
    assert output.metadata.tokens_input == 0
    assert output.metadata.tokens_output == 0

    # Assert on exactly what was sent to the "model".
    assert len(fake_llm.calls) == 1
    call = fake_llm.calls[0]
    assert "turn_1" in call["messages"][0]["content"]
    assert "what about last quarter instead?" in call["messages"][0]["content"]


async def test_non_empty_history_with_new_standalone_question() -> None:
    history = [
        ConversationTurn(
            turn_id="turn_1",
            raw_question="What was total transaction volume by market last month?",
            resolved_question="What was total transaction volume by market last month?",
        )
    ]
    fake_llm = FakeLLMClient(
        response='{"is_follow_up": false, "referenced_turn_id": null, '
        '"resolved_question": "How many active merchants do we have in APAC today?"}'
    )
    agent = ConversationAgent(llm_client=fake_llm)

    output = await agent.run(
        _make_input("How many active merchants do we have in APAC today?", history)
    )

    assert output.result.is_follow_up is False
    assert output.result.referenced_turn_id is None
    assert output.result.resolved_question == "How many active merchants do we have in APAC today?"
    assert output.errors == []


async def test_non_empty_history_with_malformed_json_falls_back_gracefully() -> None:
    history = [
        ConversationTurn(
            turn_id="turn_1",
            raw_question="What was total transaction volume by market last month?",
            resolved_question="What was total transaction volume by market last month?",
        )
    ]
    fake_llm = FakeLLMClient(response="this is not json at all")
    agent = ConversationAgent(llm_client=fake_llm)

    # Must not raise.
    output = await agent.run(_make_input("what about last quarter instead?", history))

    assert output.result.is_follow_up is False
    assert output.result.referenced_turn_id is None
    assert output.result.resolved_question == "what about last quarter instead?"
    assert output.confidence == 0.0
    assert len(output.errors) == 1
    assert output.errors[0].code == "llm_response_not_json"
    assert output.errors[0].recoverable is True

    assert len(output.lineage_events) == 1
    assert output.metadata.latency_ms >= 0


async def test_non_empty_history_with_invalid_resolved_question_falls_back_gracefully() -> None:
    """LLM claims is_follow_up=true but doesn't give us a usable rewrite --
    must not trust a guess, must fall back to treating it as not a follow-up."""

    history = [
        ConversationTurn(
            turn_id="turn_1",
            raw_question="What was total transaction volume by market last month?",
            resolved_question="What was total transaction volume by market last month?",
        )
    ]
    fake_llm = FakeLLMClient(
        response='{"is_follow_up": true, "referenced_turn_id": "turn_1", "resolved_question": ""}'
    )
    agent = ConversationAgent(llm_client=fake_llm)

    output = await agent.run(_make_input("what about last quarter instead?", history))

    assert output.result.is_follow_up is False
    assert output.result.referenced_turn_id is None
    assert output.result.resolved_question == "what about last quarter instead?"
    assert output.errors[0].code == "llm_response_invalid_resolved_question"


async def test_non_empty_history_with_llm_call_failure_falls_back_gracefully() -> None:
    def _raise(system, messages, max_tokens):
        raise RuntimeError("simulated network failure")

    history = [
        ConversationTurn(
            turn_id="turn_1",
            raw_question="What was total transaction volume by market last month?",
            resolved_question="What was total transaction volume by market last month?",
        )
    ]
    fake_llm = FakeLLMClient(response_fn=_raise)
    agent = ConversationAgent(llm_client=fake_llm)

    output = await agent.run(_make_input("what about last quarter instead?", history))

    assert output.result.is_follow_up is False
    assert output.result.resolved_question == "what about last quarter instead?"
    assert output.errors[0].code == "llm_call_failed"
    assert output.errors[0].recoverable is False
    assert output.metadata.model_version is None
    assert output.metadata.tokens_input is None
