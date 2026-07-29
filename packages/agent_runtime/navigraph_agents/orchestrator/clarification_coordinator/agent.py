"""Clarification Coordinator agent implementation.

Follows the exact structural pattern established by
`navigraph_agents.insight.follow_up_suggestion.agent.FollowUpSuggestionAgent`:
loads a `prompts/<name>.md` system prompt, builds a user message from the
payload, calls `llm_client.complete(...)`, parses the JSON response
defensively via `json.loads(strip_json_code_fence(llm_response.text))` (the
real Phase 8 bug fix -- see
`navigraph_shared.llm.json_parsing.strip_json_code_fence`'s module
docstring), and falls back to a fixed constant on any malformed response
rather than raising.

UNLIKE FOLLOW-UP SUGGESTION, THERE IS NO SKIP CONDITION HERE. Follow-Up
Suggestion short-circuits (no LLM call at all) when there is no data to
build a follow-up from. This agent has no equivalent empty-input case: it
is only ever invoked by the orchestration layer when upstream schema
resolution has already come back completely empty, so there is always
genuinely something to ask the user about. An LLM call is made on every
`run()`.

VALIDATION CHOICE -- BOTH FIELDS MUST BE PRESENT AND WELL-TYPED: the LLM's
response is only accepted if it is a JSON object containing BOTH
`needs_clarification` (a bool) AND `clarifying_question` (a string or
`null`) as actual keys. A response missing `clarifying_question` entirely --
even if `needs_clarification` is present and valid -- is treated exactly
the same as a fully malformed response: falls back to the fixed constant
plus a recoverable `clarification_llm_response_malformed` error. This is a
deliberate, stricter choice than Follow-Up Suggestion's shape validation
(which tolerates a missing/wrong-typed `rationale` by substituting `None`
for that one optional field). The difference: Follow-Up Suggestion's
`rationale` is a genuinely optional annotation on an otherwise-valid
suggestion, so silently defaulting it is harmless. Here, `needs_clarification`
and `clarifying_question` are two halves of ONE joint decision -- a
`clarifying_question` string with no accompanying `needs_clarification`
(or vice versa) is not a "partially valid, partially defaulted" response;
it is evidence the model didn't actually follow the requested output shape
at all, so trusting either field in isolation would be riskier than falling
back to the safe, fixed question. This is deliberately narrow: it does NOT
apply to `clarifying_question`'s VALUE being `null` (that is a valid,
well-typed value under the escape hatch this agent's own prompt describes),
only to the KEY being absent.

Unlike Semantic Retrieval/Grounded Narrative Generation's closed-candidate
grounding discipline, this agent's `clarifying_question` is not validated
against any candidate list -- it is generated prose, not a claim from a
result set.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from navigraph_shared.contracts import AgentError, AgentMetadata, LineageEvent
from navigraph_shared.llm import LLMClient, LLMResponse, strip_json_code_fence
from navigraph_shared.telemetry import (
    get_tracer,
    record_agent_error,
    record_agent_invocation,
)
from opentelemetry.trace import Tracer

from navigraph_agents.orchestrator.clarification_coordinator.contracts import (
    ClarificationCoordinatorInput,
    ClarificationCoordinatorOutput,
    ClarificationCoordinatorPayload,
    ClarificationCoordinatorResult,
)

AGENT_NAME = "orchestrator.clarification_coordinator"
PROMPT_VERSION = "v1"

_PROMPT_PATH = Path(__file__).parent / "prompts" / "clarification_coordinator.md"

_FIXED_FALLBACK_QUESTION = (
    "I couldn't find data matching your question -- could you rephrase it, "
    "or tell me which table or metric you mean?"
)

_FALLBACK_RESULT = ClarificationCoordinatorResult(
    needs_clarification=True,
    clarifying_question=_FIXED_FALLBACK_QUESTION,
)


def _load_system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _build_user_message(payload: ClarificationCoordinatorPayload) -> str:
    return (
        f'Original question: "{payload.original_question}"\n\n'
        f"Failed stage: {payload.failed_stage}\n\n"
        f"Failure reason: {payload.failure_reason}\n\n"
        f"Unmapped terms: {json.dumps(payload.unmapped_terms)}"
    )


class ClarificationCoordinatorAgent:
    """Generates one clarifying question to ask the user back, given a
    genuine upstream schema-resolution failure."""

    def __init__(self, llm_client: LLMClient, tracer: Tracer | None = None) -> None:
        self._llm_client = llm_client
        self._tracer = tracer or get_tracer("navigraph-agent-runtime")
        self._system_prompt = _load_system_prompt()

    async def run(self, input: ClarificationCoordinatorInput) -> ClarificationCoordinatorOutput:
        start = time.perf_counter()
        request_context = input.request_context
        payload = input.payload

        errors: list[AgentError] = []
        llm_response: LLMResponse | None = None

        with self._tracer.start_as_current_span("agent.clarification_coordinator.run") as span:
            span.set_attribute("navigraph.tenant_id", request_context.tenant_id)
            span.set_attribute("navigraph.trace_id", request_context.trace_id)
            span.set_attribute("navigraph.agent_name", AGENT_NAME)
            span.set_attribute("navigraph.failed_stage", payload.failed_stage)
            span.set_attribute("navigraph.unmapped_term_count", len(payload.unmapped_terms))

            user_message = _build_user_message(payload)

            try:
                llm_response = await self._llm_client.complete(
                    system=self._system_prompt,
                    messages=[{"role": "user", "content": user_message}],
                    max_tokens=512,
                )
            except Exception as exc:  # noqa: BLE001 - never let an LLM-side failure crash the agent
                errors.append(
                    AgentError(
                        code="llm_call_failed",
                        message=f"LLM call failed: {exc}",
                        recoverable=False,
                    )
                )

            result = self._parse_llm_response(llm_response, errors)

            confidence = 1.0 if not errors else 0.5

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
                    f"question={payload.original_question!r} "
                    f"failed_stage={payload.failed_stage!r} "
                    f"unmapped_terms={payload.unmapped_terms}"
                ),
                output_summary=(
                    f"needs_clarification={result.needs_clarification} "
                    f"errors={len(errors)}"
                ),
                tenant_id=request_context.tenant_id,
                trace_id=request_context.trace_id,
            )

            span.set_attribute("navigraph.needs_clarification", result.needs_clarification)

        record_agent_invocation(AGENT_NAME, latency_ms=metadata.latency_ms, success=not errors)
        for error in errors:
            record_agent_error(AGENT_NAME, error_code=error.code, recoverable=error.recoverable)

        return ClarificationCoordinatorOutput(
            result=result,
            confidence=confidence,
            lineage_events=[lineage_event],
            errors=errors,
            metadata=metadata,
        )

    @staticmethod
    def _parse_llm_response(
        llm_response: LLMResponse | None,
        errors: list[AgentError],
    ) -> ClarificationCoordinatorResult:
        """Parse the LLM's JSON response, requiring BOTH `needs_clarification`
        (bool) and `clarifying_question` (str | None) to be present and
        well-typed -- see this module's docstring for why a response with
        only one of the two valid is treated as fully malformed, not
        partially trusted. Any failure here records a single recoverable
        `AgentError(code="clarification_llm_response_malformed")` and falls
        back to `_FALLBACK_RESULT`, rather than raising.
        """

        if llm_response is None:
            # The LLM call itself already failed; llm_call_failed was
            # already recorded by the caller.
            return _FALLBACK_RESULT

        try:
            data: Any = json.loads(strip_json_code_fence(llm_response.text))
        except json.JSONDecodeError as exc:
            errors.append(
                AgentError(
                    code="clarification_llm_response_malformed",
                    message=f"LLM response was not valid JSON: {exc}",
                    recoverable=True,
                )
            )
            return _FALLBACK_RESULT

        if not isinstance(data, dict):
            errors.append(
                AgentError(
                    code="clarification_llm_response_malformed",
                    message="LLM response JSON was not an object",
                    recoverable=True,
                )
            )
            return _FALLBACK_RESULT

        if "needs_clarification" not in data or "clarifying_question" not in data:
            errors.append(
                AgentError(
                    code="clarification_llm_response_malformed",
                    message=(
                        "LLM response was missing 'needs_clarification' and/or "
                        "'clarifying_question'"
                    ),
                    recoverable=True,
                )
            )
            return _FALLBACK_RESULT

        needs_clarification = data["needs_clarification"]
        clarifying_question = data["clarifying_question"]

        if not isinstance(needs_clarification, bool):
            errors.append(
                AgentError(
                    code="clarification_llm_response_malformed",
                    message=(
                        f"'needs_clarification' was not a bool: {needs_clarification!r}"
                    ),
                    recoverable=True,
                )
            )
            return _FALLBACK_RESULT

        if clarifying_question is not None and not isinstance(clarifying_question, str):
            errors.append(
                AgentError(
                    code="clarification_llm_response_malformed",
                    message=(
                        f"'clarifying_question' was not a string or null: {clarifying_question!r}"
                    ),
                    recoverable=True,
                )
            )
            return _FALLBACK_RESULT

        return ClarificationCoordinatorResult(
            needs_clarification=needs_clarification,
            clarifying_question=clarifying_question,
        )
