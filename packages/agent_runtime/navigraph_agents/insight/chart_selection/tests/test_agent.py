"""Real unit tests for the Chart Selection agent.

No mocking needed -- the agent is a pure function of its input, so these
are real end-to-end tests of `ChartSelectionAgent.run` against constructed
`ChartSelectionInput` payloads. `asyncio_mode = "auto"` is set at the
workspace root `packages/pyproject.toml`, so `async def test_...`
functions run without an explicit `@pytest.mark.asyncio` decorator.
"""

from __future__ import annotations

from typing import Any, Literal

from navigraph_shared.contracts import RequestContext

from navigraph_agents.insight.chart_selection.agent import ChartSelectionAgent
from navigraph_agents.insight.chart_selection.contracts import (
    ChartColumnRef,
    ChartSelectionInput,
    ChartSelectionPayload,
)


def _request_context() -> RequestContext:
    return RequestContext(
        tenant_id="tenant-acme",
        user_id="user-1",
        trace_id="trace-1",
        roles=["analyst"],
    )


def _column(
    term: str,
    *,
    role: Literal["measure", "dimension", "filter"],
    result_alias: str,
    data_type: str = "VARCHAR",
    column_name: str | None = None,
    table_name: str = "STAGING.STAGING_TRANSACTIONS",
    catalog_column_id: str | None = None,
) -> ChartColumnRef:
    return ChartColumnRef(
        term=term,
        catalog_column_id=catalog_column_id or f"col-{term}",
        table_name=table_name,
        column_name=column_name or term.upper(),
        data_type=data_type,
        role=role,
        result_alias=result_alias,
    )


def _make_input(
    *,
    final_columns: list[str],
    final_rows: list[dict[str, Any]],
    final_row_count: int,
    columns: list[ChartColumnRef],
) -> ChartSelectionInput:
    return ChartSelectionInput(
        request_context=_request_context(),
        payload=ChartSelectionPayload(
            final_columns=final_columns,
            final_rows=final_rows,
            final_row_count=final_row_count,
            columns=columns,
        ),
    )


class TestBarChart:
    async def test_clean_dimension_and_measure_non_temporal_yields_bar(self) -> None:
        agent = ChartSelectionAgent()
        columns = [
            _column("market", role="dimension", result_alias="MARKETID", data_type="VARCHAR"),
            _column("units", role="measure", result_alias="UNITS_TOTAL", data_type="NUMBER"),
        ]
        output = await agent.run(
            _make_input(
                final_columns=["MARKETID", "UNITS_TOTAL"],
                final_rows=[{"MARKETID": "US", "UNITS_TOTAL": 100}],
                final_row_count=1,
                columns=columns,
            )
        )

        chart = output.result.chart
        assert chart.chart_type == "bar"
        assert chart.x_column == "MARKETID"
        assert chart.y_column == "UNITS_TOTAL"
        assert output.result.unmatched_columns == []
        assert output.confidence == 1.0


class TestLineChart:
    async def test_temporal_dimension_yields_line(self) -> None:
        agent = ChartSelectionAgent()
        columns = [
            _column("order_date", role="dimension", result_alias="ORDER_DATE", data_type="DATE"),
            _column("units", role="measure", result_alias="UNITS_TOTAL", data_type="NUMBER"),
        ]
        output = await agent.run(
            _make_input(
                final_columns=["ORDER_DATE", "UNITS_TOTAL"],
                final_rows=[
                    {"ORDER_DATE": "2026-01-01", "UNITS_TOTAL": 10},
                    {"ORDER_DATE": "2026-01-02", "UNITS_TOTAL": 20},
                ],
                final_row_count=2,
                columns=columns,
            )
        )

        chart = output.result.chart
        assert chart.chart_type == "line"
        assert chart.x_column == "ORDER_DATE"
        assert chart.y_column == "UNITS_TOTAL"
        assert output.confidence == 1.0

    async def test_temporal_check_is_case_insensitive(self) -> None:
        agent = ChartSelectionAgent()
        columns = [
            _column("order_date", role="dimension", result_alias="ORDER_DATE", data_type="date"),
            _column("units", role="measure", result_alias="UNITS_TOTAL", data_type="NUMBER"),
        ]
        output = await agent.run(
            _make_input(
                final_columns=["ORDER_DATE", "UNITS_TOTAL"],
                final_rows=[{"ORDER_DATE": "2026-01-01", "UNITS_TOTAL": 10}],
                final_row_count=1,
                columns=columns,
            )
        )

        assert output.result.chart.chart_type == "line"


