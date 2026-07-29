"""Real unit tests for the Follow-Up Suggestion agent.

Uses `FakeLLMClient` exclusively -- no network access, no API key required.
`asyncio_mode = "auto"` is set in packages/agent_runtime/pyproject.toml, so
these `async def test_...` functions run without an explicit
`@pytest.mark.asyncio` decorator.
"""

from __future__ import annotations

import json

from navigraph_shared.contracts import RequestContext
from navigraph_shared.llm import FakeLLMClient

from navigraph_agents.insight.follow_up_suggestion.agent import FollowUpSuggestionAgent
from navigraph_agents.insight.follow_up_suggestion.contracts import (
    AnomalyFinding,
    ChartSpec,
    FollowUpSuggestionInput,
    FollowUpSuggestionPayload,
)

_CHART = ChartSpec(
    chart_type="bar",
    x_column="MARKETID",
    y_column="UNITS_TOTAL",
    rationale="Comparing a measure across a categorical dimension.",
)
_ANOMALIES = [
    AnomalyFinding(
        row_index=1,
        group_value="Southwest",
        measure_value=483920.0,
        z_score=3.2,
        mean=150000.0,
        stdev=50000.0,
    )
]


def _make_input(
    *,
    final_row_count: int = 2,
    anomalies: list[AnomalyFinding] | None = None,
    narrative: str = "Southwest had the highest transaction volume at 483,920 units.",
    question: str = "Which market had the highest transaction volume?",
) -> FollowUpSuggestionInput:
    return FollowUpSuggestionInput(
        request_context=RequestContext(
            tenant_id="tenant-acme",
            user_id="user-1",
            trace_id="trace-1",
            roles=["analyst"],
        ),
        payload=FollowUpSuggestionPayload(
            original_question=question,
            narrative=narrative,
            final_columns=["MARKETID", "UNITS_TOTAL"],
            final_row_count=final_row_count,
            chart=_CHART,
            anomalies=_ANOMALIES if anomalies is None else anomalies,
        ),
    )


async def test_empty_result_short_circuits_with_no_llm_call() -> None:
    fake_llm = FakeLLMClient(response="should never be read")
    agent = FollowUpSuggestionAgent(llm_client=fake_llm)

    output = await agent.run(_make_input(final_row_count=0, anomalies=[]))

    assert fake_llm.calls == []
    assert len(output.result.suggestions) == 1
    assert (
        output.result.suggestions[0].question
        == "Would you like to try a broader or different question?"
    )
    assert output.result.suggestions[0].rationale is None
    assert output.confidence == 1.0
    assert output.errors == []

    assert len(output.lineage_events) == 1
    assert output.lineage_events[0].agent_name == "insight.follow_up_suggestion"

    assert output.metadata.model_version is None
    assert output.metadata.prompt_version is None
    assert output.metadata.tokens_input is None
    assert output.metadata.tokens_output is None


async def test_well_formed_response_with_two_suggestions_survives_intact() -> None:
    fake_llm = FakeLLMClient(
        response=json.dumps(
            {
                "suggestions": [
                    {
                        "question": "Did any single account drive this spike?",
                        "rationale": "Checks if the spike is broad-based or concentrated.",
                    },
                    {
                        "question": "How does Southwest compare to the same quarter last year?",
                        "rationale": "Checks whether this is seasonal.",
                    },
                ]
            }
        )
    )
    agent = FollowUpSuggestionAgent(llm_client=fake_llm)

    output = await agent.run(_make_input())

    assert len(output.result.suggestions) == 2
    assert output.result.suggestions[0].question == "Did any single account drive this spike?"
    assert output.errors == []
    assert output.confidence == 1.0

    assert len(fake_llm.calls) == 1


async def test_empty_question_is_dropped_but_valid_one_survives() -> None:
    fake_llm = FakeLLMClient(
        response=json.dumps(
            {
                "suggestions": [
                    {"question": "   ", "rationale": "this one is blank"},
                    {"question": "Was there a pricing change in Southwest?", "rationale": None},
                ]
            }
        )
    )
    agent = FollowUpSuggestionAgent(llm_client=fake_llm)

    output = await agent.run(_make_input())

    assert len(output.result.suggestions) == 1
    assert output.result.suggestions[0].question == "Was there a pricing change in Southwest?"
    assert output.errors == []
    assert output.confidence == 1.0


async def test_more_than_three_suggestions_is_truncated_without_error() -> None:
    fake_llm = FakeLLMClient(
        response=json.dumps(
            {
                "suggestions": [
                    {"question": f"Follow-up question {i}?", "rationale": None}
                    for i in range(5)
                ]
            }
        )
    )
    agent = FollowUpSuggestionAgent(llm_client=fake_llm)

    output = await agent.run(_make_input())

    assert len(output.result.suggestions) == 3
    assert [s.question for s in output.result.suggestions] == [
        "Follow-up question 0?",
        "Follow-up question 1?",
        "Follow-up question 2?",
    ]
    assert output.errors == []
    assert output.confidence == 1.0


async def test_malformed_json_falls_back_gracefully() -> None:
    fake_llm = FakeLLMClient(response="this is not json at all")
    agent = FollowUpSuggestionAgent(llm_client=fake_llm)

    # Must not raise.
    output = await agent.run(_make_input())

    assert output.result.suggestions == []
    assert output.confidence == 0.0

    codes = {e.code for e in output.errors}
    assert "follow_up_llm_response_malformed" in codes
    assert "no_valid_suggestions_returned" in codes


async def test_suggestion_referencing_ungrounded_concept_is_accepted() -> None:
    """Proves the deliberate grounding exemption is real: a suggestion that
    references a concept ("account") not present anywhere in
    `final_columns` must still be accepted, not rejected."""

    fake_llm = FakeLLMClient(
        response=json.dumps(
            {
                "suggestions": [
                    {
                        "question": "Did any single account drive this spike?",
                        "rationale": "Account is not one of the queried columns.",
                    },
                ]
            }
        )
    )
    agent = FollowUpSuggestionAgent(llm_client=fake_llm)

    output = await agent.run(_make_input())

    assert "account" not in [c.lower() for c in ["MARKETID", "UNITS_TOTAL"]]
    assert len(output.result.suggestions) == 1
    assert output.result.suggestions[0].question == "Did any single account drive this spike?"
    assert output.errors == []
    assert output.confidence == 1.0
