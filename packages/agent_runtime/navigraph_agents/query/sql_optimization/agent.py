"""SQL Optimization agent implementation.

Fully deterministic: no LLM call, no `prompts/` directory, no external
client dependency -- this agent is a pure function of its input, exactly
like `navigraph_agents.understanding.schema_mapping`. For every
`GeneratedSql` statement it receives, it applies (in order):

1. A WHERE-clause predicate reorder: pure-equality predicates are moved
   before range/LIKE/IN predicates within a top-level, AND-only WHERE
   clause. A no-op (and NOT listed in `applied_rules`) whenever there is
   no WHERE clause, only one predicate, a top-level OR, or the predicates
   are already in equality-first order.
2. A hard `LIMIT` injection, if the statement does not already end with
   one.
3. An audit-trail comment prepended to the final SQL text. Unlike the
   other two rules this always fires -- there is no "no-op" case for it.

It also emits advisory (never blocking) warnings when a statement with no
WHERE clause at all references a table whose row-count estimate exceeds
`LARGE_TABLE_ROW_THRESHOLD`.

None of this is a security boundary -- the Execution Planning agent
downstream is what actually rejects unsafe SQL. This agent's SQL
manipulation is deliberately light/string-based (not a full SQL parser):
good enough to be a real, correct optimization for the well-formed,
single-statement SELECTs SQL Generation produces, not an attempt to
handle arbitrary user-supplied SQL text safely.

Follows the same structural pattern as
`navigraph_agents.understanding.intent_understanding.agent`: open an OTel
span, never raise, always emit a `LineageEvent` and `AgentMetadata` with
`latency_ms` populated.
"""

from __future__ import annotations

import re
import time

from navigraph_shared.contracts import AgentMetadata, LineageEvent
from navigraph_shared.telemetry import get_tracer, record_agent_invocation
from opentelemetry.trace import Tracer

from navigraph_agents.query.sql_optimization.contracts import (
    GeneratedSql,
    OptimizedSql,
    SqlOptimizationInput,
    SqlOptimizationOutput,
    SqlOptimizationPayload,
    SqlOptimizationResult,
)

AGENT_NAME = "query.sql_optimization"

# Default hard cap injected as a `LIMIT` clause when a generated statement
# doesn't already have one. A real, deliberately conservative default --
# not a placeholder -- chosen to keep an unbounded/forgotten-filter query
# from ever pulling back an unbounded result set. The Execution Planning
# agent re-verifies (and re-caps) whatever `LIMIT` ends up in the SQL
# rather than trusting this value blindly.
DEFAULT_LIMIT = 10_000

# Threshold (in estimated rows) above which an unfiltered scan of a
# referenced table earns an advisory warning. Advisory only -- never
# blocks a statement from being optimized and returned.
LARGE_TABLE_ROW_THRESHOLD = 1_000_000

_TRAILING_LIMIT_RE = re.compile(r"\bLIMIT\s+\d+\s*;?\s*$", re.IGNORECASE)
_LIMIT_VALUE_RE = re.compile(r"\bLIMIT\s+(\d+)\b", re.IGNORECASE)
_WHERE_RE = re.compile(r"\bWHERE\b", re.IGNORECASE)
_BOUNDARY_RE = re.compile(
    r"\b(GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|UNION|QUALIFY)\b", re.IGNORECASE
)
_AND_RE = re.compile(r"\bAND\b", re.IGNORECASE)
_OR_RE = re.compile(r"\bOR\b", re.IGNORECASE)
_LIKE_RE = re.compile(r"\bLIKE\b", re.IGNORECASE)
_IN_RE = re.compile(r"\bIN\s*\(", re.IGNORECASE)
_BETWEEN_RE = re.compile(r"\bBETWEEN\b", re.IGNORECASE)
_NEQ_RE = re.compile(r"!=|<>")
_COMPARISON_RE = re.compile(r"[<>]=?")


