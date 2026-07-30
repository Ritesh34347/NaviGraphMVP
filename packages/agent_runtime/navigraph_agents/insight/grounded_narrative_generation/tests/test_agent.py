"""Real unit tests for the Grounded Narrative Generation agent.

Uses `FakeLLMClient` exclusively -- no network access, no API key required.
`asyncio_mode = "auto"` is set in packages/agent_runtime/pyproject.toml, so
these `async def test_...` functions run without an explicit
`@pytest.mark.asyncio` decorator.
"""

from __future__ import annotations

import json

from navigraph_shared.contracts import RequestContext
from navigraph_shared.llm import FakeLLMClient

from navigraph_agents.insight.grounded_narrative_generation.agent import (
    GroundedNarrativeGenerationAgent,
)
from navigraph_agents.insight.grounded_narrative_generation.contracts import (
    AnomalyFinding,
    ChartSpec,
    NarrativeGenerationInput,
    NarrativeGenerationPayload,
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


def _make_input(
    *,
    final_rows: list[dict] | None = None,
    final_row_count: int | None = None,
    anomalies: list[AnomalyFinding] | None = None,
    question: str = "Which market had the highest transaction volume?",
) -> NarrativeGenerationInput:
    rows = _FINAL_ROWS if final_rows is None else final_rows
    row_count = len(rows) if final_row_count is None else final_row_count

    return NarrativeGenerationInput(
        request_context=RequestContext(
            tenant_id="tenant-acme",
            user_id="user-1",
            trace_id="trace-1",
            roles=["analyst"],
        ),
        payload=NarrativeGenerationPayload(
            original_question=question,
            final_columns=_FINAL_COLUMNS,
            final_rows=rows,
            final_row_count=row_count,
            chart=_CHART,
            anomalies=_ANOMALIES if anomalies is None else anomalies,
        ),
    )


async def test_empty_result_short_circuits_with_no_llm_call() -> None:
    """The zero-row case must not call the LLM at all."""

    fake_llm = FakeLLMClient(response="should never be read")
    agent = GroundedNarrativeGenerationAgent(llm_client=fake_llm)

    output = await agent.run(
        _make_input(final_rows=[], final_row_count=0, anomalies=[])
    )

    assert fake_llm.calls == []
    assert output.result.narrative == "No data was returned for this question."
    assert output.result.citations == []
    assert output.result.unverifiable_numbers == []
    assert output.confidence == 1.0
    assert output.errors == []

    assert len(output.lineage_events) == 1
    assert output.lineage_events[0].agent_name == "insight.grounded_narrative_generation"

    assert output.metadata.model_version is None
    assert output.metadata.prompt_version is None
    assert output.metadata.tokens_input is None
    assert output.metadata.tokens_output is None
    assert output.metadata.latency_ms >= 0


async def test_well_formed_response_with_all_real_citations_survives_intact() -> None:
    fake_llm = FakeLLMClient(
        response=json.dumps(
            {
                "narrative": "Southwest [1] reached 483920.0 units [2].",
                "citations": [
                    {
                        "citation_id": 1,
                        "row_index": 1,
                        "column": "MARKETID",
                        "cited_value": "Southwest",
                    },
                    {
                        "citation_id": 2,
                        "row_index": 1,
                        "column": "UNITS_TOTAL",
                        "cited_value": "483920.0",
                    },
                ],
            }
        )
    )
    agent = GroundedNarrativeGenerationAgent(llm_client=fake_llm)

    output = await agent.run(_make_input())

    assert output.result.narrative == "Southwest [1] reached 483920.0 units [2]."
    assert len(output.result.citations) == 2
    assert output.result.unverifiable_numbers == []
    assert output.errors == []
    assert output.confidence == 1.0

    assert len(fake_llm.calls) == 1
    call = fake_llm.calls[0]
    assert "Southwest" in call["messages"][0]["content"]


async def test_citation_with_wrong_column_is_dropped_but_valid_ones_survive() -> None:
    """A citation naming a real row_index but a column that does not exist
    for that row is a fabrication and must be dropped -- but other, valid
    citations in the same response must still survive."""

    fake_llm = FakeLLMClient(
        response=json.dumps(
            {
                "narrative": "Southwest [1] drove significant volume [2].",
                "citations": [
                    {
                        "citation_id": 1,
                        "row_index": 1,
                        "column": "MARKETID",
                        "cited_value": "Southwest",
                    },
                    {
                        "citation_id": 2,
                        "row_index": 1,
                        "column": "REGION",
                        "cited_value": "Southwest",
                    },
                ],
            }
        )
    )
    agent = GroundedNarrativeGenerationAgent(llm_client=fake_llm)

    output = await agent.run(_make_input())

    assert len(output.result.citations) == 1
    assert output.result.citations[0].citation_id == 1
    assert output.result.citations[0].column == "MARKETID"

    fabricated_errors = [e for e in output.errors if e.code == "llm_cited_fabricated_value"]
    assert len(fabricated_errors) == 1
    assert "REGION" in fabricated_errors[0].message
    assert fabricated_errors[0].recoverable is True
    assert output.confidence == 0.5


async def test_unverifiable_number_in_narrative_is_flagged() -> None:
    """A number the narrative states that matches nothing in the real data
    or anomalies must be reported, independent of the citations chosen."""

    fake_llm = FakeLLMClient(
        response=json.dumps(
            {
                "narrative": "Overall growth was roughly 42% year over year [1].",
                "citations": [
                    {
                        "citation_id": 1,
                        "row_index": 1,
                        "column": "MARKETID",
                        "cited_value": "Southwest",
                    },
                ],
            }
        )
    )
    agent = GroundedNarrativeGenerationAgent(llm_client=fake_llm)

    output = await agent.run(_make_input())

    assert output.result.unverifiable_numbers == ["42"]
    unverified_errors = [
        e for e in output.errors if e.code == "narrative_contains_unverified_number"
    ]
    assert len(unverified_errors) == 1
    assert "42" in unverified_errors[0].message
    assert output.confidence == 0.5
    # The one legitimate citation is untouched by the unrelated unverifiable number.
    assert len(output.result.citations) == 1


async def test_malformed_json_falls_back_gracefully() -> None:
    fake_llm = FakeLLMClient(response="this is not json at all")
    agent = GroundedNarrativeGenerationAgent(llm_client=fake_llm)

    # Must not raise.
    output = await agent.run(_make_input())

    assert output.result.narrative == ""
    assert output.result.citations == []
    assert output.result.unverifiable_numbers == []

    assert len(output.errors) == 1
    assert output.errors[0].code == "narrative_llm_response_malformed"
    assert output.errors[0].recoverable is True
    assert output.confidence == 0.5


async def test_anomaly_derived_citations_validate_successfully() -> None:
    """A citation correctly pointing at an anomaly finding's own z_score/mean
    values (not literal final_rows cells) must validate against the
    anomaly-derived candidate set."""

    fake_llm = FakeLLMClient(
        response=json.dumps(
            {
                "narrative": (
                    "Southwest's volume was a statistical outlier [1], "
                    "far above the typical mean [2]."
                ),
                "citations": [
                    {
                        "citation_id": 1,
                        "row_index": 1,
                        "column": "z_score",
                        "cited_value": "3.2",
                    },
                    {
                        "citation_id": 2,
                        "row_index": 1,
                        "column": "mean",
                        "cited_value": "150000.0",
                    },
                ],
            }
        )
    )
    agent = GroundedNarrativeGenerationAgent(llm_client=fake_llm)

    output = await agent.run(_make_input())

    assert len(output.result.citations) == 2
    assert {c.column for c in output.result.citations} == {"z_score", "mean"}
    assert output.errors == []
    assert output.confidence == 1.0


async def test_large_anomalies_list_is_capped_in_prompt_but_still_fully_validated() -> None:
    """Real bug found live against a real model: an uncapped `anomalies`
    list bloated the prompt enough to produce a malformed response for a
    real, heavy-tailed result set (see LIMITATIONS.md item 63). Only the
    top-20-by-|z_score| findings should appear in the prompt text, but
    citation validation must still succeed against a finding OUTSIDE that
    top 20 -- proving `_build_candidate_values` still uses the full list."""

    many_anomalies = [
        AnomalyFinding(
            row_index=i,
            group_value=f"Group{i}",
            measure_value=float(i),
            z_score=float(30 - i),  # row 0 has the highest |z_score| (30.0), row 29 the lowest (1.0)
            mean=100.0,
            stdev=10.0,
        )
        for i in range(30)
    ]

    fake_llm = FakeLLMClient(
        response=json.dumps(
            {
                "narrative": "Group29 was also notable despite its modest z-score [1].",
                "citations": [
                    {
                        "citation_id": 1,
                        "row_index": 29,
                        "column": "z_score",
                        "cited_value": "1.0",
                    },
                ],
            }
        )
    )
    agent = GroundedNarrativeGenerationAgent(llm_client=fake_llm)

    output = await agent.run(_make_input(anomalies=many_anomalies))

    # The out-of-top-20 citation still validates -- proves full-list validation.
    assert len(output.result.citations) == 1
    assert output.result.citations[0].row_index == 29
    assert output.errors == []

    prompt = fake_llm.calls[0]["messages"][0]["content"]
    assert "showing top 20 of 30" in prompt
    # The lowest-|z_score| finding (row 29, the 21st-30th most extreme) must
    # not actually be rendered into the prompt text itself.
    assert '"row_index": 29' not in prompt
    assert '"row_index": 0' in prompt
