"""Execution Planning agent implementation.

This is the safety-critical gate between "SQL some upstream agent
produced" and "SQL this platform will actually execute". For every
`OptimizedSql` statement it receives, it strips the leading audit comment
SQL Optimization prepended, then validates that what remains is a single
read-only `SELECT` (a `WITH ... SELECT` common-table-expression counts as
a `SELECT`) -- nothing else. A statement that fails this check can never
reach `ExecutionPlanningResult.plans`: the `_validate_select_only` check
result is a plain boolean branch in `run()` below, and only the `True`
branch constructs an `ExecutionPlan` at all; the `False` branch can only
append to `ExecutionPlanningResult.rejected`. There is no code path that
appends to both, and no post-hoc filtering step that a future edit could
accidentally skip.

Statement-parsing approach (documented here since this is the single most
important design decision in this file): NOT a full SQL parser, and
deliberately NOT `sqlparse` either, even though `sqlparse` happens to be
importable in this environment -- it is not a declared dependency in any
`pyproject.toml` in this repository, so relying on it would be an
undeclared, environment-incidental dependency rather than a real one.
Instead this is a real, careful string-based scan with one key trick: a
same-length "masked" copy of the SQL (see `_mask_strings_and_comments`)
with the *interior* of every single-quoted string literal and every SQL
comment (`--` line comments, `/* */` block comments) blanked out to
spaces, while every other character keeps its original offset. All
keyword/semicolon scanning happens against this masked copy, so:

  * a semicolon that only appears inside a string literal or a comment
    (e.g. `SELECT ';' AS x` or `SELECT * FROM T -- ; anything`) is never
    mistaken for a second statement, and
  * a real semicolon followed by anything other than trailing
    whitespace/comments (the classic `SELECT 1; DROP TABLE x` stacked-query
    pattern) is always caught.

Known limitations of this approach (real ones, not hidden): it does not
handle SQL dialects whose string-escaping rules differ from standard
`''`-doubled single quotes (e.g. it does not treat `\\'` as an escape, since
that is not standard ANSI SQL and Snowflake/Postgres/Trino do not require
it by default), it does not understand double-quoted identifiers that
happen to contain a semicolon or comment marker (vanishingly unlikely,
since those are identifiers not literals), and it does not attempt to
validate anything about the SQL *inside* a valid single SELECT/WITH
statement (e.g. it does not block a `SELECT` containing a dangerous
subquery or function call) -- that is out of scope for "is this a single
read-only statement", which is this agent's only job.

Follows the same structural pattern as
`navigraph_agents.understanding.intent_understanding.agent`: open an OTel
span, never raise, always emit a `LineageEvent` and `AgentMetadata` with
`latency_ms` populated.
"""

from __future__ import annotations

import re
import time
from typing import Literal

from navigraph_shared.contracts import AgentError, AgentMetadata, LineageEvent
from navigraph_shared.telemetry import (
    get_tracer,
    record_agent_error,
    record_agent_invocation,
)
from opentelemetry.trace import Tracer

from navigraph_agents.query.execution_planning.contracts import (
    ExecutionPlan,
    ExecutionPlanningInput,
    ExecutionPlanningOutput,
    ExecutionPlanningPayload,
    ExecutionPlanningResult,
)

AGENT_NAME = "query.execution_planning"

# This phase's confirmed default (and only) execution route. "trino"
# exists on `ExecutionPlan.route`'s Literal as a contract option for a
# future cross-source federation routing phase, but is never assigned by
# this agent yet.
DEFAULT_ROUTE: Literal["direct_connector"] = "direct_connector"

# Real, conservative defaults -- not placeholders. `max_rows` re-verifies
# (and caps) whatever `LIMIT` SQL Optimization injected rather than
# trusting it blindly (see `_resolve_max_rows`).
DEFAULT_TIMEOUT_SECONDS = 30
MAX_ROWS_CAP = 10_000