def _mask_strings_and_comments(sql: str) -> str:
    """Return a same-length copy of `sql` with the full span of every
    single-quoted string literal (`'...'`, with `''` as an escaped quote)
    and SQL comment (`--` line comments, `/* */` block comments) replaced
    by spaces.

    Every other character keeps its original offset, so callers can locate
    a keyword/operator/semicolon in the masked copy and use that same
    index into the original `sql` -- this is what lets `_find_where_span`,
    `_split_top_level_and`, `_has_trailing_limit`, etc. below never
    mistake a keyword- or operator-shaped substring that merely *appears*
    inside a string literal or a comment for a real one.
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


def _has_trailing_limit(sql: str) -> bool:
    """True if `sql` already ends with a `LIMIT <n>` clause (ignoring a
    trailing semicolon/whitespace)."""

    masked = _mask_strings_and_comments(sql)
    return _TRAILING_LIMIT_RE.search(masked) is not None


def _inject_limit(sql: str) -> tuple[str, bool]:
    """Append `LIMIT {DEFAULT_LIMIT}` to `sql` unless it already ends with
    a `LIMIT` clause. Returns `(possibly_rewritten_sql, applied)`."""

    if _has_trailing_limit(sql):
        return sql, False

    trimmed = sql.rstrip()
    if trimmed.endswith(";"):
        trimmed = trimmed[:-1].rstrip()
    return f"{trimmed}\nLIMIT {DEFAULT_LIMIT}", True


def _find_where_span(sql: str) -> tuple[int, int] | None:
    """Return `(predicate_start, predicate_end)` indices into `sql`
    covering the WHERE clause's predicate text -- i.e. everything after
    the first top-level `WHERE` keyword up to (but not including) the
    next top-level clause keyword (`GROUP BY`/`ORDER BY`/`HAVING`/
    `LIMIT`/`UNION`/`QUALIFY`) or the end of the string. Returns `None` if
    there is no `WHERE` keyword at all.

    Operates on a comment/string-masked copy of `sql` (see
    `_mask_strings_and_comments`) so a WHERE-shaped substring inside a
    comment or string literal can never be mistaken for a real clause.
    Only the first match is considered: SQL Generation's output is a
    single simple `SELECT` (see the module docstring's scope note), not a
    nested query with its own outer-scope WHERE, so this deliberately
    does not attempt paren-depth-aware "top-level only" WHERE detection.
    """

    masked = _mask_strings_and_comments(sql)
    where_match = _WHERE_RE.search(masked)
    if where_match is None:
        return None

    predicate_start = where_match.end()
    boundary_match = _BOUNDARY_RE.search(masked, predicate_start)
    predicate_end = boundary_match.start() if boundary_match else len(sql)
    return predicate_start, predicate_end


def _split_top_level_and(predicate: str) -> list[str] | None:
    """Split `predicate` into its top-level (paren-depth 0) `AND`-joined
    conjuncts.

    Returns `None` -- meaning "do not attempt to reorder this clause at
    all" -- if a top-level `OR` is present. Reordering purely AND-joined
    predicates is always safe (AND is commutative/associative), but this
    agent does not attempt to reason about operator-precedence
    interactions between AND and OR, so any top-level OR opts the whole
    clause out of the reorder rule rather than risk changing what the
    query means.
    """

    masked = _mask_strings_and_comments(predicate)
    depth = 0
    and_positions: list[int] = []
    i = 0
    n = len(masked)
    while i < n:
        ch = masked[i]
        if ch == "(":
            depth += 1
            i += 1
            continue
        if ch == ")":
            depth -= 1
            i += 1
            continue
        if depth == 0:
            if _OR_RE.match(masked, i):
                return None
            and_match = _AND_RE.match(masked, i)
            if and_match:
                and_positions.append(i)
                i = and_match.end()
                continue
        i += 1

    parts: list[str] = []
    start = 0
    for pos in and_positions:
        parts.append(predicate[start:pos])
        start = pos + 3  # len("AND")
    parts.append(predicate[start:])
    return [p.strip() for p in parts]


def _is_pure_equality(predicate: str) -> bool:
    """A predicate counts as "pure equality" if it contains a bare `=`
    comparison and none of `LIKE`/`IN (...)`/`BETWEEN`/`!=`/`<>`/`<`/`>`/
    `<=`/`>=` -- i.e. it is not a range, membership, or pattern-match
    predicate."""

    masked = _mask_strings_and_comments(predicate)
    if _LIKE_RE.search(masked) or _IN_RE.search(masked) or _BETWEEN_RE.search(masked):
        return False
    if _NEQ_RE.search(masked):
        return False
    if _COMPARISON_RE.search(masked):
        return False
    return "=" in masked


def _reorder_where_predicates(sql: str) -> tuple[str, bool]:
    """Move pure-equality top-level WHERE predicates before
    range/LIKE/IN/other predicates, preserving relative order within each
    group. Returns `(possibly_rewritten_sql, applied)` -- `applied` is
    `False` whenever this is a no-op: no WHERE clause, fewer than two
    top-level predicates, a top-level OR present, all predicates already
    the same kind, or the equality-first order happens to match the
    original order already.
    """

    span = _find_where_span(sql)
    if span is None:
        return sql, False

    start, end = span
    predicate_text = sql[start:end]
    conjuncts = _split_top_level_and(predicate_text)
    if conjuncts is None or len(conjuncts) < 2:
        return sql, False

    equality = [c for c in conjuncts if _is_pure_equality(c)]
    other = [c for c in conjuncts if not _is_pure_equality(c)]
    if not equality or not other:
        return sql, False

    new_order = equality + other
    if new_order == conjuncts:
        return sql, False

    new_predicate_text = " " + " AND ".join(new_order) + " "
    new_sql = sql[:start] + new_predicate_text + sql[end:]
    return new_sql, True


def _prepend_audit_comment(sql: str, *, trace_id: str, tenant_id: str) -> str:
    return f"-- navigraph trace_id={trace_id} tenant_id={tenant_id}\n{sql}"


def _parse_limit_value(sql: str) -> int | None:
    """Return the last `LIMIT <n>` value found in `sql` (the one this
    agent itself injects, or one already present), or `None`."""

    masked = _mask_strings_and_comments(sql)
    matches = list(_LIMIT_VALUE_RE.finditer(masked))
    if not matches:
        return None
    return int(matches[-1].group(1))


def _estimate_row_count(
    generated: GeneratedSql,
    table_row_count_estimates: dict[str, int | None],
    limit_value: int | None,
) -> int | None:
    """Best-effort estimated result-row count: the largest known row-count
    estimate among the statement's `referenced_tables` (tables absent from
    `table_row_count_estimates`, or present with a `None` estimate, are
    ignored), capped at the statement's own `LIMIT` value if one is
    present. Returns `None` when no referenced table has a usable
    estimate at all -- this is advisory metadata, not a guarantee."""

    candidates: list[int] = []
    for table in generated.referenced_tables:
        table_estimate = table_row_count_estimates.get(table)
        if table_estimate is not None:
            candidates.append(table_estimate)
    if not candidates:
        return None

    estimate = max(candidates)
    if limit_value is not None:
        estimate = min(estimate, limit_value)
    return estimate


def _row_count_warnings(
    generated: GeneratedSql,
    table_row_count_estimates: dict[str, int | None],
    *,
    has_where: bool,
) -> list[str]:
    """Advisory warnings for every referenced table whose row-count
    estimate exceeds `LARGE_TABLE_ROW_THRESHOLD`, when the statement has
    no WHERE clause at all. Never blocks the statement."""

    if has_where:
        return []

    warnings: list[str] = []
    for table in generated.referenced_tables:
        estimate = table_row_count_estimates.get(table)
        if estimate is not None and estimate > LARGE_TABLE_ROW_THRESHOLD:
            warnings.append(
                f"data_source_id={generated.data_source_id}: table '{table}' has an "
                f"estimated {estimate} rows and this statement has no WHERE clause -- "
                "consider adding a filter to avoid a full table scan."
            )
    return warnings


class SqlOptimizationAgent:
    """Applies deterministic, safe SQL rewrites and advisory warnings.
    Pure function of its input -- no external client dependency."""

    def __init__(self, tracer: Tracer | None = None) -> None:
        self._tracer = tracer or get_tracer("navigraph-agent-runtime")

    async def run(self, input: SqlOptimizationInput) -> SqlOptimizationOutput:
        start = time.perf_counter()
        request_context = input.request_context
        payload = input.payload

        with self._tracer.start_as_current_span("agent.sql_optimization.run") as span:
            span.set_attribute("navigraph.tenant_id", request_context.tenant_id)
            span.set_attribute("navigraph.trace_id", request_context.trace_id)
            span.set_attribute("navigraph.agent_name", AGENT_NAME)

            optimized_statements: list[OptimizedSql] = []
            warnings: list[str] = []

            for generated in payload.statements:
                optimized, statement_warnings = self._optimize_statement(generated, payload)
                optimized_statements.append(optimized)
                warnings.extend(statement_warnings)

            result = SqlOptimizationResult(statements=optimized_statements, warnings=warnings)

            # Deterministic and dependency-free: there is no failure mode
            # for this agent to reflect in a lower confidence (warnings
            # are advisory, not a sign the optimization itself is
            # uncertain), so confidence is always 1.0.
            confidence = 1.0

            lineage_event = LineageEvent(
                agent_name=AGENT_NAME,
                input_summary=f"statements={len(payload.statements)}",
                output_summary=(
                    f"statements={len(optimized_statements)} warnings={len(warnings)}"
                ),
                tenant_id=request_context.tenant_id,
                trace_id=request_context.trace_id,
            )

            latency_ms = (time.perf_counter() - start) * 1000.0
            metadata = AgentMetadata(latency_ms=latency_ms)

            span.set_attribute("navigraph.warnings_count", len(warnings))

        record_agent_invocation(AGENT_NAME, latency_ms=latency_ms, success=True)

        return SqlOptimizationOutput(
            result=result,
            confidence=confidence,
            lineage_events=[lineage_event],
            errors=[],
            metadata=metadata,
        )

    @staticmethod
    def _optimize_statement(
        generated: GeneratedSql,
        payload: SqlOptimizationPayload,
    ) -> tuple[OptimizedSql, list[str]]:
        applied_rules: list[str] = []

        reordered_sql, reorder_applied = _reorder_where_predicates(generated.sql)
        if reorder_applied:
            applied_rules.append("reorder_predicates")

        limited_sql, limit_applied = _inject_limit(reordered_sql)
        if limit_applied:
            applied_rules.append("inject_limit")

        final_sql = _prepend_audit_comment(
            limited_sql, trace_id=payload.trace_id, tenant_id=payload.tenant_id
        )
        applied_rules.append("audit_comment")

        limit_value = _parse_limit_value(limited_sql)
        estimated_row_count = _estimate_row_count(
            generated, payload.table_row_count_estimates, limit_value
        )

        has_where = _find_where_span(generated.sql) is not None
        statement_warnings = _row_count_warnings(
            generated, payload.table_row_count_estimates, has_where=has_where
        )

        optimized = OptimizedSql(
            data_source_id=generated.data_source_id,
            sql=final_sql,
            params=generated.params,
            applied_rules=applied_rules,
            estimated_row_count=estimated_row_count,
        )
        return optimized, statement_warnings
