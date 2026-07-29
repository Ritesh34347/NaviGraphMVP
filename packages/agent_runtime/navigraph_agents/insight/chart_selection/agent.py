"""Chart Selection agent implementation.

Fully deterministic: no LLM call, no `prompts/` directory, no external
client dependency at all -- this agent is a pure function of its input,
exactly like `navigraph_agents.query.sql_optimization` and
`navigraph_agents.understanding.schema_mapping`. Given the real, already
-executed result set (`final_columns`/`final_rows`/`final_row_count`, as
Data Federation produces them) and the resolved columns Schema Mapping
assigned a query role to (with `result_alias` threaded in by the caller --
see `ChartColumnRef`'s docstring), this agent:

1. Splits `payload.columns` into dimension and measure candidates
   (`role="filter"` columns are never x/y candidates and are not reported
   as unmatched either -- a filter column isn't expected to appear as a
   result column at all).
2. Checks each dimension/measure column's `result_alias` against the real
   `final_columns` header list. A column whose alias doesn't actually
   appear there is dropped from x/y consideration and reported, by its
   alias, in `unmatched_columns` -- a real, honest signal that the
   caller's alias-threading didn't line up with the real result set,
   never silently swallowed.
3. Resolves `x_column` to the first (by original list order) matched
   dimension's alias, and `y_column` to the first matched measure's
   alias -- `None` for either if nothing matched.
4. Picks a `chart_type` via `_select_chart` below.

Follows the same structural pattern as
`navigraph_agents.query.sql_optimization.agent`: open an OTel span, never
raise, always emit a `LineageEvent` and `AgentMetadata` with `latency_ms`
populated.
"""

from __future__ import annotations

import time

from navigraph_shared.contracts import AgentMetadata, LineageEvent
from navigraph_shared.telemetry import get_tracer, record_agent_invocation
from opentelemetry.trace import Tracer

from navigraph_agents.insight.chart_selection.contracts import (
    ChartColumnRef,
    ChartSelectionInput,
    ChartSelectionOutput,
    ChartSelectionPayload,
    ChartSelectionResult,
    ChartSpec,
)

AGENT_NAME = "insight.chart_selection"

# Real, small, explicit set of catalog `data_type` spellings this agent
# treats as temporal for chart-type selection -- mirrors the
# "deliberately narrow, real constant, not an attempt at exhaustive
# dialect coverage" convention documented on
# `sql_optimization.agent.LARGE_TABLE_ROW_THRESHOLD`. A resolved x_column
# dimension whose (uppercased) data_type is in this set drives a "line"
# chart rather than a "bar" chart.
_TEMPORAL_DATA_TYPES = frozenset({"DATE", "TIMESTAMP", "TIMESTAMP_NTZ", "TIMESTAMP_TZ", "DATETIME"})


def _resolve_candidates(
    columns: list[ChartColumnRef], final_columns: list[str]
) -> tuple[list[ChartColumnRef], list[ChartColumnRef], list[str]]:
    """Split `columns` into resolved dimension candidates and resolved
    measure candidates (both in original list order, `role="filter"`
    columns excluded from both), plus the `result_alias` of every
    dimension/measure column that does NOT appear in `final_columns` --
    a real, honest `unmatched_columns` signal, not silently dropped.

    "Resolved" means `result_alias` actually appears in `final_columns`;
    an unresolved column is excluded from consideration as an x/y
    candidate entirely.
    """

    dimensions: list[ChartColumnRef] = []
    measures: list[ChartColumnRef] = []
    unmatched: list[str] = []

    for column in columns:
        if column.role == "filter":
            continue
        if column.result_alias not in final_columns:
            unmatched.append(column.result_alias)
            continue
        if column.role == "dimension":
            dimensions.append(column)
        else:
            measures.append(column)

    return dimensions, measures, unmatched


