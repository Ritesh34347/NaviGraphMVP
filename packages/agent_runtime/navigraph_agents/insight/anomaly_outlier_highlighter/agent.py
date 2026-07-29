"""Anomaly/Outlier Highlighter agent implementation.

Fully deterministic: no LLM call, no `prompts/` directory, no external
client dependency at all, and no third-party numeric dependency either
(`packages/agent_runtime/pyproject.toml` declares no numpy/scipy/pandas) --
this agent is a pure function of its input, using only the stdlib
`statistics` module, exactly like `navigraph_agents.insight.chart_selection`
and `navigraph_agents.guardrail.query_cost_estimator`.

Given the real, already-executed result set and the `ChartSpec` Chart
Selection assigned to it, this agent runs a simple population z-score
outlier check over the resolved measure column (`payload.chart.y_column`):
every numeric value more than `_Z_SCORE_THRESHOLD` population-standard-
deviations from the group's mean is reported as an `AnomalyFinding`.

There are several real, honestly-flagged "can't run detection at all"
cases -- no measure column resolved, too few numeric values to make a
z-score meaningful, and zero variance across all values -- each surfaced
via `AnomalyDetectionResult.skipped_reason` rather than an `AgentError`:
none of these are a malfunction, just data that this method cannot (or
should not) be applied to.

Follows the same structural pattern as
`navigraph_agents.insight.chart_selection.agent`: open an OTel span, never
raise, always emit a `LineageEvent` and `AgentMetadata` with `latency_ms`
populated.
"""

from __future__ import annotations

import statistics
import time

from navigraph_shared.contracts import AgentMetadata, LineageEvent
from navigraph_shared.telemetry import get_tracer, record_agent_invocation
from opentelemetry.trace import Tracer

from navigraph_agents.insight.anomaly_outlier_highlighter.contracts import (
    AnomalyDetectionInput,
    AnomalyDetectionOutput,
    AnomalyDetectionPayload,
    AnomalyDetectionResult,
    AnomalyFinding,
)

AGENT_NAME = "insight.anomaly_outlier_highlighter"

# Real placeholder pending business-requirement confirmation -- same
# category as guardrail.query_cost_estimator.ROLE_ROW_LIMITS: a real,
# usable default, not a stand-in for logic that hasn't been written yet.
_Z_SCORE_THRESHOLD = 2.0

# Fewer numeric points than this makes a z-score statistically
# meaningless (a "mean" and "stdev" over one or two points don't say
# anything useful about outliers), so detection is skipped outright
# rather than producing a technically-computable but meaningless result.
_MIN_GROUPS_FOR_DETECTION = 3


def _numeric_values(rows: list[dict], y_column: str) -> list[tuple[int, float]]:
    """Every `(row_index, value)` pair from `rows` whose `y_column` cell
    converts cleanly to `float`. Non-numeric or missing cells are skipped
    defensively -- this agent never crashes on a non-numeric cell, it just
    excludes that row from the numeric population being analyzed."""

    values: list[tuple[int, float]] = []
    for row_index, row in enumerate(rows):
        cell = row.get(y_column)
        try:
            value = float(cell)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        values.append((row_index, value))
    return values


def _group_value(row: dict, x_column: str | None, row_index: int) -> str:
    if x_column is None:
        return f"row_{row_index}"
    return str(row.get(x_column))


class AnomalyOutlierHighlighterAgent:
    """Flags result rows whose resolved measure value is a population
    z-score outlier. Pure function of its input -- no external client
    dependency."""

    def __init__(self, tracer: Tracer | None = None) -> None:
        self._tracer = tracer or get_tracer("navigraph-agent-runtime")

    async def run(self, input: AnomalyDetectionInput) -> AnomalyDetectionOutput:
        start = time.perf_counter()
        request_context = input.request_context
        payload = input.payload

        with self._tracer.start_as_current_span(
            "agent.anomaly_outlier_highlighter.run"
        ) as span:
            span.set_attribute("navigraph.tenant_id", request_context.tenant_id)
            span.set_attribute("navigraph.trace_id", request_context.trace_id)
            span.set_attribute("navigraph.agent_name", AGENT_NAME)

            result = self._detect(payload)

            # A `skipped_reason` is a real, honest degraded case -- not a
            # malfunction, just data this method cannot (or should not) be
            # applied to -- so it earns a lower confidence rather than an
            # AgentError. Detection actually running is a confident, valid
            # result regardless of whether it found any anomalies at all:
            # zero anomalies is itself a real, confident answer.
            confidence = 0.5 if result.skipped_reason is not None else 1.0

            lineage_event = LineageEvent(
                agent_name=AGENT_NAME,
                input_summary=(
                    f"final_row_count={payload.final_row_count} "
                    f"y_column={payload.chart.y_column}"
                ),
                output_summary=(
                    f"anomalies={len(result.anomalies)} "
                    f"skipped_reason={result.skipped_reason}"
                ),
                tenant_id=request_context.tenant_id,
                trace_id=request_context.trace_id,
            )

            latency_ms = (time.perf_counter() - start) * 1000.0
            metadata = AgentMetadata(latency_ms=latency_ms)

            span.set_attribute("navigraph.anomalies_count", len(result.anomalies))
            span.set_attribute("navigraph.skipped", result.skipped_reason is not None)

        record_agent_invocation(AGENT_NAME, latency_ms=latency_ms, success=True)

        return AnomalyDetectionOutput(
            result=result,
            confidence=confidence,
            lineage_events=[lineage_event],
            errors=[],
            metadata=metadata,
        )

    @staticmethod
    def _detect(payload: AnomalyDetectionPayload) -> AnomalyDetectionResult:
        y_column = payload.chart.y_column
        if y_column is None:
            return AnomalyDetectionResult(
                anomalies=[],
                skipped_reason="chart selection identified no measure column to analyze",
            )

        numeric_values = _numeric_values(payload.final_rows, y_column)
        if len(numeric_values) < _MIN_GROUPS_FOR_DETECTION:
            return AnomalyDetectionResult(
                anomalies=[],
                skipped_reason=(
                    f"fewer than {_MIN_GROUPS_FOR_DETECTION} numeric {y_column!r} "
                    f"values ({len(numeric_values)} found)"
                ),
            )

        values = [value for _, value in numeric_values]
        mean = statistics.mean(values)
        # Population standard deviation, deliberately -- these
        # grouped-by-dimension result sets are small and are treated as
        # the full population being compared (every group this query
        # returned), not a sample drawn from some larger population, so
        # `pstdev` (divides by N) is the correct choice over `stdev`
        # (divides by N-1).
        stdev = statistics.pstdev(values)

        if stdev == 0:
            return AnomalyDetectionResult(
                anomalies=[],
                skipped_reason="zero variance across all groups -- no outliers possible",
            )

        anomalies: list[AnomalyFinding] = []
        for row_index, value in numeric_values:
            z_score = (value - mean) / stdev
            if abs(z_score) > _Z_SCORE_THRESHOLD:
                anomalies.append(
                    AnomalyFinding(
                        row_index=row_index,
                        group_value=_group_value(
                            payload.final_rows[row_index], payload.chart.x_column, row_index
                        ),
                        measure_value=value,
                        z_score=z_score,
                        mean=mean,
                        stdev=stdev,
                    )
                )

        return AnomalyDetectionResult(
            threshold=_Z_SCORE_THRESHOLD,
            anomalies=anomalies,
            skipped_reason=None,
        )