class TestSingleValue:
    async def test_one_row_one_measure_no_dimension_yields_single_value(self) -> None:
        agent = ChartSelectionAgent()
        columns = [
            _column("units", role="measure", result_alias="UNITS_TOTAL", data_type="NUMBER"),
        ]
        output = await agent.run(
            _make_input(
                final_columns=["UNITS_TOTAL"],
                final_rows=[{"UNITS_TOTAL": 4200}],
                final_row_count=1,
                columns=columns,
            )
        )

        chart = output.result.chart
        assert chart.chart_type == "single_value"
        assert chart.x_column is None
        assert chart.y_column == "UNITS_TOTAL"
        assert output.confidence == 1.0


class TestUnmatchedColumns:
    async def test_alias_not_in_final_columns_is_reported_and_excluded(self) -> None:
        agent = ChartSelectionAgent()
        columns = [
            _column("market", role="dimension", result_alias="MARKETID", data_type="VARCHAR"),
            # This measure's alias was never actually produced by the real
            # result set -- e.g. the caller's alias-threading didn't match
            # SQL Generation's real aggregation alias.
            _column("units", role="measure", result_alias="UNITS_TOTAL", data_type="NUMBER"),
        ]
        output = await agent.run(
            _make_input(
                final_columns=["MARKETID"],  # UNITS_TOTAL is NOT here
                final_rows=[{"MARKETID": "US"}],
                final_row_count=1,
                columns=columns,
            )
        )

        assert output.result.unmatched_columns == ["UNITS_TOTAL"]
        chart = output.result.chart
        assert chart.y_column is None
        # No measure resolved -> honest table fallback (single_value needs
        # a resolved y_column).
        assert chart.chart_type == "table"
        assert output.confidence == 0.5

    async def test_unmatched_filter_column_is_not_reported(self) -> None:
        agent = ChartSelectionAgent()
        columns = [
            _column("market", role="dimension", result_alias="MARKETID", data_type="VARCHAR"),
            _column("units", role="measure", result_alias="UNITS_TOTAL", data_type="NUMBER"),
            _column(
                "status", role="filter", result_alias="STATUS_FILTER", data_type="VARCHAR"
            ),
        ]
        output = await agent.run(
            _make_input(
                final_columns=["MARKETID", "UNITS_TOTAL"],
                final_rows=[{"MARKETID": "US", "UNITS_TOTAL": 100}],
                final_row_count=1,
                columns=columns,
            )
        )

        # STATUS_FILTER never appears in final_columns either, but filter
        # columns are never x/y candidates and are not expected to be
        # result columns, so it is not reported as unmatched.
        assert output.result.unmatched_columns == []


class TestTableFallback:
    async def test_no_measure_column_at_all_yields_table_fallback(self) -> None:
        agent = ChartSelectionAgent()
        columns = [
            _column("market", role="dimension", result_alias="MARKETID", data_type="VARCHAR"),
        ]
        output = await agent.run(
            _make_input(
                final_columns=["MARKETID"],
                final_rows=[{"MARKETID": "US"}, {"MARKETID": "EU"}],
                final_row_count=2,
                columns=columns,
            )
        )

        chart = output.result.chart
        assert chart.chart_type == "table"
        assert chart.y_column is None
        assert chart.x_column == "MARKETID"
        assert output.confidence == 0.5


class TestOutputEnvelope:
    async def test_lineage_and_metadata(self) -> None:
        agent = ChartSelectionAgent()
        columns = [
            _column("market", role="dimension", result_alias="MARKETID", data_type="VARCHAR"),
            _column("units", role="measure", result_alias="UNITS_TOTAL", data_type="NUMBER"),
        ]
        output = await agent.run(
            _make_input(
                final_columns=["MARKETID", "UNITS_TOTAL"],
                final_rows=[{"MARKETID": "US", "UNITS_TOTAL": 100}],
                final_row_count=1,
                columns=columns,
            )
        )

        assert output.errors == []
        assert len(output.lineage_events) == 1
        assert output.lineage_events[0].agent_name == "insight.chart_selection"
        assert output.lineage_events[0].tenant_id == "tenant-acme"
        assert output.lineage_events[0].trace_id == "trace-1"
        assert output.metadata.latency_ms >= 0
        assert output.metadata.model_version is None
        assert output.metadata.tokens_input is None