def _select_chart(
    *,
    x_dimension: ChartColumnRef | None,
    y_measure: ChartColumnRef | None,
    final_row_count: int,
) -> ChartSpec:
    """Pick a `ChartSpec`, in this priority order:

    (a) `"single_value"` -- exactly one result row, a measure resolved,
        and no dimension resolved: a single aggregate with no grouping
        dimension.
    (b) `"line"` -- both a dimension and a measure resolved, and the
        dimension's `data_type` (uppercased) is temporal
        (`_TEMPORAL_DATA_TYPES`).
    (c) `"bar"` -- both a dimension and a measure resolved, dimension not
        temporal.
    (d) `"table"` -- the honest fallback whenever no clean x/y pair
        resolves at all (no measure resolved at all, no dimension
        resolved at all, or the resolved measure/dimension aliases never
        actually matched a real result column).

    NOTE (judgment call): the spec that commissioned this agent describes
    (b) as firing whenever "an x_column was resolved AND that dimension's
    data_type ... is temporal", without restating that a y_column must
    also be resolved -- but it also describes (d)'s trigger as "no
    measure column" resolving, i.e. a temporal-dimension-with-no-measure
    case is meant to fall through to table, not line. Requiring both
    `x_dimension` and `y_measure` for the line/bar branches (mirroring
    (c)'s explicit "both x_column and y_column resolved" wording) is the
    reading that keeps this agent from ever emitting a "line"/"bar" chart
    with a missing axis, and keeps (d)'s own stated rationale accurate.
    """

    x_column = x_dimension.result_alias if x_dimension is not None else None
    y_column = y_measure.result_alias if y_measure is not None else None

    if final_row_count == 1 and y_column is not None and x_column is None:
        return ChartSpec(
            chart_type="single_value",
            x_column=None,
            y_column=y_column,
            rationale=(
                f"single row with one resolved measure ({y_column!r}) and no "
                "dimension -- rendering as a single value"
            ),
        )

    if x_column is not None and y_column is not None:
        data_type = x_dimension.data_type  # type: ignore[union-attr]
        if data_type.upper() in _TEMPORAL_DATA_TYPES:
            return ChartSpec(
                chart_type="line",
                x_column=x_column,
                y_column=y_column,
                rationale=(
                    f"dimension {x_column!r} (data_type={data_type!r}) is "
                    "temporal -- rendering as a line chart"
                ),
            )
        return ChartSpec(
            chart_type="bar",
            x_column=x_column,
            y_column=y_column,
            rationale=(
                f"dimension {x_column!r} and measure {y_column!r} both resolved "
                "and the dimension is not temporal -- rendering as a bar chart"
            ),
        )

    if y_column is None:
        reason = "no measure column resolved to a real result column"
    elif x_column is None:
        reason = "no dimension column resolved to a real result column"
    else:  # pragma: no cover - unreachable given the branches above
        reason = "no clean x/y pair resolved"
    return ChartSpec(
        chart_type="table",
        x_column=x_column,
        y_column=y_column,
        rationale=f"{reason} -- falling back to a table",
    )


class ChartSelectionAgent:
    """Picks a chart type and x/y column pair from a real result set and
    its resolved columns. Pure function of its input -- no external client
    dependency."""

    def __init__(self, tracer: Tracer | None = None) -> None:
        self._tracer = tracer or get_tracer("navigraph-agent-runtime")

    async def run(self, input: ChartSelectionInput) -> ChartSelectionOutput:
        start = time.perf_counter()
        request_context = input.request_context
        payload = input.payload

        with self._tracer.start_as_current_span("agent.chart_selection.run") as span:
            span.set_attribute("navigraph.tenant_id", request_context.tenant_id)
            span.set_attribute("navigraph.trace_id", request_context.trace_id)
            span.set_attribute("navigraph.agent_name", AGENT_NAME)

            chart, unmatched_columns = self._select(payload)
            result = ChartSelectionResult(chart=chart, unmatched_columns=unmatched_columns)

            # A "table" fallback is a real, honest degraded case -- not an
            # AgentError, since "table" is still a valid, useful chart
            # choice -- but it earns a lower confidence than a clean
            # resolution.
            confidence = 1.0 if chart.chart_type != "table" else 0.5

            lineage_event = LineageEvent(
                agent_name=AGENT_NAME,
                input_summary=(
                    f"columns={len(payload.columns)} final_row_count={payload.final_row_count}"
                ),
                output_summary=(
                    f"chart_type={chart.chart_type} x_column={chart.x_column} "
                    f"y_column={chart.y_column} unmatched={len(unmatched_columns)}"
                ),
                tenant_id=request_context.tenant_id,
                trace_id=request_context.trace_id,
            )

            latency_ms = (time.perf_counter() - start) * 1000.0
            metadata = AgentMetadata(latency_ms=latency_ms)

            span.set_attribute("navigraph.chart_type", chart.chart_type)
            span.set_attribute("navigraph.unmatched_columns_count", len(unmatched_columns))

        record_agent_invocation(AGENT_NAME, latency_ms=latency_ms, success=True)

        return ChartSelectionOutput(
            result=result,
            confidence=confidence,
            lineage_events=[lineage_event],
            errors=[],
            metadata=metadata,
        )

    @staticmethod
    def _select(payload: ChartSelectionPayload) -> tuple[ChartSpec, list[str]]:
        dimensions, measures, unmatched = _resolve_candidates(
            payload.columns, payload.final_columns
        )
        x_dimension = dimensions[0] if dimensions else None
        y_measure = measures[0] if measures else None

        chart = _select_chart(
            x_dimension=x_dimension,
            y_measure=y_measure,
            final_row_count=payload.final_row_count,
        )
        return chart, unmatched