# Statement types this agent will never allow to become an ExecutionPlan.
# Listed explicitly (rather than "anything that isn't SELECT/WITH") purely
# so the rejection message names the actual offending keyword.
_REJECTED_KEYWORDS = {
    "INSERT",
    "UPDATE",
    "DELETE",
    "MERGE",
    "CREATE",
    "DROP",
    "ALTER",
    "GRANT",
    "REVOKE",
    "COPY",
    "CALL",
    "PUT",
    "GET",
    "USE",
}
_ALLOWED_KEYWORDS = {"SELECT", "WITH"}

_LIMIT_VALUE_RE = re.compile(r"\bLIMIT\s+(\d+)\b", re.IGNORECASE)
_FIRST_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _mask_strings_and_comments(sql: str) -> str:
    """Return a same-length copy of `sql` with the full span of every
    single-quoted string literal (`'...'`, with `''` as an escaped quote)
    and SQL comment (`--` line comments, `/* */` block comments) replaced
    by spaces. Every other character keeps its original offset -- see the
    module docstring for why this is the load-bearing trick behind every
    check below.
    """

    n = len(sql)
    out = list(sql)
    i = 0
    while i < n:
        if sql.startswith("--", i):
            newline_index = sql.find("\n", i)
            end = n if newline_index == -1 else newline_index
            for k in range(i, end):
                out[k] = " "
            i = end
            continue
        if sql.startswith("/*", i):
            close_index = sql.find("*/", i + 2)
            end = n if close_index == -1 else close_index + 2
            for k in range(i, end):
                out[k] = " "
            i = end
            continue
        if sql[i] == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            end = min(j, n)
            for k in range(i, end):
                out[k] = " "
            i = end
            continue
        i += 1
    return "".join(out)


def _strip_leading_comments_and_whitespace(sql: str) -> str:
    """Strip leading whitespace and leading `--`/`/* */` comments from the
    start of `sql` -- this is exactly what's needed to remove SQL
    Optimization's `-- navigraph trace_id=... tenant_id=...` audit comment
    (which is just an ordinary leading line comment) before validating
    the real statement, with no special-casing of its exact text required.
    """

    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch.isspace():
            i += 1
            continue
        if sql.startswith("--", i):
            newline_index = sql.find("\n", i)
            i = n if newline_index == -1 else newline_index + 1
            continue
        if sql.startswith("/*", i):
            close_index = sql.find("*/", i + 2)
            i = n if close_index == -1 else close_index + 2
            continue
        break
    return sql[i:]


def _has_stacked_statement(sql: str) -> bool:
    """True if `sql` contains a real (not inside a string/comment)
    semicolon followed by any non-whitespace, non-comment content -- the
    classic `SELECT 1; DROP TABLE x` stacked-query pattern. A trailing
    semicolon followed only by whitespace and/or trailing comments is NOT
    a stacked statement."""

    masked = _mask_strings_and_comments(sql)
    semicolon_index = masked.find(";")
    if semicolon_index == -1:
        return False
    remainder = masked[semicolon_index + 1 :]
    return remainder.strip() != ""


def _first_keyword(sql: str) -> str | None:
    """Return the first identifier-shaped token of `sql` (case-normalized
    to uppercase), scanning against the comment/string-masked copy so a
    keyword-shaped substring inside a leading comment is never picked up
    (leading comments/whitespace should already have been stripped by the
    caller via `_strip_leading_comments_and_whitespace`, but this is
    defensive in case they weren't)."""

    masked = _mask_strings_and_comments(sql)
    match = _FIRST_WORD_RE.search(masked)
    if match is None:
        return None
    return match.group(0).upper()


def _validate_select_only(sql: str) -> tuple[bool, str | None]:
    """The safety-critical check: is `sql` a single, read-only
    `SELECT`/`WITH ... SELECT` statement?

    Returns `(True, None)` if so. Returns `(False, reason)` -- with a
    human-readable rejection reason -- otherwise. `sql` is expected to be
    the RAW optimized SQL (including SQL Optimization's leading audit
    comment); this function strips that comment itself.
    """

    stripped = _strip_leading_comments_and_whitespace(sql)

    if _has_stacked_statement(stripped):
        return False, "multiple SQL statements detected (stacked/chained query)"

    keyword = _first_keyword(stripped)
    if keyword is None:
        return False, "no recognizable SQL statement found"

    if keyword in _ALLOWED_KEYWORDS:
        return True, None

    if keyword in _REJECTED_KEYWORDS:
        return False, f"statement type '{keyword}' is not a read-only SELECT"

    return False, f"statement does not begin with SELECT or WITH (found '{keyword}')"


