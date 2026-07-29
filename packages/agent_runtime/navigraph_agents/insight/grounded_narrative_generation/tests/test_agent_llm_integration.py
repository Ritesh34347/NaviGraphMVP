"""Real integration test for the Grounded Narrative Generation agent against
the actual Anthropic API.

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

from navigraph_agents.insight.grounded_narrative_generation.agent import (
    GroundedNarrativeGenerationAgent,
)
from navigraph_agents.insight.grounded_narrative_generation.contracts import (
    ChartSpec,
    NarrativeGenerationInput,
    NarrativeGenerationPayload,
)

pytestmark = pytest.mark.llm_integration


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY is not set; skipping real Anthropic API call",
)
async def test_agent_produces_a_grounded_narrative_via_the_real_anthropic_api() -> None:
    llm_client = AnthropicLLMClient()
    agent = GroundedNarrativeGenerationAgent(llm_client=llm_client)

    final_rows = [
        {"MARKETID": "Northeast", "UNITS_TOTAL": 120000.0},
        {"MARKETID": "Southwest", "UNITS_TOTAL": 483920.0},
    ]

    agent_input = NarrativeGenerationInput(
        request_context=RequestContext(
            tenant_id="tenant-integration-test",
            user_id="user-1",
            trace_id="trace-1",
        ),
        payload=NarrativeGenerationPayload(
            original_question="Which market had the highest transaction volume?",
            final_columns=["MARKETID", "UNITS_TOTAL"],
            final_rows=final_rows,
            final_row_count=len(final_rows),
            chart=ChartSpec(
                chart_type="bar",
                x_column="MARKETID",
                y_column="UNITS_TOTAL",
                rationale="Comparing a measure across a categorical dimension.",
            ),
            anomalies=[],
        ),
    )

    output = await agent.run(agent_input)

    # We don't hard-assert the exact narrative wording (model output can
    # vary), but the call must have actually reached the real API (non-zero
    # token usage), and it must have produced at least one citation that
    # survives the agent's own grounding validation -- if the model wrote a
    # narrative with no citations at all, or every citation it gave was
    # fabricated, this would be zero.
    assert output.metadata.tokens_input is not None
    assert output.metadata.tokens_input > 0
    assert output.result.narrative != ""
    assert len(output.result.citations) > 0
    for citation in output.result.citations:
        assert citation.column in {"MARKETID", "UNITS_TOTAL"}
