"""Real unit tests for the Anomaly/Outlier Highlighter agent.

No mocking needed -- the agent is a pure function of its input, so these
are real end-to-end tests of `AnomalyOutlierHighlighterAgent.run` against
constructed `AnomalyDetectionInput` payloads. `asyncio_mode = "auto"` is
set at the workspace root `packages/pyproject.toml`, so `async def
test_...` functions run without an explicit `@pytest.mark.asyncio`
decorator.
"""

from __future__ import annotations

import statistics
from typing import Any, Literal

from navigraph_shared.contracts import RequestContext

from navigraph_agents.insight.anomaly_outlier_highlighter.agent import (
    AnomalyOutlierHighlighterAgent,
)
from navigraph_agents.insight.anomaly_outlier_highlighter.contracts import (
    AnomalyDetectionInput,
    AnomalyDetectionPayload,
    ChartSpec,
)


def _request_context() -> RequestContext:
    return RequestContext(
        tenant_id="tenant-acme",
        user_id="user-1",
        trace_id="trace-1",
        roles=["analyst"],
    )


def _chart(
    *,
    x_column: str | None = "MARKETID",
    y_column: str | None = "UNITS_TOTAL",
    chart_type: Literal["bar", "line", "table", "single_value"] = "bar",
) -> ChartSpec:
    return ChartSpec(
        chart_type=chart_type,
        x_column=x_column,
        y_column=y_column,
        rationale="test fixture",
    )


def _make_input(
    *,
    final_rows: list[dict[str, Any]],
    chart: ChartSpec,
    final_columns: list[str] | None = None,
) -> AnomalyDetectionInput:
    return AnomalyDetectionInput(
        request_context=_request_context(),
        payload=AnomalyDetectionPayload(
            final_columns=final_columns or ["MARKETID", "UNITS_TOTAL"],
            final_rows=final_rows,
            final_row_count=len(final_rows),
            chart=chart,
        ),
    )


class TestClearOutlier:
    async def test_one_clear_outlier_is_flagged_with_hand_computed_stats(self) -> None:
        agent = AnomalyOutlierHighlighterAgent()
        # A tight cluster around 100 plus one obvious outlier at 10_000.
        raw_values = [98, 101, 99, 102, 100, 10_000]
        rows = [
            {"MARKETID": f"market-{i}", "UNITS_TOTAL": value}
            for i, value in enumerate(raw_values)
        ]

        expected_mean = statistics.mean(raw_values)
        expected_stdev = statistics.pstdev(raw_values)
        outlier_index = raw_values.index(10_000)
        expected_z = (10_000 - expected_mean) / expected_stdev

        output = await agent.run(_make_input(final_rows=rows, chart=_chart()))
        result = output.result

        assert result.skipped_reason is None
        assert len(result.anomalies) == 1
        finding = result.anomalies[0]
        assert finding.row_index == outlier_index
        assert finding.group_value == f"market-{outlier_index}"
        assert finding.measure_value == 10_000
        assert finding.mean == expected_mean
        assert finding.stdev == expected_stdev
        assert finding.z_score == expected_z
        assert abs(finding.z_score) > 2.0
        assert output.confidence == 1.0

    async def test_no_outliers_among_close_values_is_a_confident_empty_result(self) -> None:
        agent = AnomalyOutlierHighlighterAgent()
        rows = [
            {"MARKETID": "a", "UNITS_TOTAL": 100},
            {"MARKETID": "b", "UNITS_TOTAL": 101},
            {"MARKETID": "c", "UNITS_TOTAL": 99},
            {"MARKETID": "d", "UNITS_TOTAL": 102},
        ]
        output = await agent.run(_make_input(final_rows=rows, chart=_chart()))

        assert output.result.skipped_reason is None
        assert output.result.anomalies == []
        assert output.confidence == 1.0


