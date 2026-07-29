"""Real integration test for the Conversation agent against the actual
Anthropic API.

Marked `llm_integration` (registered in packages/agent_runtime/pyproject.toml
under `[tool.pytest.ini_options].markers`). A plain `pytest` run never
executes this file's assertions against the real API: the test is guarded by
`@pytest.mark.skipif` on `ANTHROPIC_API_KEY` being unset, so it *skips*
cleanly (not an error, not a failure) when no key is present. To actually
exercise it against the real API:

    ANTHROPIC_API_KEY=sk-... pytest -m llm_integration
"""

from __future__ import annotations

import os

import pytest
from navigraph_shared.contracts import RequestContext
from navigraph_shared.llm import AnthropicLLMClient

from navigraph_agents.understanding.conversation.agent import ConversationAgent
from navigraph_agents.understanding.conversation.contracts import (
    ConversationInput,
    ConversationPayload,
    ConversationTurn,
)

pytestmark = pytest.mark.llm_integration


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY is not set; skipping real Anthropic API call",
)
async def test_agent_resolves_a_real_follow_up_via_the_real_anthropic_api() -> None:
    llm_client = AnthropicLLMClient()
    agent = ConversationAgent(llm_client=llm_client)

    history = [
        ConversationTurn(
            turn_id="turn_1",
            raw_question="What was total transaction volume by market last month?",
            resolved_question="What was total transaction volume by market last month?",
            intent="metric_lookup",
            entities=["transaction volume", "market", "last month"],
        ),
        ConversationTurn(
            turn_id="turn_2",
            raw_question="what about last quarter instead?",
            resolved_question="What was total transaction volume by market last quarter?",
            intent="metric_lookup",
            entities=["transaction volume", "market", "last quarter"],
        ),
    ]

    agent_input = ConversationInput(
        request_context=RequestContext(
            tenant_id="tenant-integration-test",
            user_id="user-1",
            trace_id="trace-1",
        ),
        payload=ConversationPayload(
            question="and for Premium customers?",
            conversation_history=history,
        ),
    )

    output = await agent.run(agent_input)

    # We don't assert the exact rewrite text (model output can vary
    # slightly), but the call must have actually reached the real API
    # (non-zero token usage) and must have recognized this as a follow-up
    # that no longer matches the raw question verbatim.
    assert output.metadata.tokens_input is not None
    assert output.metadata.tokens_input > 0
    assert output.result.is_follow_up is True
    assert output.result.resolved_question != output.result.raw_question