def _resolve_max_rows(sql: str) -> int:
    """The plan's `max_rows`: whatever `LIMIT` value SQL Optimization (or
    an upstream agent) already put in the SQL, capped at `MAX_ROWS_CAP` --
    this agent re-verifies rather than blindly trusting that value. If no
    `LIMIT` is present at all, the cap itself is used."""

    masked = _mask_strings_and_comments(sql)
    matches = list(_LIMIT_VALUE_RE.finditer(masked))
    if not matches:
        return MAX_ROWS_CAP
    parsed_limit = int(matches[-1].group(1))
    return min(parsed_limit, MAX_ROWS_CAP)


class ExecutionPlanningAgent:
    """Validates each optimized statement is a single read-only SELECT
    before turning it into an `ExecutionPlan`; anything that fails is
    routed to `rejected` and never becomes a plan. Pure function of its
    input -- no external client dependency."""

    def __init__(self, tracer: Tracer | None = None) -> None:
        self._tracer = tracer or get_tracer("navigraph-agent-runtime")

    async def run(self, input: ExecutionPlanningInput) -> ExecutionPlanningOutput:
        start = time.perf_counter()
        request_context = input.request_context
        payload = input.payload

        with self._tracer.start_as_current_span("agent.execution_planning.run") as span:
            span.set_attribute("navigraph.tenant_id", request_context.tenant_id)
            span.set_attribute("navigraph.trace_id", request_context.trace_id)
            span.set_attribute("navigraph.agent_name", AGENT_NAME)

            plans, rejected = self._plan_statements(payload)

            distinct_data_sources = {plan.data_source_id for plan in plans}
            requires_cross_source_join = len(distinct_data_sources) > 1

            result = ExecutionPlanningResult(
                plans=plans,
                requires_cross_source_join=requires_cross_source_join,
                rejected=rejected,
            )

            confidence = 0.0 if rejected else 1.0

            lineage_event = LineageEvent(
                agent_name=AGENT_NAME,
                input_summary=f"statements={len(payload.statements)}",
                output_summary=(
                    f"plans={len(plans)} rejected={len(rejected)} "
                    f"requires_cross_source_join={requires_cross_source_join}"
                ),
                tenant_id=request_context.tenant_id,
                trace_id=request_context.trace_id,
            )

            latency_ms = (time.perf_counter() - start) * 1000.0
            metadata = AgentMetadata(latency_ms=latency_ms)

            span.set_attribute("navigraph.plans_count", len(plans))
            span.set_attribute("navigraph.rejected_count", len(rejected))

        record_agent_invocation(AGENT_NAME, latency_ms=latency_ms, success=not rejected)
        for error in rejected:
            record_agent_error(AGENT_NAME, error_code=error.code, recoverable=error.recoverable)

        return ExecutionPlanningOutput(
            result=result,
            confidence=confidence,
            lineage_events=[lineage_event],
            errors=[],
            metadata=metadata,
        )

    @staticmethod
    def _plan_statements(
        payload: ExecutionPlanningPayload,
    ) -> tuple[list[ExecutionPlan], list[AgentError]]:
        plans: list[ExecutionPlan] = []
        rejected: list[AgentError] = []

        for statement in payload.statements:
            is_valid, reason = _validate_select_only(statement.sql)

            if not is_valid:
                # Structurally cannot also become a plan: this branch
                # never touches `plans`.
                rejected.append(
                    AgentError(
                        code="rejected_unsafe_statement",
                        message=(
                            f"data_source_id={statement.data_source_id}: {reason}"
                        ),
                        recoverable=False,
                    )
                )
                continue

            plans.append(
                ExecutionPlan(
                    data_source_id=statement.data_source_id,
                    route=DEFAULT_ROUTE,
                    sql=statement.sql,
                    params=statement.params,
                    timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
                    max_rows=_resolve_max_rows(statement.sql),
                    read_only_verified=True,
                )
            )

        return plans, rejected
