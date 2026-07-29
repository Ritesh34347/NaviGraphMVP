"""Intent Understanding agent implementation.

This is the ONE agent in this phase that is genuinely real and working end
to end: it calls a real (or fake, in tests) LLM, parses structured JSON out
of the response, handles malformed output gracefully instead of crashing,
emits a lineage event, and records latency/token metadata -- the exact
pattern every future agent (built in later phases) should follow.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import cast

from navigraph_shared.contracts import AgentError, AgentMetadata, LineageEvent
from navigraph_shared.llm import LLMClient, LLMResponse, strip_json_code_fence
from navigraph_shared.telemetry import (
    get_tracer,
    record_agent_error,
    record_agent_invocation,
)
from opentelemetry.trace import Tracer

from navigraph_agents.understanding.intent_understanding.contracts import (
    IntentLabel,
    IntentUnderstandingInput,
    IntentUnderstandingOutput,
    IntentUnderstandingResult,
)

AGENT_NAME = "understanding.intent_understanding"
PROMPT_VERSION = "v1"

_ALLOWED_INTENTS = {
    "metric_lookup",
    "trend_analysis",
    "comparison",
    "anomaly_investigation",
    "unknown",
}

_PROMPT_PATH = Path(__file__).parent / "prompts" / "intent_understanding.md"


def _load_system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


class IntentUnderstandingAgent:
    """Classifies a business question's intent and extracts its entities."""

    def __init__(self, llm_client: LLMClient, tracer: Tracer | None = None) -> None:
        self._llm_client = llm_client
        self._tracer = tracer or get_tracer("navigraph-agent-runtime")
        self._system_prompt = _load_system_prompt()

    async def run(self, input: IntentUnderstandingInput) -> IntentUnderstandingOutput:
        start = time.perf_counter()
        request_context = input.request_context
        question = input.payload.question

        errors: list[AgentError] = []
        llm_response: LLMResponse | None = None

        with self._tracer.start_as_current_span("agent.intent_understanding.run") as span:
            span.set_attribute("navigraph.tenant_id", request_context.tenant_id)
            span.set_attribute("navigraph.trace_id", request_context.trace_id)
            span.set_attribute("navigraph.agent_name", AGENT_NAME)

            try:
                llm_response = await self._llm_client.complete(
                    system=self._system_prompt,
                    messages=[{"role": "user", "content": question}],
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

            intent, entities = self._parse_llm_response(llm_response, errors)

            result = IntentUnderstandingResult(
                intent=intent,
                entities=entities,
                raw_question=question,
            )

            confidence = 0.0 if errors else 1.0

            lineage_event = LineageEvent(
                agent_name=AGENT_NAME,
                input_summary=f"question={question!r}",
                output_summary=f"intent={intent} entities={entities}",
                tenant_id=request_context.tenant_id,
                trace_id=request_context.trace_id,
            )

            latency_ms = (time.perf_counter() - start) * 1000.0

            metadata = AgentMetadata(
                latency_ms=latency_ms,
                model_version=llm_response.model if llm_response else None,
                prompt_version=PROMPT_VERSION,
                tokens_input=llm_response.tokens_input if llm_response else None,
                tokens_output=llm_response.tokens_output if llm_response else None,
            )

            span.set_attribute("navigraph.intent", intent)

        record_agent_invocation(AGENT_NAME, latency_ms=latency_ms, success=not errors)
        for error in errors:
            record_agent_error(AGENT_NAME, error_code=error.code, recoverable=error.recoverable)

        return IntentUnderstandingOutput(
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
    ) -> tuple[IntentLabel, list[str]]:
        """Parse the LLM's JSON response into (intent, entities).

        Handles every way the response can be malformed -- not valid JSON,
        missing/invalid `intent`, `entities` present but not a list of
        strings -- by recording a recoverable `AgentError` and falling back
        to the safe `"unknown"` intent, rather than raising.
        """

        if llm_response is None:
            # The LLM call itself already failed; llm_call_failed error was
            # already recorded by the caller.
            return "unknown", []

        try:
            data = json.loads(strip_json_code_fence(llm_response.text))
        except json.JSONDecodeError as exc:
            errors.append(
                AgentError(
                    code="llm_response_not_json",
                    message=f"LLM response was not valid JSON: {exc}",
                    recoverable=True,
                )
            )
            return "unknown", []

        if not isinstance(data, dict):
            errors.append(
                AgentError(
                    code="llm_response_malformed",
                    message="LLM response JSON was not an object",
                    recoverable=True,
                )
            )
            return "unknown", []

        intent = data.get("intent")
        if intent not in _ALLOWED_INTENTS:
            errors.append(
                AgentError(
                    code="llm_response_invalid_intent",
                    message=f"LLM returned an unrecognized intent: {intent!r}",
                    recoverable=True,
                )
            )
            return "unknown", []

        entities = data.get("entities", [])
        if not isinstance(entities, list) or not all(isinstance(e, str) for e in entities):
            errors.append(
                AgentError(
                    code="llm_response_invalid_entities",
                    message="LLM returned entities that were not a list of strings",
                    recoverable=True,
                )
            )
            # The intent itself was valid, so keep it -- only entities were bad.
            return cast(IntentLabel, intent), []

        return cast(IntentLabel, intent), entities
