"""Real unit tests for the Clarification Coordinator agent.

Uses `FakeLLMClient` exclusively -- no network access, no API key required.
`asyncio_mode = "auto"` is set in packages/agent_runtime/pyproject.toml, so
these `async def test_...` functions run without an explicit
`@pytest.mark.asyncio` decorator.
"""

from __future__ import annotations

import json

from navigraph_shared.contracts import RequestContext
from navigraph_shared.llm import FakeLLMClient

from navigraph_agents.orchestrator.clarification_coordinator.agent import (
    ClarificationCoordinatorAgent,
)
from navigraph_agents.orchestrator.clarification_coordinator.contracts import (
    ClarificationCoordinatorInput,
    ClarificationCoordinatorPayload,
)

# Mirrors agent.py's private `_FIXED_FALLBACK_QUESTION` -- not imported
# (this codebase's tests hardcode a private module constant's literal
# value rather than reaching into it, exactly like follow_up_suggestion's
# tests hardcode its private `_FIXED_EMPTY_RESULT_SUGGESTION` text rather
# than importing it).
_EXPECTED_FIXED_FALLBACK_QUESTION = (
    "I couldn't find data matching your question -- could you rephrase it, "
    "or tell me which table or metric you mean?"
)


def _make_input(
    *,
    original_question: str = "What was the transaction pattern last quarter?",
    failed_stage: str = "understanding.schema_mapping",
    failure_reason: str = "No catalog column matched any term in the question.",
    unmapped_terms: list[str] | None = None,
) -> ClarificationCoordinatorInput:
    return ClarificationCoordinatorInput(
        request_context=RequestContext(
            tenant_id="tenant-acme",
            user_id="user-1",
            trace_id="trace-1",
            roles=["analyst"],
        ),
        payload=ClarificationCoordinatorPayload(
            original_question=original_question,
            failed_stage=failed_stage,
            failure_reason=failure_reason,
            unmapped_terms=["transaction pattern"] if unmapped_terms is None else unmapped_terms,
        ),
    )


async def test_well_formed_response_needing_clarification_is_populated_correctly() -> None:
    fake_llm = FakeLLMClient(
        response=json.dumps(
            {
                "needs_clarification": True,
                "clarifying_question": (
                    "I couldn't find data matching 'transaction pattern' -- could you "
                    "tell me which specific metric or table you're interested in?"
                ),
            }
        )
    )
    agent = ClarificationCoordinatorAgent(llm_client=fake_llm)

    output = await agent.run(_make_input())

    assert output.errors == []
    assert output.confidence == 1.0
    assert output.result.needs_clarification is True
    assert output.result.clarifying_question == (
        "I couldn't find data matching 'transaction pattern' -- could you "
        "tell me which specific metric or table you're interested in?"
    )
    assert len(fake_llm.calls) == 1


async def test_well_formed_response_with_no_clarification_needed_passes_through() -> None:
    fake_llm = FakeLLMClient(
        response=json.dumps({"needs_clarification": False, "clarifying_question": None})
    )
    agent = ClarificationCoordinatorAgent(llm_client=fake_llm)

    output = await agent.run(_make_input())

    assert output.errors == []
    assert output.confidence == 1.0
    assert output.result.needs_clarification is False
    assert output.result.clarifying_question is None


async def test_malformed_top_level_json_falls_back_to_fixed_question() -> None:
    fake_llm = FakeLLMClient(response="this is not json at all")
    agent = ClarificationCoordinatorAgent(llm_client=fake_llm)

    # Must not raise.
    output = await agent.run(_make_input())

    assert output.result.needs_clarification is True
    assert output.result.clarifying_question == _EXPECTED_FIXED_FALLBACK_QUESTION
    assert len(output.errors) == 1
    assert output.errors[0].code == "clarification_llm_response_malformed"
    assert output.errors[0].recoverable is True
    assert output.confidence == 0.5


async def test_response_missing_clarifying_question_key_still_falls_back() -> None:
    """`needs_clarification` is present and valid, but `clarifying_question`
    is entirely absent -- per this agent's deliberate "both fields required"
    validation choice, this is treated as fully malformed, not partially
    trusted."""

    fake_llm = FakeLLMClient(response=json.dumps({"needs_clarification": True}))
    agent = ClarificationCoordinatorAgent(llm_client=fake_llm)

    output = await agent.run(_make_input())

    assert output.result.needs_clarification is True
    assert output.result.clarifying_question == _EXPECTED_FIXED_FALLBACK_QUESTION
    assert len(output.errors) == 1
    assert output.errors[0].code == "clarification_llm_response_malformed"
    assert output.confidence == 0.5


async def test_lineage_event_and_metadata_populate_correctly_in_happy_path() -> None:
    fake_llm = FakeLLMClient(
        response=json.dumps(
            {
                "needs_clarification": True,
                "clarifying_question": "Which specific metric do you mean?",
            }
        ),
        model="fake-clarification-model",
    )
    agent = ClarificationCoordinatorAgent(llm_client=fake_llm)

    output = await agent.run(_make_input())

    assert len(output.lineage_events) == 1
    event = output.lineage_events[0]
    assert event.agent_name == "orchestrator.clarification_coordinator"
    assert event.tenant_id == "tenant-acme"
    assert event.trace_id == "trace-1"
    assert "transaction pattern" in event.input_summary

    assert output.metadata.model_version == "fake-clarification-model"
    assert output.metadata.prompt_version == "v1"
    assert output.metadata.tokens_input == 0
    assert output.metadata.tokens_output == 0
    assert output.metadata.latency_ms >= 0.0


async def test_response_wrapped_in_markdown_code_fence_parses_correctly() -> None:
    """Proves `strip_json_code_fence` is actually being used, not just
    imported -- the real Phase 8 bug fix."""

    fenced_response = (
        "```json\n"
        + json.dumps(
            {
                "needs_clarification": True,
                "clarifying_question": "Which specific metric do you mean?",
            }
        )
        + "\n```"
    )
    fake_llm = FakeLLMClient(response=fenced_response)
    agent = ClarificationCoordinatorAgent(llm_client=fake_llm)

    output = await agent.run(_make_input())

    assert output.errors == []
    assert output.confidence == 1.0
    assert output.result.needs_clarification is True
    assert output.result.clarifying_question == "Which specific metric do you mean?"
