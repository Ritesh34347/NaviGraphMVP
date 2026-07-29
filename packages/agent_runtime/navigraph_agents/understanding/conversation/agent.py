"""Conversation agent implementation.

Follows the exact pattern established by
`navigraph_agents.understanding.intent_understanding.agent.IntentUnderstandingAgent`:
calls a real (or fake, in tests) LLM, parses structured JSON out of the
response, handles malformed output gracefully instead of crashing, emits a
lineage event, and records latency/token metadata.

The one addition here is a deterministic short-circuit: when there is no
conversation history to resolve against, there is nothing for an LLM to add
-- the new question already stands alone -- so this agent skips the LLM
call entirely. This is the common case (most turns start a new
conversation, or are already the first turn) and must stay cheap and fast.
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

from navigraph_agents.understanding.conversation.contracts import (
    ConversationInput,
    ConversationOutput,
    ConversationResult,
    ConversationTurn,
)

AGENT_NAME = "understanding.conversation"
PROMPT_VERSION = "v1"

_PROMPT_PATH = Path(__file__).parent / "prompts" / "conversation.md"

# How many of the most recent turns to include in the prompt. Bounded so the
# prompt doesn't grow unboundedly with a long-running conversation -- recent
# turns are overwhelmingly what follow-ups reference.
_MAX_HISTORY_TURNS = 10


def _load_system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _format_history(history: list[ConversationTurn]) -> str:
    recent = history[-_MAX_HISTORY_TURNS:]
    lines = []
    for turn in recent:
        lines.append(
            f'[{turn.turn_id}] raw: "{turn.raw_question}" resolved: "{turn.resolved_question}"'
        )
    return "\n".join(lines)


class ConversationAgent:
    """Resolves a follow-up question against prior conversation turns into a
    fully standalone question."""

    def __init__(self, llm_client: LLMClient, tracer: Tracer | None = None) -> None:
        self._llm_client = llm_client
        self._tracer = tracer or get_tracer("navigraph-agent-runtime")
        self._system_prompt = _load_system_prompt()

    async def run(self, input: ConversationInput) -> ConversationOutput:
        start = time.perf_counter()
        request_context = input.request_context
        question = input.payload.question
        history = input.payload.conversation_history

        errors: list[AgentError] = []
        llm_response: LLMResponse | None = None

        with self._tracer.start_as_current_span("agent.conversation.run") as span:
            span.set_attribute("navigraph.tenant_id", request_context.tenant_id)
            span.set_attribute("navigraph.trace_id", request_context.trace_id)
            span.set_attribute("navigraph.agent_name", AGENT_NAME)
            span.set_attribute("navigraph.conversation_history_length", len(history))

            if not history:
                # No prior context to resolve against -- the question already
                # stands alone. Skip the LLM call entirely: this is the
                # common case and must be cheap and deterministic.
                is_follow_up = False
                referenced_turn_id = None
                resolved_question = question
                confidence = 1.0
                latency_ms = (time.perf_counter() - start) * 1000.0

                metadata = AgentMetadata(
                    latency_ms=latency_ms,
                    model_version=None,
                    prompt_version=None,
                    tokens_input=None,
                    tokens_output=None,
                )
            else:
                user_message = (
                    "Conversation history:\n"
                    f"{_format_history(history)}\n\n"
                    f'New question: "{question}"'
                )

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

                is_follow_up, referenced_turn_id, resolved_question = self._parse_llm_response(
                    llm_response, question, errors
                )

                confidence = 0.0 if errors else 1.0
                latency_ms = (time.perf_counter() - start) * 1000.0

                metadata = AgentMetadata(
                    latency_ms=latency_ms,
                    model_version=llm_response.model if llm_response else None,
                    prompt_version=PROMPT_VERSION,
                    tokens_input=llm_response.tokens_input if llm_response else None,
                    tokens_output=llm_response.tokens_output if llm_response else None,
                )

            result = ConversationResult(
                resolved_question=resolved_question,
                is_follow_up=is_follow_up,
                referenced_turn_id=referenced_turn_id,
                raw_question=question,
            )

            lineage_event = LineageEvent(
                agent_name=AGENT_NAME,
                input_summary=f"question={question!r} history_len={len(history)}",
                output_summary=(
                    f"is_follow_up={is_follow_up} resolved_question={resolved_question!r}"
                ),
                tenant_id=request_context.tenant_id,
                trace_id=request_context.trace_id,
            )

            span.set_attribute("navigraph.is_follow_up", is_follow_up)

        record_agent_invocation(AGENT_NAME, latency_ms=latency_ms, success=not errors)
        for error in errors:
            record_agent_error(AGENT_NAME, error_code=error.code, recoverable=error.recoverable)

        return ConversationOutput(
            result=result,
            confidence=confidence,
            lineage_events=[lineage_event],
            errors=errors,
            metadata=metadata,
        )

    @staticmethod
    def _parse_llm_response(
        llm_response: LLMResponse | None,
        question: str,
        errors: list[AgentError],
    ) -> tuple[bool, str | None, str]:
        """Parse the LLM's JSON response into
        (is_follow_up, referenced_turn_id, resolved_question).

        Handles every way the response can be malformed -- not valid JSON,
        missing/invalid `is_follow_up`, invalid `resolved_question` -- by
        recording a recoverable `AgentError` and falling back to the safe
        "not a follow-up" interpretation (original question unchanged)
        rather than guessing, exactly like
        `IntentUnderstandingAgent._parse_llm_response` does.
        """

        if llm_response is None:
            # The LLM call itself already failed; llm_call_failed error was
            # already recorded by the caller.
            return False, None, question

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
            return False, None, question

        if not isinstance(data, dict):
            errors.append(
                AgentError(
                    code="llm_response_malformed",
                    message="LLM response JSON was not an object",
                    recoverable=True,
                )
            )
            return False, None, question

        is_follow_up = data.get("is_follow_up")
        if not isinstance(is_follow_up, bool):
            errors.append(
                AgentError(
                    code="llm_response_invalid_is_follow_up",
                    message=f"LLM returned a non-boolean is_follow_up: {is_follow_up!r}",
                    recoverable=True,
                )
            )
            return False, None, question

        if not is_follow_up:
            # Not a follow-up: the resolved question is the original
            # question, regardless of what (if anything) the LLM echoed back.
            return False, None, question

        referenced_turn_id = data.get("referenced_turn_id")
        if referenced_turn_id is not None and not isinstance(referenced_turn_id, str):
            errors.append(
                AgentError(
                    code="llm_response_invalid_referenced_turn_id",
                    message=(
                        f"LLM returned a non-string, non-null referenced_turn_id: "
                        f"{referenced_turn_id!r}"
                    ),
                    recoverable=True,
                )
            )
            referenced_turn_id = None

        resolved_question = data.get("resolved_question")
        if not isinstance(resolved_question, str) or not resolved_question.strip():
            errors.append(
                AgentError(
                    code="llm_response_invalid_resolved_question",
                    message=(
                        f"LLM claimed is_follow_up=true but returned an invalid "
                        f"resolved_question: {resolved_question!r}"
                    ),
                    recoverable=True,
                )
            )
            # Can't trust the rewrite -- fall back to treating this as NOT a
            # follow-up rather than guessing at a rewrite.
            return False, None, question

        return True, referenced_turn_id, resolved_question
