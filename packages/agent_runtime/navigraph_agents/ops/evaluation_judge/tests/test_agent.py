"""Real unit tests for the Evaluation Judge agent.

Uses `FakeLLMClient` exclusively -- no network access, no API key required.
`asyncio_mode = "auto"` is set in packages/agent_runtime/pyproject.toml, so
these `async def test_...` functions run without an explicit
`@pytest.mark.asyncio` decorator.
"""

from __future__ import annotations

import json

from navigraph_shared.contracts import RequestContext
from navigraph_shared.llm import FakeLLMClient

from navigraph_agents.ops.evaluation_judge.agent import EvaluationJudgeAgent
from navigraph_agents.ops.evaluation_judge.contracts import (
    AnomalyFinding,
    ChartSpec,
    EvaluationJudgeInput,
    EvaluationJudgePayload,
    IntentLabel,
)

_FINAL_COLUMNS = ["MARKETID", "UNITS_TOTAL"]
_FINAL_ROWS = [
    {"MARKETID": "Northeast", "UNITS_TOTAL": 120000.0},
    {"MARKETID": "Southwest", "UNITS_TOTAL": 483920.0},
]
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

_WELL_FORMED_RESPONSE = {
    "correctness": {"score": 4, "rationale": "The conclusion follows from the data."},
    "groundedness": {"score": 5, "rationale": "All cited figures appear in the real rows."},
    "narrative_quality": {"score": 4, "rationale": "Clear and concise for a business reader."},
}


def _make_input(
    *,
    expected_intent: IntentLabel = "comparison",
    actual_intent: IntentLabel = "comparison",
    question: str = "Which market had the highest transaction volume?",
    actual_narrative: str = "Southwest reached 483920.0 units, the highest of any market.",
    final_rows: list[dict] | None = None,
    anomalies: list[AnomalyFinding] | None = None,
) -> EvaluationJudgeInput:
    return EvaluationJudgeInput(
        request_context=RequestContext(
            tenant_id="tenant-acme",
            user_id="user-1",
            trace_id="trace-1",
            roles=["analyst"],
        ),
        payload=EvaluationJudgePayload(
            question=question,
            expected_intent=expected_intent,
            expected_entities=["MARKETID"],
            actual_intent=actual_intent,
            actual_narrative=actual_narrative,
            final_columns=_FINAL_COLUMNS,
            final_rows=_FINAL_ROWS if final_rows is None else final_rows,
            chart=_CHART,
            anomalies=_ANOMALIES if anomalies is None else anomalies,
        ),
    )


async def test_well_formed_response_populates_all_dimensions() -> None:
    fake_llm = FakeLLMClient(response=json.dumps(_WELL_FORMED_RESPONSE))
    agent = EvaluationJudgeAgent(llm_client=fake_llm)

    output = await agent.run(_make_input())

    assert output.result.correctness.score == 4
    assert output.result.correctness.rationale == "The conclusion follows from the data."
    assert output.result.groundedness.score == 5
    assert output.result.groundedness.rationale == "All cited figures appear in the real rows."
    assert output.result.narrative_quality.score == 4
    assert (
        output.result.narrative_quality.rationale
        == "Clear and concise for a business reader."
    )
    assert output.errors == []
    assert output.confidence == 1.0


async def test_intent_match_true_computed_without_llm_mentioning_intent() -> None:
    """`intent_match` must be computed purely in Python from
    actual_intent == expected_intent -- the LLM response JSON never
    mentions intent at all."""

    fake_llm = FakeLLMClient(response=json.dumps(_WELL_FORMED_RESPONSE))
    agent = EvaluationJudgeAgent(llm_client=fake_llm)

    output = await agent.run(
        _make_input(expected_intent="comparison", actual_intent="comparison")
    )

    assert output.result.intent_match is True
    # Confirm the LLM response used truly never mentions intent.
    assert "intent" not in json.dumps(_WELL_FORMED_RESPONSE)


async def test_intent_match_false_when_intents_differ() -> None:
    fake_llm = FakeLLMClient(response=json.dumps(_WELL_FORMED_RESPONSE))
    agent = EvaluationJudgeAgent(llm_client=fake_llm)

    output = await agent.run(
        _make_input(expected_intent="comparison", actual_intent="trend_analysis")
    )

    assert output.result.intent_match is False


async def test_malformed_top_level_json_falls_back_all_three_dimensions() -> None:
    fake_llm = FakeLLMClient(response="this is not json at all")
    agent = EvaluationJudgeAgent(llm_client=fake_llm)

    # Must not raise.
    output = await agent.run(_make_input())

    assert output.result.correctness.score == 1
    assert output.result.correctness.rationale == "judge response could not be parsed"
    assert output.result.groundedness.score == 1
    assert output.result.groundedness.rationale == "judge response could not be parsed"
    assert output.result.narrative_quality.score == 1
    assert output.result.narrative_quality.rationale == "judge response could not be parsed"

    malformed_errors = [e for e in output.errors if e.code == "judge_response_malformed"]
    assert len(malformed_errors) == 1
    assert malformed_errors[0].recoverable is True
    assert output.confidence == 0.5


