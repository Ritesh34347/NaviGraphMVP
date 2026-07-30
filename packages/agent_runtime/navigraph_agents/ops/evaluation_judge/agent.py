"""Evaluation Judge agent implementation.

Follows the exact structural pattern established by
`navigraph_agents.insight.grounded_narrative_generation.agent.GroundedNarrativeGenerationAgent`:
a real LLM call constrained to caller-supplied data, a defensive
`_parse_llm_response` staticmethod that never raises and falls back to safe
defaults on any malformed shape, a `confidence` of 1.0 when nothing went
wrong and 0.5 when any fallback occurred, and a real `LineageEvent`/
`AgentMetadata` populated from the real LLM response.

Unlike Grounded Narrative Generation, this agent has no LLM-skip
short-circuit: by the time it is invoked at all, there is always a
narrative (and the data it was grounded in) to judge. Whether it is worth
invoking at all -- e.g. skipping evaluation when the upstream pipeline
failed before producing a narrative -- is the caller's decision, not this
agent's.

`intent_match` is computed directly in Python from
`payload.actual_intent == payload.expected_intent` and is never asked of
the LLM at all -- the system prompt explicitly instructs the model to
ignore intent-matching, and the three dimensions it scores
(correctness/groundedness/narrative_quality) never include it.

## Malformed-response handling (documented choice)

`_parse_llm_response` distinguishes two failure scopes and records exactly
one `AgentError(code="judge_response_malformed")` per distinct problem:

- **Top-level malformed** (the response text is not valid JSON at all, or
  the parsed JSON is not an object): there is no way to salvage individual
  dimensions from something that isn't a JSON object in the first place, so
  ONE error is recorded and ALL THREE dimensions fall back to
  `DimensionScore(score=1, rationale="judge response could not be parsed")`.
- **Per-dimension malformed** (the top level parsed fine, but one
  dimension's key is missing, or its value isn't an object with a valid
  `int` `score` in `[1, 5]` and a `str` `rationale`): ONE error is recorded
  PER broken dimension, and only THAT dimension falls back -- the other,
  validly-shaped dimensions in the same response are trusted and returned
  as-is. This mirrors the "don't over-invalidate a partially-good response"
  spirit of `GroundedNarrativeGenerationAgent._validate_citations`, which
  drops only the one fabricated citation rather than discarding an entire
  otherwise-good narrative.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from navigraph_shared.contracts import AgentError, AgentMetadata, LineageEvent
from navigraph_shared.llm import LLMClient, LLMResponse, strip_json_code_fence
from navigraph_shared.telemetry import (
    get_tracer,
    record_agent_error,
    record_agent_invocation,
)
from opentelemetry.trace import Tracer

from navigraph_agents.ops.evaluation_judge.contracts import (
    DimensionScore,
    EvaluationJudgeInput,
    EvaluationJudgeOutput,
    EvaluationJudgePayload,
    EvaluationJudgeResult,
)

AGENT_NAME = "ops.evaluation_judge"
PROMPT_VERSION = "v1"

_PROMPT_PATH = Path(__file__).parent / "prompts" / "judge.md"

_FALLBACK_RATIONALE = "judge response could not be parsed"

_DIMENSION_KEYS = ("correctness", "groundedness", "narrative_quality")

# REAL BUG, found live against a real model: both `final_rows` and
# `anomalies` were rendered into the judge prompt fully uncapped -- for a
# real 10,000-row result this alone was large enough to make the judge's
# own response come back unparseable (`judge_response_malformed`, all
# three dimensions falling back to score=1), which is a strictly worse
# failure mode than a narrative-generation prompt bloat: it silently
# degrades the eval harness's own signal rather than the user-facing
# answer. Same treatment `insight.grounded_narrative_generation` already
# applies to its own prompt: rows capped to the first N (stated explicitly,
# never silently), anomalies capped to the top-N by `|z_score|`. This agent
# does no grounding/citation check of its own (it only asks the model to
# score an already-generated narrative), so there is no separate "full
# list" to preserve for validation, unlike narrative generation.
_MAX_ROWS_IN_PROMPT = 200
_MAX_ANOMALIES_IN_PROMPT = 20


def _load_system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _fallback_dimension_score() -> DimensionScore:
    return DimensionScore(score=1, rationale=_FALLBACK_RATIONALE)


class EvaluationJudgeAgent:
    """Scores an already-generated conversational-BI narrative for
    correctness, groundedness, and narrative quality against the real data
    it was supposed to be grounded in, and computes intent-match directly
    in Python."""

    def __init__(self, llm_client: LLMClient, tracer: Tracer | None = None) -> None:
        self._llm_client = llm_client
        self._tracer = tracer or get_tracer("navigraph-agent-runtime")
        self._system_prompt = _load_system_prompt()

    async def run(self, input: EvaluationJudgeInput) -> EvaluationJudgeOutput:
        start = time.perf_counter()
        request_context = input.request_context
        payload = input.payload

        errors: list[AgentError] = []
        llm_response: LLMResponse | None = None

        with self._tracer.start_as_current_span(
            "agent.evaluation_judge.run"
        ) as span:
            span.set_attribute("navigraph.tenant_id", request_context.tenant_id)
            span.set_attribute("navigraph.trace_id", request_context.trace_id)
            span.set_attribute("navigraph.agent_name", AGENT_NAME)
            span.set_attribute("navigraph.expected_intent", payload.expected_intent)
            span.set_attribute("navigraph.actual_intent", payload.actual_intent)

            user_message = self._build_user_message(payload)

            try:
                llm_response = await self._llm_client.complete(
                    system=self._system_prompt,
                    messages=[{"role": "user", "content": user_message}],
                    max_tokens=1024,
                )
            except Exception as exc:  # noqa: BLE001 - never let an LLM-side failure crash the agent
                errors.append(
                    AgentError(
                        code="llm_call_failed",
                        message=f"LLM call failed: {exc}",
                        recoverable=False,
                    )
                )

            correctness, groundedness, narrative_quality = self._parse_llm_response(
                llm_response, errors
            )

            intent_match = payload.actual_intent == payload.expected_intent

            # Per this agent's documented confidence rule: 1.0 only when
            # every dimension parsed cleanly out of a real LLM response; any
            # fallback (a malformed top level, a missing dimension, an
            # out-of-range/wrong-typed score, or an outright LLM call
            # failure) degrades to 0.5 -- a partial, still-useful result
            # rather than an outright failure.
            confidence = 1.0 if not errors else 0.5

            result = EvaluationJudgeResult(
                correctness=correctness,
                groundedness=groundedness,
                narrative_quality=narrative_quality,
                intent_match=intent_match,
            )

            metadata = AgentMetadata(
                latency_ms=(time.perf_counter() - start) * 1000.0,
                model_version=llm_response.model if llm_response else None,
                prompt_version=PROMPT_VERSION,
                tokens_input=llm_response.tokens_input if llm_response else None,
                tokens_output=llm_response.tokens_output if llm_response else None,
            )

            lineage_event = LineageEvent(
                agent_name=AGENT_NAME,
                input_summary=(
                    f"question={payload.question!r} expected_intent={payload.expected_intent!r} "
                    f"actual_intent={payload.actual_intent!r} "
                    f"final_row_count={len(payload.final_rows)} "
                    f"anomaly_count={len(payload.anomalies)}"
                ),
                output_summary=(
                    f"correctness={result.correctness.score} "
                    f"groundedness={result.groundedness.score} "
                    f"narrative_quality={result.narrative_quality.score} "
                    f"intent_match={result.intent_match}"
                ),
                tenant_id=request_context.tenant_id,
                trace_id=request_context.trace_id,
            )

            span.set_attribute("navigraph.correctness_score", result.correctness.score)
            span.set_attribute("navigraph.groundedness_score", result.groundedness.score)
            span.set_attribute(
                "navigraph.narrative_quality_score", result.narrative_quality.score
            )
            span.set_attribute("navigraph.intent_match", result.intent_match)

        record_agent_invocation(
            AGENT_NAME, latency_ms=metadata.latency_ms, success=not errors
        )
        for error in errors:
            record_agent_error(AGENT_NAME, error_code=error.code, recoverable=error.recoverable)

        return EvaluationJudgeOutput(
            result=result,
            confidence=confidence,
            lineage_events=[lineage_event],
            errors=errors,
            metadata=metadata,
        )

    @staticmethod
    def _build_user_message(payload: EvaluationJudgePayload) -> str:
        rows_for_prompt = payload.final_rows[:_MAX_ROWS_IN_PROMPT]
        anomalies_for_prompt = sorted(
            payload.anomalies, key=lambda a: abs(a.z_score), reverse=True
        )[:_MAX_ANOMALIES_IN_PROMPT]

        return (
            f'Question: "{payload.question}"\n\n'
            f"Narrative to evaluate: {json.dumps(payload.actual_narrative)}\n\n"
            f"Final result set (showing {len(rows_for_prompt)} of "
            f"{len(payload.final_rows)} rows):\n"
            f"columns: {json.dumps(payload.final_columns)}\n"
            f"rows: {json.dumps(rows_for_prompt, default=str)}\n\n"
            f"Chart: {json.dumps(payload.chart.model_dump())}\n\n"
            f"Anomalies (showing top {len(anomalies_for_prompt)} of "
            f"{len(payload.anomalies)} by |z_score|): "
            f"{json.dumps([a.model_dump() for a in anomalies_for_prompt])}"
        )

    @staticmethod
    def _parse_llm_response(
        llm_response: LLMResponse | None,
        errors: list[AgentError],
    ) -> tuple[DimensionScore, DimensionScore, DimensionScore]:
        """Parse the LLM's JSON response into the three `DimensionScore`s.

        Never raises. See this module's docstring for the exact two-scope
        malformed-response policy: a top-level failure (not JSON, or not a
        JSON object) records ONE error and falls back ALL THREE dimensions;
        a top-level success with one or more individually-broken dimensions
        records ONE error PER broken dimension and falls back only those,
        trusting the other, validly-shaped dimensions in the same response.
        """

        fallback_all = (
            _fallback_dimension_score(),
            _fallback_dimension_score(),
            _fallback_dimension_score(),
        )

        if llm_response is None:
            # The LLM call itself already failed; llm_call_failed error was
            # already recorded by the caller -- no additional error here.
            return fallback_all

        try:
            data = json.loads(strip_json_code_fence(llm_response.text))
        except json.JSONDecodeError as exc:
            errors.append(
                AgentError(
                    code="judge_response_malformed",
                    message=f"LLM response was not valid JSON: {exc}",
                    recoverable=True,
                )
            )
            return fallback_all

        if not isinstance(data, dict):
            errors.append(
                AgentError(
                    code="judge_response_malformed",
                    message="LLM response JSON was not an object",
                    recoverable=True,
                )
            )
            return fallback_all

        scores = {
            key: EvaluationJudgeAgent._parse_dimension(data, key, errors)
            for key in _DIMENSION_KEYS
        }

        return scores["correctness"], scores["groundedness"], scores["narrative_quality"]

    @staticmethod
    def _parse_dimension(
        data: dict[str, object],
        key: str,
        errors: list[AgentError],
    ) -> DimensionScore:
        """Parse a single dimension's entry out of an already-confirmed
        top-level JSON object. Any shape problem specific to this one
        dimension -- a missing key, a non-object value, a missing/
        out-of-range/wrong-typed `score`, or a non-string `rationale` --
        records ONE `AgentError(code="judge_response_malformed")` scoped to
        this dimension and falls back to `DimensionScore(score=1, ...)` for
        THIS dimension only, leaving any other, validly-shaped dimensions in
        `data` untouched.
        """

        entry = data.get(key)
        if not isinstance(entry, dict):
            errors.append(
                AgentError(
                    code="judge_response_malformed",
                    message=f"LLM response missing or invalid {key!r} entry: {entry!r}",
                    recoverable=True,
                )
            )
            return _fallback_dimension_score()

        score = entry.get("score")
        rationale = entry.get("rationale")

        if (
            not isinstance(score, int)
            or isinstance(score, bool)
            or not (1 <= score <= 5)
            or not isinstance(rationale, str)
        ):
            errors.append(
                AgentError(
                    code="judge_response_malformed",
                    message=(
                        f"LLM response {key!r} entry had an invalid score/rationale: "
                        f"{entry!r}"
                    ),
                    recoverable=True,
                )
            )
            return _fallback_dimension_score()

        return DimensionScore(score=score, rationale=rationale)