class TestNoMeasureColumn:
    async def test_no_y_column_is_skipped(self) -> None:
        agent = AnomalyOutlierHighlighterAgent()
        rows = [{"MARKETID": "a"}, {"MARKETID": "b"}, {"MARKETID": "c"}]
        output = await agent.run(
            _make_input(final_rows=rows, chart=_chart(y_column=None, chart_type="table"))
        )

        assert output.result.anomalies == []
        assert output.result.skipped_reason == (
            "chart selection identified no measure column to analyze"
        )
        assert output.confidence == 0.5


class TestTooFewNumericValues:
    async def test_fewer_than_three_numeric_values_is_skipped(self) -> None:
        agent = AnomalyOutlierHighlighterAgent()
        rows = [
            {"MARKETID": "a", "UNITS_TOTAL": 100},
            {"MARKETID": "b", "UNITS_TOTAL": 200},
        ]
        output = await agent.run(_make_input(final_rows=rows, chart=_chart()))

        assert output.result.anomalies == []
        assert output.result.skipped_reason is not None
        assert "fewer than 3" in output.result.skipped_reason
        assert "2 found" in output.result.skipped_reason
        assert output.confidence == 0.5


class TestZeroVariance:
    async def test_all_identical_values_is_skipped_without_crashing(self) -> None:
        agent = AnomalyOutlierHighlighterAgent()
        rows = [
            {"MARKETID": "a", "UNITS_TOTAL": 50},
            {"MARKETID": "b", "UNITS_TOTAL": 50},
            {"MARKETID": "c", "UNITS_TOTAL": 50},
            {"MARKETID": "d", "UNITS_TOTAL": 50},
        ]
        output = await agent.run(_make_input(final_rows=rows, chart=_chart()))

        assert output.result.anomalies == []
        assert output.result.skipped_reason == (
            "zero variance across all groups -- no outliers possible"
        )
        assert output.confidence == 0.5


class TestNonNumericCell:
    async def test_non_numeric_cell_is_skipped_defensively(self) -> None:
        agent = AnomalyOutlierHighlighterAgent()
        rows: list[dict[str, Any]] = [
            {"MARKETID": "a", "UNITS_TOTAL": 100},
            {"MARKETID": "b", "UNITS_TOTAL": "not-a-number"},
            {"MARKETID": "c", "UNITS_TOTAL": 105},
            {"MARKETID": "d", "UNITS_TOTAL": None},
            {"MARKETID": "e", "UNITS_TOTAL": 98},
        ]
        # Should not raise despite the non-numeric/None cells, and those
        # rows should not count toward the numeric population at all.
        output = await agent.run(_make_input(final_rows=rows, chart=_chart()))

        assert output.result.skipped_reason is None
        numeric_values = [100, 105, 98]
        expected_mean = statistics.mean(numeric_values)
        expected_stdev = statistics.pstdev(numeric_values)
        # No outlier among these three close values.
        assert output.result.anomalies == []
        # Sanity: hand-computed stats agree these are all within threshold.
        for value in numeric_values:
            z = (value - expected_mean) / expected_stdev if expected_stdev else 0.0
            assert abs(z) <= 2.0


class TestOutputEnvelope:
    async def test_lineage_and_metadata(self) -> None:
        agent = AnomalyOutlierHighlighterAgent()
        rows = [
            {"MARKETID": "a", "UNITS_TOTAL": 100},
            {"MARKETID": "b", "UNITS_TOTAL": 101},
            {"MARKETID": "c", "UNITS_TOTAL": 99},
        ]
        output = await agent.run(_make_input(final_rows=rows, chart=_chart()))

        assert output.errors == []
        assert len(output.lineage_events) == 1
        assert output.lineage_events[0].agent_name == "insight.anomaly_outlier_highlighter"
        assert output.lineage_events[0].tenant_id == "tenant-acme"
        assert output.lineage_events[0].trace_id == "trace-1"
        assert output.metadata.latency_ms >= 0
        assert output.result.method == "z_score"
        assert output.result.threshold == 2.0
