"""Real integration test for the Intent Understanding agent against the
actual Anthropic API.

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

from navigraph_agents.understanding.intent_understanding.agent import (
    IntentUnderstandingAgent,
)
from navigraph_agents.understanding.intent_understanding.contracts import (
    IntentUnderstandingInput,
    IntentUnderstandingPayload,
)

pytestmark = pytest.mark.llm_integration


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY is not set; skipping real Anthropic API call",
)
async def test_agent_classifies_a_real_question_via_the_real_anthropic_api() -> None:
    llm_client = AnthropicLLMClient()
    agent = IntentUnderstandingAgent(llm_client=llm_client)

    agent_input = IntentUnderstandingInput(
        request_context=RequestContext(
            tenant_id="tenant-integration-test",
            user_id="user-1",
            trace_id="trace-1",
        ),
        payload=IntentUnderstandingPayload(
            question="What was our total revenue in Q1 2026?"
        ),
    )

    output = await agent.run(agent_input)

    # We don't assert the exact intent (model output can vary slightly),
    # but it must be one of the controlled-vocabulary values, and the call
    # must actually have reached the real API (non-zero token usage).
    assert output.result.intent in {
        "metric_lookup",
        "trend_analysis",
        "comparison",
        "anomaly_investigation",
        "unknown",
    }
    assert output.metadata.tokens_input is not None
    assert output.metadata.tokens_input > 0
