"""Follow-Up Suggestion agent implementation.

Follows the exact structural pattern established by
`navigraph_agents.understanding.semantic_retrieval.agent.SemanticRetrievalAgent`
and, more closely,
`navigraph_agents.insight.grounded_narrative_generation.agent.GroundedNarrativeGenerationAgent`:
an LLM call is made ONLY when it is actually needed (short-circuiting
entirely when there is no data to build a follow-up from, exactly like
Semantic Retrieval's empty-`unresolved_terms` short-circuit and Grounded
Narrative Generation's zero-row short-circuit), malformed LLM output is
handled gracefully instead of crashing, and a lineage event/latency/token
metadata are recorded the same way.

WHERE THIS AGENT DELIBERATELY DIVERGES FROM ITS SIBLINGS: Semantic
Retrieval and Grounded Narrative Generation both enforce a closed-candidate
grounding discipline -- an LLM-returned value that isn't in the
caller-supplied candidate list (or, for narrative generation, the real
result data) is a hallucination and is never trusted. This agent does NOT
apply that discipline to its suggestions, and that is intentional, not an
oversight: a follow-up question is a *proposal* for what to look at next,
not a factual claim about data already seen. Suggesting "Did any single
account drive this spike?" when "account" never appeared in
`final_columns` is not a hallucination here -- it is exactly the kind of
useful, exploratory suggestion this agent exists to produce. Applying
Semantic Retrieval's rejection discipline to this agent would reject
precisely the suggestions that make it valuable. Validation here is
SHAPE-ONLY: is `question` a non-empty string, and are there at most 3 of
them. See `_validate_suggestions` for the exact (deliberately narrow)
checks.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from navigraph_shared.contracts import AgentError, AgentMetadata, LineageEvent
from navigraph_shared.llm import LLMClient, LLMResponse
from navigraph_shared.telemetry import (
    get_tracer,
    record_agent_error,
    record_agent_invocation,
)
from opentelemetry.trace import Tracer

from navigraph_agents.insight.follow_up_suggestion.contracts import (
    FollowUpQuestion,
    FollowUpSuggestionInput,
    FollowUpSuggestionOutput,
    FollowUpSuggestionPayload,
    FollowUpSuggestionResult,
)

AGENT_NAME = "insight.follow_up_suggestion"
PROMPT_VERSION = "v1"

_PROMPT_PATH = Path(__file__).parent / "prompts" / "follow_up_suggestion.md"

# Hard cap on the number of suggestions returned to callers. If the LLM
# returns more, the extras are silently truncated (kept in the LLM's own
# ranked order) -- not an error, since "the model was more generous than
# asked" is not a fabrication or a malformed response, just excess to trim.
_MAX_SUGGESTIONS = 3

_FIXED_EMPTY_RESULT_SUGGESTION = FollowUpQuestion(
    question="Would you like to try a broader or different question?",
    rationale=None,
)


def _load_system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _build_user_message(payload: FollowUpSuggestionPayload) -> str:
    return (
        f'Original question: "{payload.original_question}"\n\n'
        f"Narrative already given to the user:\n{payload.narrative}\n\n"
        f"Result shape: {payload.final_row_count} row(s), "
        f"columns={json.dumps(payload.final_columns)}\n\n"
        f"Chart: {json.dumps(payload.chart.model_dump())}\n\n"
        f"Anomalies: {json.dumps([a.model_dump() for a in payload.anomalies])}"
    )


class FollowUpSuggestionAgent:
    """Suggests 1-3 exploratory follow-up questions a business user would
    naturally want to ask next."""

    def __init__(self, llm_client: LLMClient, tracer: Tracer | None = None) -> None:
        self._llm_client = llm_client
        self._tracer = tracer or get_tracer("navigraph-agent-runtime")
        self._system_prompt = _load_system_prompt()

    async def run(self, input: FollowUpSuggestionInput) -> FollowUpSuggestionOutput:
        start = time.perf_counter()
        request_context = input.request_context
        payload = input.payload

        errors: list[AgentError] = []
        llm_response: LLMResponse | None = None

        with self._tracer.start_as_current_span("agent.follow_up_suggestion.run") as span:
            span.set_attribute("navigraph.tenant_id", request_context.tenant_id)
            span.set_attribute("navigraph.trace_id", request_context.trace_id)
            span.set_attribute("navigraph.agent_name", AGENT_NAME)
            span.set_attribute("navigraph.final_row_count", payload.final_row_count)
            span.set_attribute("navigraph.anomaly_count", len(payload.anomalies))

            if payload.final_row_count == 0:
                # Nothing to build a follow-up from -- skip the LLM call
                # entirely, exactly like SemanticRetrievalAgent's
                # empty-`unresolved_terms` short-circuit.
                suggestions = [_FIXED_EMPTY_RESULT_SUGGESTION]
                confidence = 1.0

                metadata = AgentMetadata(
                    latency_ms=(time.perf_counter() - start) * 1000.0,
                    model_version=None,
                    prompt_version=None,
                    tokens_input=None,
                    tokens_output=None,
                )
            else:
                user_message = _build_user_message(payload)

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

                raw_suggestions = self._parse_llm_response(llm_response, errors)
                suggestions = self._validate_suggestions(raw_suggestions, errors)

                confidence = 1.0 if suggestions else 0.0

                metadata = AgentMetadata(
                    latency_ms=(time.perf_counter() - start) * 1000.0,
                    model_version=llm_response.model if llm_response else None,
                    prompt_version=PROMPT_VERSION,
                    tokens_input=llm_response.tokens_input if llm_response else None,
                    tokens_output=llm_response.tokens_output if llm_response else None,
                )

            result = FollowUpSuggestionResult(suggestions=suggestions)

            lineage_event = LineageEvent(
                agent_name=AGENT_NAME,
                input_summary=(
                    f"question={payload.original_question!r} "
                    f"final_row_count={payload.final_row_count} "
                    f"anomaly_count={len(payload.anomalies)}"
                ),
                output_summary=f"suggestions={len(result.suggestions)}",
                tenant_id=request_context.tenant_id,
                trace_id=request_context.trace_id,
            )

            span.set_attribute("navigraph.suggestion_count", len(result.suggestions))

        record_agent_invocation(
            AGENT_NAME, latency_ms=metadata.latency_ms, success=not errors
        )
        for error in errors:
            record_agent_error(AGENT_NAME, error_code=error.code, recoverable=error.recoverable)

        return FollowUpSuggestionOutput(
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
    ) -> list[Any]:
        """Parse the LLM's JSON response into a raw list of suggestion
        entries (not yet shape-validated -- see `_validate_suggestions`).

        Handles every way the response can be malformed -- not valid JSON, a
        non-object top level, a non-list `suggestions` -- by recording a
        single recoverable `AgentError(code="follow_up_llm_response_malformed")`
        and falling back to an empty list, rather than raising, exactly like
        `SemanticRetrievalAgent._parse_llm_response`.
        """

        if llm_response is None:
            # The LLM call itself already failed; llm_call_failed error was
            # already recorded by the caller.
            return []

        try:
            data = json.loads(llm_response.text)
        except json.JSONDecodeError as exc:
            errors.append(
                AgentError(
                    code="follow_up_llm_response_malformed",
                    message=f"LLM response was not valid JSON: {exc}",
                    recoverable=True,
                )
            )
            return []

        if not isinstance(data, dict):
            errors.append(
                AgentError(
                    code="follow_up_llm_response_malformed",
                    message="LLM response JSON was not an object",
                    recoverable=True,
                )
            )
            return []

        raw_suggestions = data.get("suggestions")
        if not isinstance(raw_suggestions, list):
            errors.append(
                AgentError(
                    code="follow_up_llm_response_malformed",
                    message=f"LLM returned a non-list 'suggestions': {raw_suggestions!r}",
                    recoverable=True,
                )
            )
            return []

        return raw_suggestions

    @staticmethod
    def _validate_suggestions(
        raw_suggestions: list[Any],
        errors: list[AgentError],
    ) -> list[FollowUpQuestion]:
        """Deliberately SHAPE-ONLY validation -- see this module's
        docstring for why a closed-candidate grounding check (as done by
        Semantic Retrieval and Grounded Narrative Generation) is
        intentionally NOT applied here. A suggestion is dropped only if its
        `question` is missing, not a string, or empty/whitespace-only (an
        entry that isn't even a usable question at all, not one that
        introduces an ungrounded concept -- that's expected and welcome).
        Survivors are capped at `_MAX_SUGGESTIONS`, keeping the LLM's own
        order; going over the cap is a silent truncation, not an error. If
        zero valid suggestions remain after filtering, a single recoverable
        `AgentError(code="no_valid_suggestions_returned")` is recorded.
        """

        valid: list[FollowUpQuestion] = []

        for entry in raw_suggestions:
            if not isinstance(entry, dict):
                continue

            question = entry.get("question")
            if not isinstance(question, str) or not question.strip():
                continue

            rationale = entry.get("rationale")
            if not isinstance(rationale, str):
                rationale = None

            valid.append(FollowUpQuestion(question=question, rationale=rationale))

        truncated = valid[:_MAX_SUGGESTIONS]

        if not truncated:
            errors.append(
                AgentError(
                    code="no_valid_suggestions_returned",
                    message="No valid follow-up suggestions remained after shape validation",
                    recoverable=True,
                )
            )

        return truncated
