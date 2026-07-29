"""Grounded Narrative Generation agent implementation.

Follows the exact structural pattern established by
`navigraph_agents.understanding.semantic_retrieval.agent.SemanticRetrievalAgent`:
an LLM call is made ONLY when it is actually needed (short-circuiting
entirely when there is no data to narrate, exactly like Semantic
Retrieval's empty-`unresolved_terms` short-circuit), the LLM is constrained
to real, caller-supplied data, and every value it claims is validated
before being trusted.

The single most important behavior in this agent, mirroring Semantic
Retrieval's own discipline verbatim: the LLM is only ever allowed to cite
real values it was actually given -- the final result rows and the anomaly
findings. Every `NarrativeCitation` the LLM returns is validated against a
closed candidate set of every real, checkable `(row_index, column, value)`
triple built directly from the caller's own data. A citation that points at
a `(row_index, column)` that doesn't exist, or that names a value that
doesn't match the real one, is never trusted -- it is dropped and a
recoverable `AgentError` is recorded. A second, independent layer then
scans the whole narrative text for numeric tokens that don't match ANY real
value anywhere, catching numbers the LLM stated without even attempting to
cite. See `_validate_citations` and `_scan_for_unverifiable_numbers` for the
exact two-layer mechanism and its documented, honest limitation.
"""

from __future__ import annotations

import json
import re
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

from navigraph_agents.insight.grounded_narrative_generation.contracts import (
    AnomalyFinding,
    NarrativeCitation,
    NarrativeGenerationInput,
    NarrativeGenerationOutput,
    NarrativeGenerationPayload,
    NarrativeGenerationResult,
)

AGENT_NAME = "insight.grounded_narrative_generation"
PROMPT_VERSION = "v1"

_PROMPT_PATH = Path(__file__).parent / "prompts" / "narrative_generation.md"

# Real, documented cap on how many of `final_rows` are rendered into the
# LLM prompt for very large result sets -- not a silent truncation: the
# prompt itself states "(first N of M rows)" (see `_format_user_message`),
# and citation validation below is always performed against the FULL
# `final_rows`/`anomalies` the caller provided, never just the capped view
# the LLM saw, so a citation into row 250 of a 500-row result set is still
# validated correctly even though the LLM itself only ever saw rows 0-199.
_MAX_ROWS_IN_PROMPT = 200

# Tolerance for treating two numbers as "the same real value" -- accounts
# for float/str round-tripping (e.g. `483920` vs `483920.0`), not for
# genuine rounding differences.
_FLOAT_TOLERANCE = 1e-6

_NUMBER_TOKEN_RE = re.compile(r"-?\d[\d,]*\.?\d*")

# Matches a citation bracket marker like `[1]` or `[12]` -- structural
# markup the narrative uses to point at a `citations` entry, not a data
# claim itself. Stripped out before the numeric-token scan below so a
# marker's own digit (e.g. the `1` in `[1]`) is never mistaken for an
# unverifiable data value -- without this, every correctly-cited narrative
# would spuriously fail the scan on its own citation markers.
_CITATION_MARKER_RE = re.compile(r"\[\d+\]")


def _load_system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _normalize(value: str) -> str:
    return value.replace(",", "").strip()


def _values_match(real_value: str, cited_value: str) -> bool:
    """Compare a real, known-good value against an LLM-cited value.

    Strips commas/whitespace from both first. If BOTH parse as floats,
    compares with a small tolerance (so `"483920"` and `"483920.0"` are
    treated as equal); otherwise falls back to plain string equality.
    """

    a, b = _normalize(real_value), _normalize(cited_value)
    try:
        return abs(float(a) - float(b)) < _FLOAT_TOLERANCE
    except ValueError:
        return a == b


