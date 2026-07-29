"""Semantic Retrieval agent implementation.

Follows the exact pattern established by
`navigraph_agents.understanding.intent_understanding.agent.IntentUnderstandingAgent`:
calls a real (or fake, in tests) LLM, parses structured JSON out of the
response, handles malformed output gracefully instead of crashing, emits a
lineage event, and records latency/token metadata.

The single most important behavior in this agent: the LLM is only ever
allowed to choose from a closed, caller-supplied candidate list. Every
`catalog_column_id` the LLM returns is validated against that list after
parsing -- an ID that doesn't appear in the candidates the caller provided
(a hallucination) is never trusted, even if it's a plausible-looking string.
It is rejected, the term is reported as unmatched, and a recoverable
`AgentError` is recorded so callers can observe that the LLM tried to
hallucinate.
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

from navigraph_agents.understanding.semantic_retrieval.contracts import (
    RetrievalCandidate,
    SemanticRetrievalInput,
    SemanticRetrievalOutput,
    SemanticRetrievalResult,
    TermMatch,
)

AGENT_NAME = "understanding.semantic_retrieval"
PROMPT_VERSION = "v1"

_PROMPT_PATH = Path(__file__).parent / "prompts" / "semantic_retrieval.md"


def _load_system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _format_candidates(candidates: list[RetrievalCandidate]) -> str:
    return json.dumps([c.model_dump() for c in candidates], indent=2)


class SemanticRetrievalAgent:
    """Matches unresolved business terms in a question against a closed
    candidate list of catalog columns."""

    def __init__(self, llm_client: LLMClient, tracer: Tracer | None = None) -> None:
        self._llm_client = llm_client
        self._tracer = tracer or get_tracer("navigraph-agent-runtime")
        self._system_prompt = _load_system_prompt()

    async def run(self, input: SemanticRetrievalInput) -> SemanticRetrievalOutput:
        start = time.perf_counter()
        request_context = input.request_context
        question = input.payload.question
        unresolved_terms = input.payload.unresolved_terms
        candidates = input.payload.candidates

        errors: list[AgentError] = []
        llm_response: LLMResponse | None = None

        with self._tracer.start_as_current_span("agent.semantic_retrieval.run") as span:
            span.set_attribute("navigraph.tenant_id", request_context.tenant_id)
            span.set_attribute("navigraph.trace_id", request_context.trace_id)
            span.set_attribute("navigraph.agent_name", AGENT_NAME)
            span.set_attribute("navigraph.unresolved_term_count", len(unresolved_terms))
            span.set_attribute("navigraph.candidate_count", len(candidates))

            if not unresolved_terms:
                # Nothing to resolve -- skip the LLM call entirely.
                matches: list[TermMatch] = []
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
                    f'Question: "{question}"\n\n'
                    f"Unresolved terms: {json.dumps(unresolved_terms)}\n\n"
                    f"Candidates:\n{_format_candidates(candidates)}"
                )

                try:
                    llm_response = await self._llm_client.complete(
                        system=self._system_prompt,
                        messages=[{"role": "user", "content": user_message}],
                        max_tokens=1536,
                    )
                except Exception as exc:  # noqa: BLE001 - never let an LLM-side failure crash the agent
                    errors.append(
                        AgentError(
                            code="llm_call_failed",
                            message=f"LLM call failed: {exc}",
                            recoverable=False,
                        )
                    )

                matches = self._parse_llm_response(
                    llm_response, unresolved_terms, candidates, errors
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

            result = SemanticRetrievalResult(matches=matches)

            matched_count = sum(1 for m in matches if m.matched)
            lineage_event = LineageEvent(
                agent_name=AGENT_NAME,
                input_summary=(
                    f"question={question!r} unresolved_terms={unresolved_terms} "
                    f"candidate_count={len(candidates)}"
                ),
                output_summary=f"matched={matched_count}/{len(unresolved_terms)}",
                tenant_id=request_context.tenant_id,
                trace_id=request_context.trace_id,
            )

            span.set_attribute("navigraph.matched_count", matched_count)

        record_agent_invocation(AGENT_NAME, latency_ms=latency_ms, success=not errors)
        for error in errors:
            record_agent_error(AGENT_NAME, error_code=error.code, recoverable=error.recoverable)

        return SemanticRetrievalOutput(
            result=result,
            confidence=confidence,
            lineage_events=[lineage_event],
            errors=errors,
            metadata=metadata,
        )

    @staticmethod
    def _parse_llm_response(
        llm_response: LLMResponse | None,
        unresolved_terms: list[str],
        candidates: list[RetrievalCandidate],
        errors: list[AgentError],
    ) -> list[TermMatch]:
        """Parse the LLM's JSON response into one `TermMatch` per unresolved
        term, in input order.

        Handles every way the response can be malformed -- not valid JSON,
        missing/invalid `matches`, a missing entry for a given term -- by
        recording a recoverable `AgentError` and falling back to an
        unmatched `TermMatch` for the affected term(s), exactly like
        `IntentUnderstandingAgent._parse_llm_response` falls back to
        `"unknown"` rather than raising.

        Critically: every non-null `catalog_column_id` the LLM returns is
        checked against the caller-supplied candidate list. An ID that isn't
        in that list is a hallucination and is never trusted -- the term is
        reported unmatched and a recoverable `AgentError` is recorded.
        """

        def _unmatched_fallback() -> list[TermMatch]:
            return [TermMatch(term=term, matched=False) for term in unresolved_terms]

        if llm_response is None:
            # The LLM call itself already failed; llm_call_failed error was
            # already recorded by the caller.
            return _unmatched_fallback()

        try:
            data = json.loads(llm_response.text)
        except json.JSONDecodeError as exc:
            errors.append(
                AgentError(
                    code="llm_response_not_json",
                    message=f"LLM response was not valid JSON: {exc}",
                    recoverable=True,
                )
            )
            return _unmatched_fallback()

        if not isinstance(data, dict):
            errors.append(
                AgentError(
                    code="llm_response_malformed",
                    message="LLM response JSON was not an object",
                    recoverable=True,
                )
            )
            return _unmatched_fallback()

        raw_matches = data.get("matches")
        if not isinstance(raw_matches, list):
            errors.append(
                AgentError(
                    code="llm_response_invalid_matches",
                    message=f"LLM returned a non-list 'matches': {raw_matches!r}",
                    recoverable=True,
                )
            )
            return _unmatched_fallback()

        # Index raw match entries by their `term` field for lookup, in case
        # the LLM reordered them (we always return in the caller's order).
        by_term: dict[str, dict[str, Any]] = {}
        for entry in raw_matches:
            if isinstance(entry, dict) and isinstance(entry.get("term"), str):
                by_term[entry["term"]] = entry

        candidates_by_id = {c.catalog_column_id: c for c in candidates}

        results: list[TermMatch] = []
        for term in unresolved_terms:
            entry = by_term.get(term)
            if entry is None:
                errors.append(
                    AgentError(
                        code="llm_response_missing_term_match",
                        message=f"LLM response had no match entry for term {term!r}",
                        recoverable=True,
                    )
                )
                results.append(TermMatch(term=term, matched=False))
                continue

            rationale = entry.get("rationale")
            if not isinstance(rationale, str):
                rationale = None

            catalog_column_id = entry.get("catalog_column_id")

            if catalog_column_id is None:
                # A legitimate, expected outcome: the LLM correctly found no
                # good candidate for this term. Not an error.
                results.append(TermMatch(term=term, matched=False, rationale=rationale))
                continue

            if not isinstance(catalog_column_id, str):
                errors.append(
                    AgentError(
                        code="llm_response_invalid_catalog_column_id",
                        message=(
                            f"LLM returned a non-string, non-null catalog_column_id "
                            f"for term {term!r}: {catalog_column_id!r}"
                        ),
                        recoverable=True,
                    )
                )
                results.append(TermMatch(term=term, matched=False, rationale=rationale))
                continue

            candidate = candidates_by_id.get(catalog_column_id)
            if candidate is None:
                # Hallucination: the LLM returned an ID that is not in the
                # closed candidate list it was given. Never trust it.
                errors.append(
                    AgentError(
                        code="llm_returned_invalid_candidate",
                        message=(
                            f"LLM matched term {term!r} to catalog_column_id "
                            f"{catalog_column_id!r}, which is not in the provided "
                            f"candidate list"
                        ),
                        recoverable=True,
                    )
                )
                results.append(TermMatch(term=term, matched=False, rationale=rationale))
                continue

            results.append(
                TermMatch(
                    term=term,
                    matched=True,
                    catalog_column_id=candidate.catalog_column_id,
                    table_name=candidate.table_name,
                    column_name=candidate.column_name,
                    rationale=rationale,
                )
            )

        return results