async def test_missing_dimension_key_falls_back_only_that_dimension() -> None:
    """A response missing `narrative_quality` entirely must fall back only
    that one dimension -- the other two, validly-shaped dimensions must
    still populate from the real LLM values."""

    partial_response = {
        "correctness": {"score": 3, "rationale": "Mostly sound, minor imprecision."},
        "groundedness": {"score": 2, "rationale": "One figure not found in the data."},
        # narrative_quality intentionally omitted
    }
    fake_llm = FakeLLMClient(response=json.dumps(partial_response))
    agent = EvaluationJudgeAgent(llm_client=fake_llm)

    output = await agent.run(_make_input())

    assert output.result.correctness.score == 3
    assert output.result.correctness.rationale == "Mostly sound, minor imprecision."
    assert output.result.groundedness.score == 2
    assert output.result.groundedness.rationale == "One figure not found in the data."

    assert output.result.narrative_quality.score == 1
    assert output.result.narrative_quality.rationale == "judge response could not be parsed"

    malformed_errors = [e for e in output.errors if e.code == "judge_response_malformed"]
    assert len(malformed_errors) == 1
    assert "narrative_quality" in malformed_errors[0].message
    assert output.confidence == 0.5


async def test_out_of_range_and_wrong_type_scores_fall_back_correctly() -> None:
    """`score` out of the 1-5 range (e.g. 7) or the wrong type (e.g. a
    string) must fall back that dimension, without discarding the other
    valid dimensions."""

    bad_response = {
        "correctness": {"score": 7, "rationale": "Out of range."},
        "groundedness": {"score": "4", "rationale": "Wrong type."},
        "narrative_quality": {"score": 5, "rationale": "Clear and well-written."},
    }
    fake_llm = FakeLLMClient(response=json.dumps(bad_response))
    agent = EvaluationJudgeAgent(llm_client=fake_llm)

    output = await agent.run(_make_input())

    assert output.result.correctness.score == 1
    assert output.result.correctness.rationale == "judge response could not be parsed"
    assert output.result.groundedness.score == 1
    assert output.result.groundedness.rationale == "judge response could not be parsed"

    # The one validly-shaped dimension survives untouched.
    assert output.result.narrative_quality.score == 5
    assert output.result.narrative_quality.rationale == "Clear and well-written."

    malformed_errors = [e for e in output.errors if e.code == "judge_response_malformed"]
    assert len(malformed_errors) == 2
    assert output.confidence == 0.5


async def test_zero_score_out_of_range_falls_back() -> None:
    bad_response = {
        "correctness": {"score": 0, "rationale": "Zero is out of range."},
        "groundedness": {"score": 5, "rationale": "Fine."},
        "narrative_quality": {"score": 5, "rationale": "Fine."},
    }
    fake_llm = FakeLLMClient(response=json.dumps(bad_response))
    agent = EvaluationJudgeAgent(llm_client=fake_llm)

    output = await agent.run(_make_input())

    assert output.result.correctness.score == 1
    assert output.result.correctness.rationale == "judge response could not be parsed"
    assert output.result.groundedness.score == 5
    assert output.result.narrative_quality.score == 5

    malformed_errors = [e for e in output.errors if e.code == "judge_response_malformed"]
    assert len(malformed_errors) == 1
    assert output.confidence == 0.5


async def test_lineage_event_and_metadata_populated_in_happy_path() -> None:
    fake_llm = FakeLLMClient(
        response=json.dumps(_WELL_FORMED_RESPONSE),
        model="claude-judge-test",
    )
    agent = EvaluationJudgeAgent(llm_client=fake_llm)

    output = await agent.run(_make_input())

    assert len(output.lineage_events) == 1
    lineage_event = output.lineage_events[0]
    assert lineage_event.agent_name == "ops.evaluation_judge"
    assert lineage_event.tenant_id == "tenant-acme"
    assert lineage_event.trace_id == "trace-1"
    assert "correctness=4" in lineage_event.output_summary
    assert "groundedness=5" in lineage_event.output_summary
    assert "narrative_quality=4" in lineage_event.output_summary
    assert "intent_match=True" in lineage_event.output_summary

    assert output.metadata.model_version == "claude-judge-test"
    assert output.metadata.prompt_version == "v1"
    assert output.metadata.tokens_input == 0
    assert output.metadata.tokens_output == 0
    assert output.metadata.latency_ms >= 0

    assert len(fake_llm.calls) == 1
    call = fake_llm.calls[0]
    assert "Southwest" in call["messages"][0]["content"]
    assert call["max_tokens"] == 1024


async def test_large_result_set_and_anomalies_are_capped_in_prompt() -> None:
    """Real bug found live against a real model (see LIMITATIONS.md item
    63): both `final_rows` and `anomalies` were rendered into this agent's
    own judge prompt fully uncapped -- for a real 10,000-row result this
    alone was large enough to make the judge's own response come back
    unparseable. Only the first 200 rows and the top-20-by-|z_score|
    findings should be rendered into the prompt text."""

    many_rows = [
        {"MARKETID": f"Market{i}", "UNITS_TOTAL": float(i)} for i in range(300)
    ]
    many_anomalies = [
        AnomalyFinding(
            row_index=i,
            group_value=f"Market{i}",
            measure_value=float(i),
            z_score=float(30 - i),
            mean=100.0,
            stdev=10.0,
        )
        for i in range(30)
    ]

    fake_llm = FakeLLMClient(response=json.dumps(_WELL_FORMED_RESPONSE))
    agent = EvaluationJudgeAgent(llm_client=fake_llm)

    await agent.run(_make_input(final_rows=many_rows, anomalies=many_anomalies))

    prompt = fake_llm.calls[0]["messages"][0]["content"]
    assert "showing 200 of 300 rows" in prompt
    assert "showing top 20 of 30" in prompt
    assert '"MARKETID": "Market299"' not in prompt
    assert '"MARKETID": "Market0"' in prompt
    assert '"row_index": 29' not in prompt
    assert '"row_index": 0' in prompt