def _build_candidate_values(
    final_rows: list[dict[str, Any]],
    anomalies: list[AnomalyFinding],
) -> dict[tuple[int, str], str]:
    """Build the closed candidate set of every real, checkable
    `(row_index, column) -> stringified value` pair: one entry per column
    of every row in `final_rows`, plus -- for each anomaly finding -- four
    synthetic entries at that finding's own `row_index` for
    `"z_score"`/`"mean"`/`"stdev"`/`"measure_value"`, since those are real
    numbers the finding legitimately carries even though they are not
    literal `final_rows` cells.
    """

    candidates: dict[tuple[int, str], str] = {}

    for row_index, row in enumerate(final_rows):
        for column, value in row.items():
            candidates[(row_index, column)] = str(value)

    for finding in anomalies:
        candidates[(finding.row_index, "z_score")] = str(finding.z_score)
        candidates[(finding.row_index, "mean")] = str(finding.mean)
        candidates[(finding.row_index, "stdev")] = str(finding.stdev)
        candidates[(finding.row_index, "measure_value")] = str(finding.measure_value)

    return candidates


class GroundedNarrativeGenerationAgent:
    """Writes a short, citation-grounded natural-language narrative
    answering the original question from the final result set and any
    anomaly findings."""

    def __init__(self, llm_client: LLMClient, tracer: Tracer | None = None) -> None:
        self._llm_client = llm_client
        self._tracer = tracer or get_tracer("navigraph-agent-runtime")
        self._system_prompt = _load_system_prompt()

    async def run(self, input: NarrativeGenerationInput) -> NarrativeGenerationOutput:
        start = time.perf_counter()
        request_context = input.request_context
        payload = input.payload
        question = payload.original_question

        errors: list[AgentError] = []
        llm_response: LLMResponse | None = None

        with self._tracer.start_as_current_span(
            "agent.grounded_narrative_generation.run"
        ) as span:
            span.set_attribute("navigraph.tenant_id", request_context.tenant_id)
            span.set_attribute("navigraph.trace_id", request_context.trace_id)
            span.set_attribute("navigraph.agent_name", AGENT_NAME)
            span.set_attribute("navigraph.final_row_count", payload.final_row_count)
            span.set_attribute("navigraph.anomaly_count", len(payload.anomalies))

            if payload.final_row_count == 0:
                # Nothing to narrate -- skip the LLM call entirely, exactly
                # like SemanticRetrievalAgent's empty-`unresolved_terms`
                # short-circuit.
                narrative = "No data was returned for this question."
                citations: list[NarrativeCitation] = []
                unverifiable_numbers: list[str] = []
                confidence = 1.0

                metadata = AgentMetadata(
                    latency_ms=(time.perf_counter() - start) * 1000.0,
                    model_version=None,
                    prompt_version=None,
                    tokens_input=None,
                    tokens_output=None,
                )
            else:
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

                narrative, raw_citations = self._parse_llm_response(llm_response, errors)

                candidates = _build_candidate_values(payload.final_rows, payload.anomalies)
                citations = self._validate_citations(raw_citations, candidates, errors)
                unverifiable_numbers = self._scan_for_unverifiable_numbers(
                    narrative, candidates, errors
                )

                # Per this agent's documented confidence rule: 1.0 only when
                # nothing at all went wrong; any error (a dropped citation,
                # an unverifiable number, a malformed response, or an LLM
                # call failure) degrades to 0.5 -- a partial, still-useful
                # result rather than an outright failure, since a narrative
                # with one bad citation stripped out is still meaningful.
                confidence = 1.0 if not errors else 0.5

                metadata = AgentMetadata(
                    latency_ms=(time.perf_counter() - start) * 1000.0,
                    model_version=llm_response.model if llm_response else None,
                    prompt_version=PROMPT_VERSION,
                    tokens_input=llm_response.tokens_input if llm_response else None,
                    tokens_output=llm_response.tokens_output if llm_response else None,
                )

            result = NarrativeGenerationResult(
                narrative=narrative,
                citations=citations,
                unverifiable_numbers=unverifiable_numbers,
            )

            lineage_event = LineageEvent(
                agent_name=AGENT_NAME,
                input_summary=(
                    f"question={question!r} final_row_count={payload.final_row_count} "
                    f"anomaly_count={len(payload.anomalies)}"
                ),
                output_summary=(
                    f"citations={len(result.citations)} "
                    f"unverifiable_numbers={len(result.unverifiable_numbers)}"
                ),
                tenant_id=request_context.tenant_id,
                trace_id=request_context.trace_id,
            )

            span.set_attribute("navigraph.citation_count", len(result.citations))
            span.set_attribute(
                "navigraph.unverifiable_number_count", len(result.unverifiable_numbers)
            )

        record_agent_invocation(
            AGENT_NAME, latency_ms=metadata.latency_ms, success=not errors
        )
        for error in errors:
            record_agent_error(AGENT_NAME, error_code=error.code, recoverable=error.recoverable)

        return NarrativeGenerationOutput(
            result=result,
            confidence=confidence,
            lineage_events=[lineage_event],
            errors=errors,
            metadata=metadata,
        )

    @staticmethod
    def _build_user_message(payload: NarrativeGenerationPayload) -> str:
        rows_for_prompt = payload.final_rows[:_MAX_ROWS_IN_PROMPT]

        return (
            f'Question: "{payload.original_question}"\n\n'
            f"Final result set (showing {len(rows_for_prompt)} of "
            f"{payload.final_row_count} rows):\n"
            f"columns: {json.dumps(payload.final_columns)}\n"
            f"rows: {json.dumps(rows_for_prompt, default=str)}\n\n"
            f"Chart: {json.dumps(payload.chart.model_dump())}\n\n"
            f"Anomalies: {json.dumps([a.model_dump() for a in payload.anomalies])}"
        )

    @staticmethod
    def _parse_llm_response(
        llm_response: LLMResponse | None,
        errors: list[AgentError],
    ) -> tuple[str, list[NarrativeCitation]]:
        """Parse the LLM's JSON response into (narrative, citations).

        Handles every way the response can be malformed -- not valid JSON,
        a non-object top level, a non-string `narrative`, a non-list
        `citations`, a malformed individual citation entry -- by recording a
        single recoverable `AgentError(code="narrative_llm_response_malformed")`
        and falling back to an empty narrative and/or no citations for the
        affected part, rather than raising, exactly like
        `SemanticRetrievalAgent._parse_llm_response`.

        Note this only checks *shape*: whether each citation's
        `(row_index, column)` actually exists and whether `cited_value`
        actually matches is the closed-candidate-set validation done
        separately in `_validate_citations`.
        """

        if llm_response is None:
            # The LLM call itself already failed; llm_call_failed error was
            # already recorded by the caller.
            return "", []

        try:
            data = json.loads(strip_json_code_fence(llm_response.text))
        except json.JSONDecodeError as exc:
            errors.append(
                AgentError(
                    code="narrative_llm_response_malformed",
                    message=f"LLM response was not valid JSON: {exc}",
                    recoverable=True,
                )
            )
            return "", []

        if not isinstance(data, dict):
            errors.append(
                AgentError(
                    code="narrative_llm_response_malformed",
                    message="LLM response JSON was not an object",
                    recoverable=True,
                )
            )
            return "", []

        narrative = data.get("narrative")
        if not isinstance(narrative, str):
            errors.append(
                AgentError(
                    code="narrative_llm_response_malformed",
                    message=f"LLM returned a non-string 'narrative': {narrative!r}",
                    recoverable=True,
                )
            )
            narrative = ""

        raw_citations = data.get("citations", [])
        if not isinstance(raw_citations, list):
            errors.append(
                AgentError(
                    code="narrative_llm_response_malformed",
                    message=f"LLM returned a non-list 'citations': {raw_citations!r}",
                    recoverable=True,
                )
            )
            return narrative, []

        citations: list[NarrativeCitation] = []
        for entry in raw_citations:
            if not isinstance(entry, dict):
                errors.append(
                    AgentError(
                        code="narrative_llm_response_malformed",
                        message=f"Citation entry was not an object: {entry!r}",
                        recoverable=True,
                    )
                )
                continue

            citation_id = entry.get("citation_id")
            row_index = entry.get("row_index")
            column = entry.get("column")
            cited_value = entry.get("cited_value")

            if (
                not isinstance(citation_id, int)
                or not isinstance(row_index, int)
                or not isinstance(column, str)
                or not isinstance(cited_value, str)
            ):
                errors.append(
                    AgentError(
                        code="narrative_llm_response_malformed",
                        message=f"Citation entry had a missing/invalid field: {entry!r}",
                        recoverable=True,
                    )
                )
                continue

            citations.append(
                NarrativeCitation(
                    citation_id=citation_id,
                    row_index=row_index,
                    column=column,
                    cited_value=cited_value,
                )
            )

        return narrative, citations

    @staticmethod
    def _validate_citations(
        citations: list[NarrativeCitation],
        candidates: dict[tuple[int, str], str],
        errors: list[AgentError],
    ) -> list[NarrativeCitation]:
        """The core grounding check: every citation's `(row_index, column)`
        must be a key in the closed candidate set built from the real
        result rows and anomaly findings, and its `cited_value` must match
        the real value at that key. Either failure drops the citation
        entirely (never trusted, even partially) and records ONE
        recoverable `AgentError(code="llm_cited_fabricated_value")`.
        """

        valid: list[NarrativeCitation] = []

        for citation in citations:
            key = (citation.row_index, citation.column)

            if key not in candidates:
                errors.append(
                    AgentError(
                        code="llm_cited_fabricated_value",
                        message=(
                            f"citation {citation.citation_id} references "
                            f"({citation.row_index}, {citation.column!r}) which does not "
                            f"exist in the real result set or anomaly data"
                        ),
                        recoverable=True,
                    )
                )
                continue

            real_value = candidates[key]
            if not _values_match(real_value, citation.cited_value):
                errors.append(
                    AgentError(
                        code="llm_cited_fabricated_value",
                        message=(
                            f"citation {citation.citation_id} cites value "
                            f"{citation.cited_value!r} for "
                            f"({citation.row_index}, {citation.column!r}) which does not "
                            f"match the real value {real_value!r}"
                        ),
                        recoverable=True,
                    )
                )
                continue

            valid.append(citation)

        return valid

    @staticmethod
    def _scan_for_unverifiable_numbers(
        narrative: str,
        candidates: dict[tuple[int, str], str],
        errors: list[AgentError],
    ) -> list[str]:
        """Second, defensive layer: a whole-narrative scan, independent of
        what the LLM chose to cite. Extracts every numeric-looking token
        from `narrative` and checks whether it matches ANY real value
        anywhere in the candidate set (not just the row/column a citation
        claimed it came from). A token matching nothing real anywhere is
        recorded in the returned list, with ONE recoverable
        `AgentError(code="narrative_contains_unverified_number")` per
        distinct unmatched token (not one per occurrence).

        HONEST LIMITATION (documented, not silently glossed over): this
        two-layer check -- per-citation validation in `_validate_citations`
        plus this whole-data scan -- can only ever catch wholesale
        fabrication, i.e. a number that does not appear ANYWHERE in the real
        data at all. It cannot catch a real value MISATTRIBUTED to the
        wrong row or group (e.g. citing the West region's real number as if
        it were the East region's) -- if that exact value is real and
        present somewhere in the data, it passes this scan regardless of
        which row/column it was actually attached to. Catching
        misattribution would require re-deriving each claim's intended
        row/group from the narrative's own prose, which this scan
        deliberately does not attempt.
        """

        real_floats: set[float] = set()
        real_strings: set[str] = set()
        for value in candidates.values():
            normalized = _normalize(value)
            try:
                real_floats.add(float(normalized))
            except ValueError:
                real_strings.add(normalized)

        unverifiable: list[str] = []
        seen: set[str] = set()

        narrative_without_markers = _CITATION_MARKER_RE.sub("", narrative)

        for token in _NUMBER_TOKEN_RE.findall(narrative_without_markers):
            normalized = _normalize(token)
            if normalized in seen:
                continue
            seen.add(normalized)

            try:
                token_value = float(normalized)
                matched = any(
                    abs(token_value - real) < _FLOAT_TOLERANCE for real in real_floats
                )
            except ValueError:
                matched = normalized in real_strings

            if not matched:
                unverifiable.append(token)
                errors.append(
                    AgentError(
                        code="narrative_contains_unverified_number",
                        message=(
                            f"Narrative contains number {token!r} that does not match any "
                            f"real value in the result set or anomaly data"
                        ),
                        recoverable=True,
                    )
                )

        return unverifiable
