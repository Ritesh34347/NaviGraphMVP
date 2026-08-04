"""SQL Generation agent implementation.

Follows the exact structural pattern established by
`navigraph_agents.understanding.intent_understanding.agent.IntentUnderstandingAgent`
and, more closely,
`navigraph_agents.understanding.semantic_retrieval.agent.SemanticRetrievalAgent`:
an LLM call is made ONLY when it is actually needed (short-circuiting
entirely otherwise, exactly like Semantic Retrieval's empty-`unresolved_terms`
short-circuit), the LLM is constrained to a closed candidate list rather than
free text, and every LLM-returned value is validated against that list before
being trusted -- an invented column name is never silently used.

Two things this agent does that its siblings don't:

1. A deterministic SQL-skeleton builder (`_build_sql`) that never touches the
   LLM at all -- it assembles SELECT/FROM/JOIN/GROUP BY purely from
   `schema_mapping`'s already-resolved tables, columns, and joins.
2. Real bind-parameterization: every literal value a predicate resolves to
   is placed in `GeneratedSql.params` and referenced from `GeneratedSql.sql`
   via a `%(name)s` placeholder -- **never** string-interpolated into the SQL
   text. `%(name)s` (pyformat) is not a generic choice: it is exactly the
   paramstyle `navigraph_connectors.snowflake.connector.SnowflakeConnector
   .execute_query` passes through to `cursor.execute(sql, params)` (a plain
   `dict[str, Any]` handed straight to the `snowflake-connector-python`
   driver, whose default paramstyle is `pyformat`) -- so SQL produced here is
   directly executable via that connector with no dialect translation.
   Table/column identifiers, by contrast, are catalog-validated identifiers
   from a prior agent (not user input), so they are safe to embed directly
   as SQL text -- but literal *values* (predicate values, never identifiers)
   must never take that path.
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

from navigraph_agents.query.sql_generation.contracts import (
    GeneratedSql,
    IntentLabel,
    JoinSpec,
    PredicateResolution,
    ResolvedColumnRef,
    SqlGenerationInput,
    SqlGenerationOutput,
    SqlGenerationPayload,
    SqlGenerationResult,
)

AGENT_NAME = "query.sql_generation"
PROMPT_VERSION = "v1"

_PROMPT_PATH = Path(__file__).parent / "prompts" / "predicate_resolution.md"

# Data types treated as numeric for aggregation purposes -- the same real,
# deliberately narrow set `schema_mapping.agent` uses for its own
# `_assign_role` numeric check (Snowflake/Postgres-catalog-style type
# names), kept identical here rather than re-invented so a column
# schema_mapping already assigned `role="measure"` to is judged numeric by
# this agent the same way it was judged numeric upstream.
_NUMERIC_DATA_TYPES = {"NUMBER", "FLOAT", "INTEGER", "DECIMAL", "NUMERIC", "DOUBLE"}

# Intents for which a numeric measure column is aggregated with SUM --
# mirrors `schema_mapping.agent._MEASURE_INTENTS` exactly, since those are
# the only intents schema_mapping itself ever assigns `role="measure"` under.
_MEASURE_INTENTS = {"metric_lookup", "trend_analysis", "comparison"}

# Case-insensitive substring/word triggers for "this question likely
# contains a relative-date phrase or an explicit range/comparison that
# schema_mapping's resolved columns don't already pin down." Deliberately a
# small, documented heuristic, not an attempt at exhaustive NLP -- see
# `_needs_predicate_resolution`'s docstring for the full rule and its
# rationale.
_TEMPORAL_TRIGGER_PHRASES = (
    "last ",
    "this ",
    "previous ",
    "quarter",
    "month",
    "year",
    "since ",
    "between ",
    "compared to",
    " vs ",
    " vs.",
    "versus",
    "ago",
    "recent",
    "current ",
)

_VALID_OPERATORS = {"=", "!=", ">", ">=", "<", "<=", "IN", "BETWEEN", "LIKE"}

# REAL BUG, found live against a real model (LIMITATIONS.md item 38):
# `_aggregation_function`'s SUM/COUNT choice, keyed only on a resolved
# measure column's data type and intent, has no way to express "count the
# rows," which "how many X"-shaped questions actually need -- for real,
# live gq_002 ("How many transactions has each customer made?"), this
# produced a real SUM over whatever numeric column got resolved as
# `role="measure"`, yielding a nonsensical "1,229,737,256 transactions"
# per customer. A `COUNT(*)` trigger phrase check, mirroring
# `_needs_predicate_resolution`'s existing small-heuristic style, fixes
# this at the one place it can be fixed correctly regardless of which
# (if any) measure column upstream resolved.
_COUNT_QUESTION_TRIGGER_PHRASES = (
    "how many",
    "number of",
    "count of",
)


def _is_count_question(question: str) -> bool:
    """A "how many X" / "number of X" / "count of X" question is asking
    for a row COUNT, never a SUM over some resolved numeric column --
    regardless of whatever `role="measure"` column schema_mapping (or an
    upstream Semantic Retrieval mis-resolution) may have attached. This
    intentionally overrides `_aggregation_function` entirely rather than
    only supplementing it: a "how many" question summing a resolved
    numeric field would be just as wrong as summing the wrong one.
    Deliberately a small, documented phrase-trigger heuristic, not an
    attempt at exhaustive NLP -- same honesty as `_needs_predicate_resolution`.
    """

    lowered = question.lower()
    return any(phrase in lowered for phrase in _COUNT_QUESTION_TRIGGER_PHRASES)


def _load_system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _normalize_label(text: str) -> str:
    """Strip everything but letters/digits and lowercase -- mirrors
    `understanding.ontology.agent._normalize_label` exactly, for the same
    reason: comparing free-text phrasing against a canonical identifier
    needs to ignore spaces/punctuation/case, not just casing."""

    return "".join(ch for ch in text.lower() if ch.isalnum())


def _resolved_via_named_value(column: ResolvedColumnRef) -> bool:
    """True when a resolved dimension column's `.term` (the free-text
    phrase that resolved it) does NOT textually correspond to the column's
    own name -- i.e. the question named a SPECIFIC VALUE of that column
    (e.g. "Mobile App", "Gold", "Athens Exchange") rather than the
    dimension itself (e.g. "channel", "loyalty tier", "market").

    REAL BUG, found live: "How much revenue came from the Mobile App?"
    resolved "Mobile App" to `DIM_CHANNEL.CHANNEL_NAME` via Semantic
    Retrieval -- a completely correct column match -- but Schema Mapping
    then treats that resolution exactly like a plain "channel" reference:
    a `role="dimension"` column destined for `GROUP BY`, with no signal
    anywhere that the term itself already names one specific value. SQL
    Generation's predicate-resolution LLM call (`_needs_predicate_resolution`)
    only ever fired on relative-date/comparison trigger words, so it was
    never even asked whether "Mobile App" needed a `WHERE` filter --
    confirmed live: a direct Semantic Retrieval call for this exact term
    correctly returns `CHANNEL_NAME`, proving the resolution itself was
    never the problem, only the missing trigger to reconsider it as a
    filter.

    This uses `column.term` -- already a real, existing field on every
    resolved column, carrying the original phrase verbatim -- compared
    against `column.column_name` via the same normalize-and-substring
    heuristic `understanding.ontology.agent._label_matches_entities` already
    uses for the identical class of judgment call (free-text phrasing vs.
    a canonical identifier). "channel" vs `CHANNEL_NAME` normalizes to
    "channel"/"channelname" -- a real substring match, so a plain
    dimension reference correctly does NOT trigger this. "Mobile App" vs
    `CHANNEL_NAME` normalizes to "mobileapp"/"channelname" -- no overlap,
    so this correctly flags it. Deliberately favors false positives (e.g.
    an irregular plural like "categories" vs `CATEGORY` won't
    substring-match either, triggering an unnecessary but harmless LLM
    call that itself correctly returns no predicates) over false negatives
    -- a missed named-value filter produces a silently wrong, misleading
    answer (exactly what happened live), while an extra LLM call is only
    a cost, never a correctness risk.
    """

    if column.role == "measure":
        return False
    term_norm = _normalize_label(column.term)
    column_norm = _normalize_label(column.column_name)
    if not term_norm or not column_norm:
        return False
    return term_norm not in column_norm and column_norm not in term_norm


def _needs_predicate_resolution(question: str, columns: list[ResolvedColumnRef]) -> bool:
    """Decide whether the predicate-resolution LLM call is needed at all.

    Heuristic (documented here since "does this question have an unresolved
    relative-date/qualitative filter" is inherently judgement-driven, not a
    hard rule): fires on EITHER of two signals --

    1. The question contains a fixed set of trigger words/phrases commonly
       associated with a relative-date filter ("last quarter", "since
       March", "this year") or an explicit range/comparison ("between X
       and Y", "compared to", "vs") -- `_TEMPORAL_TRIGGER_PHRASES`.
    2. Any resolved dimension column was reached via a term that names a
       specific VALUE of that column rather than the column itself (e.g.
       "Mobile App" resolving to `CHANNEL_NAME`) -- see
       `_resolved_via_named_value`'s own docstring for the real live bug
       this closes.

    If neither signal fires, there is nothing an LLM could resolve, so the
    call is skipped entirely -- this is the common case (e.g. "What is the
    total transaction volume by market?" has neither, so no LLM call is
    made for it). If a signal DOES fire but Schema Mapping already
    resolved a `role="filter"` column for this request, the phrase's value
    is presumed already pinned down elsewhere in the upstream pipeline, so
    the call is skipped there too. This is a real, reasoned heuristic, not
    a claim of perfect recall or precision -- it can both miss a genuine
    filter phrased unusually and fire on a false positive; both are
    acceptable given a missed trigger just means the predicate stays
    unresolved (rather than producing wrong SQL) and an extra trigger costs
    only one LLM call that itself correctly recognizes when nothing needs
    resolving.
    """

    lowered = question.lower()
    has_temporal_trigger = any(phrase in lowered for phrase in _TEMPORAL_TRIGGER_PHRASES)
    has_named_value_trigger = any(_resolved_via_named_value(c) for c in columns)
    if not has_temporal_trigger and not has_named_value_trigger:
        return False

    already_has_filter_column = any(c.role == "filter" for c in columns)
    return not already_has_filter_column


def _is_identifier_column(column: ResolvedColumnRef) -> bool:
    """A column whose name ends in "ID" (`CUSTOMERID`, `TRANSACTIONID`,
    `MARKETID`, `EXCHANGEID` -- the real, consistent naming convention
    across this schema's actual tables) is a surrogate/natural key, never
    a genuine additive measure. Summing an identifier is always
    semantically wrong, independent of how the question is phrased --
    unlike the `_is_count_question` phrase-trigger (item 38/73), which
    only catches "how many"/"number of"/"count of"-shaped questions.

    REAL BUG, found live (LIMITATIONS.md item 80): "How does the
    transaction count and value on 2018-01-02 compare..." doesn't trip
    `_is_count_question` (no trigger phrase present), so Semantic
    Retrieval's real, reasonable match of "transaction count" to
    `TRANSACTIONID` fell through to this function, which summed it --
    producing a nonsensical "transaction count total of
    3,063,258,983,525." This check fixes the general case:
    `TRANSACTIONID` (or any other real ID-shaped column) is never a valid
    `SUM` target, regardless of phrasing or intent.
    """

    return column.column_name.upper().endswith("ID")


def _aggregation_function(column: ResolvedColumnRef, intent: IntentLabel) -> str:
    """Choose the SQL aggregate function for a `role="measure"` column.

    v1 rule (deliberately narrow, mirrors `schema_mapping.agent._assign_role`'s
    documented narrowness): an identifier column (`_is_identifier_column`)
    is never summed, regardless of intent -- `COUNT` is always the correct,
    meaningful aggregate over a key column. Otherwise, a numeric `data_type`
    combined with an intent that actually asks for an aggregated quantity
    (`_MEASURE_INTENTS`) uses `SUM` -- correct for the additive metrics this
    system's worked examples actually exercise (transaction volume, unit
    counts, revenue). A column that upstream nonetheless labeled
    `role="measure"` without a numeric `data_type` (which, per
    schema_mapping's own role-assignment invariant, should never happen --
    but this agent does not blindly trust that invariant since it receives
    schema_mapping's output as plain data, not a guarantee) falls back to
    `COUNT`, since `SUM` over non-numeric data is invalid SQL.
    """

    if _is_identifier_column(column):
        return "COUNT"
    if column.data_type.upper() in _NUMERIC_DATA_TYPES and intent in _MEASURE_INTENTS:
        return "SUM"
    return "COUNT"


def _qualified_table(table_name: str, schema_by_table: dict[str, str]) -> str:
    schema_name = schema_by_table.get(table_name)
    return f"{schema_name}.{table_name}" if schema_name else table_name


def _qualified_col(column: ResolvedColumnRef) -> str:
    return f"{column.table_name}.{column.column_name}"


def _build_from_clause(
    tables: list[str],
    joins: list[JoinSpec],
    schema_by_table: dict[str, str],
) -> tuple[str, set[str]]:
    """Build the `FROM ... [JOIN ... ON ...]` clause.

    Single-table case: a plain `FROM SCHEMA.TABLE`. Multi-table case: starts
    from the first join's `right_table` (schema_mapping's join-derivation
    always anchors joins on a `realizing_table`, which is exactly what
    `right_table` is -- see `schema_mapping.agent._build_joins`'s
    docstring), then walks the join list breadth-first, adding one `JOIN`
    per edge that connects a not-yet-joined table to the growing joined set
    (works for any real join graph the input describes, not just the exact
    two-table case this phase's worked examples exercise).

    Returns `(from_clause_sql, unjoined_tables)`. `unjoined_tables` is the
    set of resolved tables that could NOT be connected via the provided
    joins.

    REAL BUG, found live (a real "total transaction volume by market"
    question): this function used to silently append any unreachable
    table via a plain comma-join -- `FROM A, B` with no `ON` condition at
    all, i.e. a genuine Cartesian product. `schema_mapping.agent._build_joins`
    only derives a join when the Ontology agent's knowledge-graph lookup
    resolved a real `RelationshipConcept` connecting the two tables
    (`STAGING_TRANSACTIONS`/`STAGING_MARKETS` has no such curated concept
    yet), so `tables=[STAGING_TRANSACTIONS, STAGING_MARKETS]` with
    `joins=[]` is a real, live-reproduced case, not a hypothetical one --
    confirmed live: `SELECT STAGING_MARKETS.NAME, SUM(...) GROUP BY
    STAGING_MARKETS.NAME FROM STAGING.STAGING_TRANSACTIONS,
    STAGING.STAGING_MARKETS` returned the SAME grand-total sum for every
    market, since every transaction row was paired with every market row
    before the aggregate ran. This function no longer silently emits that
    SQL -- it now reports which tables couldn't be joined, and
    `_generate_statements` turns that into a real, non-recoverable
    `AgentError` instead of a statement that looks like a real per-market
    breakdown but isn't.
    """

    if not tables:
        return "", set()

    if len(tables) == 1:
        return "FROM " + _qualified_table(tables[0], schema_by_table), set()

    if not joins:
        return (
            "FROM " + ", ".join(_qualified_table(t, schema_by_table) for t in tables),
            set(tables),
        )

    joined_set = {joins[0].right_table}
    from_lines = [f"FROM {_qualified_table(joins[0].right_table, schema_by_table)}"]
    remaining = list(joins)

    progressed = True
    while progressed:
        progressed = False
        for join in list(remaining):
            if join.right_table in joined_set and join.left_table not in joined_set:
                from_lines.append(
                    f"JOIN {_qualified_table(join.left_table, schema_by_table)} "
                    f"ON {join.left_table}.{join.left_column} = "
                    f"{join.right_table}.{join.right_column}"
                )
                joined_set.add(join.left_table)
                remaining.remove(join)
                progressed = True
            elif join.left_table in joined_set and join.right_table not in joined_set:
                from_lines.append(
                    f"JOIN {_qualified_table(join.right_table, schema_by_table)} "
                    f"ON {join.right_table}.{join.right_column} = "
                    f"{join.left_table}.{join.left_column}"
                )
                joined_set.add(join.right_table)
                remaining.remove(join)
                progressed = True

    unjoined = {table for table in tables if table not in joined_set}
    return "\n".join(from_lines), unjoined


def _build_where_clause(
    predicates: list[PredicateResolution],
    columns_by_name: dict[str, ResolvedColumnRef],
) -> tuple[str, dict[str, Any]]:
    """Build the `WHERE ...` clause and its bind parameters.

    Every literal value lives in the returned `params` dict, referenced from
    the SQL text only via a `%(name)s` placeholder -- see this module's
    docstring for why `%(name)s` (pyformat) specifically. `BETWEEN` binds
    two placeholders; `IN` binds one placeholder per value (a DBAPI
    `%(name)s` placeholder cannot itself expand a list, so each element
    needs its own bound parameter); every other operator binds one.
    """

    if not predicates:
        return "", {}

    clauses: list[str] = []
    params: dict[str, Any] = {}

    for idx, predicate in enumerate(predicates):
        column = columns_by_name[predicate.column]
        qualified = _qualified_col(column)

        if predicate.operator == "BETWEEN":
            values = predicate.resolved_value
            assert isinstance(values, list) and len(values) == 2  # validated at parse time
            start_name, end_name = f"predicate_{idx}_start", f"predicate_{idx}_end"
            params[start_name] = values[0]
            params[end_name] = values[1]
            clauses.append(f"{qualified} BETWEEN %({start_name})s AND %({end_name})s")

        elif predicate.operator == "IN":
            values = predicate.resolved_value
            assert isinstance(values, list)  # validated at parse time
            names = []
            for v_idx, item in enumerate(values):
                name = f"predicate_{idx}_{v_idx}"
                params[name] = item
                names.append(f"%({name})s")
            clauses.append(f"{qualified} IN ({', '.join(names)})")

        else:
            value = predicate.resolved_value
            assert isinstance(value, str)  # validated at parse time
            name = f"predicate_{idx}"
            params[name] = value
            clauses.append(f"{qualified} {predicate.operator} %({name})s")

    return "WHERE " + " AND ".join(clauses), params


def _format_candidate_columns(columns: list[ResolvedColumnRef]) -> str:
    return json.dumps(
        [
            {
                "column": c.column_name,
                "table": c.table_name,
                "data_type": c.data_type,
                "role": c.role,
            }
            for c in columns
        ],
        indent=2,
    )


class SqlGenerationAgent:
    """Builds a deterministic, schema-grounded SQL skeleton from a Schema
    Mapping result, calling an LLM only when a relative-date or qualitative
    filter phrase needs to be resolved to a literal, bind-parameterized
    value."""

    def __init__(self, llm_client: LLMClient, tracer: Tracer | None = None) -> None:
        self._llm_client = llm_client
        self._tracer = tracer or get_tracer("navigraph-agent-runtime")
        self._system_prompt = _load_system_prompt()

    async def run(self, input: SqlGenerationInput) -> SqlGenerationOutput:
        start = time.perf_counter()
        request_context = input.request_context
        payload = input.payload
        question = payload.original_question
        columns = payload.schema_mapping.columns

        errors: list[AgentError] = []
        llm_response: LLMResponse | None = None
        predicate_resolutions: list[PredicateResolution] = []
        unresolved_predicates: list[str] = []

        with self._tracer.start_as_current_span("agent.sql_generation.run") as span:
            span.set_attribute("navigraph.tenant_id", request_context.tenant_id)
            span.set_attribute("navigraph.trace_id", request_context.trace_id)
            span.set_attribute("navigraph.agent_name", AGENT_NAME)
            span.set_attribute("navigraph.table_count", len(payload.schema_mapping.tables))

            needs_llm = _needs_predicate_resolution(question, columns)
            span.set_attribute("navigraph.predicate_resolution_needed", needs_llm)

            if not needs_llm:
                # Nothing to resolve -- skip the LLM call entirely, exactly
                # like SemanticRetrievalAgent's empty-`unresolved_terms`
                # short-circuit.
                model_version = None
                prompt_version = None
                tokens_input = None
                tokens_output = None
            else:
                user_message = (
                    f'Question: "{question}"\n\n'
                    f"Resolved columns (the ONLY valid targets for `column`):\n"
                    f"{_format_candidate_columns(columns)}"
                )

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

                predicate_resolutions, unresolved_predicates = self._parse_llm_response(
                    llm_response, columns, errors
                )

                model_version = llm_response.model if llm_response else None
                prompt_version = PROMPT_VERSION
                tokens_input = llm_response.tokens_input if llm_response else None
                tokens_output = llm_response.tokens_output if llm_response else None

            statements, generation_errors = self._generate_statements(
                payload, columns, predicate_resolutions
            )
            errors.extend(generation_errors)

            result = SqlGenerationResult(
                statements=statements,
                predicate_resolutions=predicate_resolutions,
                unresolved_predicates=unresolved_predicates,
            )

            confidence = 1.0
            if errors:
                confidence = 0.0 if any(not e.recoverable for e in errors) else 0.5

            lineage_event = LineageEvent(
                agent_name=AGENT_NAME,
                input_summary=(
                    f"question={question!r} intent={payload.intent} "
                    f"tables={payload.schema_mapping.tables} "
                    f"columns={len(columns)} predicate_llm_call={needs_llm}"
                ),
                output_summary=(
                    f"statements={len(statements)} "
                    f"predicate_resolutions={len(predicate_resolutions)} "
                    f"unresolved_predicates={unresolved_predicates}"
                ),
                tenant_id=request_context.tenant_id,
                trace_id=request_context.trace_id,
            )

            latency_ms = (time.perf_counter() - start) * 1000.0

            metadata = AgentMetadata(
                latency_ms=latency_ms,
                model_version=model_version,
                prompt_version=prompt_version,
                tokens_input=tokens_input,
                tokens_output=tokens_output,
            )

            span.set_attribute("navigraph.statement_count", len(statements))

        record_agent_invocation(AGENT_NAME, latency_ms=latency_ms, success=not errors)
        for error in errors:
            record_agent_error(AGENT_NAME, error_code=error.code, recoverable=error.recoverable)

        return SqlGenerationOutput(
            result=result,
            confidence=confidence,
            lineage_events=[lineage_event],
            errors=errors,
            metadata=metadata,
        )

    @staticmethod
    def _generate_statements(
        payload: SqlGenerationPayload,
        columns: list[ResolvedColumnRef],
        predicate_resolutions: list[PredicateResolution],
    ) -> tuple[list[GeneratedSql], list[AgentError]]:
        """Assemble the deterministic SQL skeleton (step 2) and bind the
        resolved predicates (step 5) into one `GeneratedSql` per resolved
        data source.

        Real, working single-source case: every resolved table maps to the
        same `data_source_id` -- the common, actually-exercisable case per
        the spec. Two non-crashing failure paths are handled explicitly
        rather than producing wrong or partial SQL: no resolved (or no
        reachable) data source at all for a needed table, and a genuine
        cross-source query (resolved tables split across more than one
        distinct `data_source_id`) -- generating one correct SQL statement
        that spans two different physical data sources isn't meaningful (a
        single connector can't execute a join across two separate
        connections), so this is reported as a real, non-recoverable
        `AgentError` instead of silently emitting a join that could never
        actually run.
        """

        errors: list[AgentError] = []
        tables = payload.schema_mapping.tables

        data_source_by_table = {ds.table_name: ds for ds in payload.resolved_data_sources}

        relevant_ids: set[str] = set()
        for table in tables:
            ds = data_source_by_table.get(table)
            if ds is None:
                errors.append(
                    AgentError(
                        code="no_resolved_data_source",
                        message=f"No resolved data source for table {table!r}",
                        recoverable=False,
                    )
                )
                continue
            if not ds.reachable:
                errors.append(
                    AgentError(
                        code="data_source_unreachable",
                        message=(
                            f"Resolved data source {ds.data_source_id!r} for table "
                            f"{table!r} is not reachable"
                        ),
                        recoverable=False,
                    )
                )
                continue
            relevant_ids.add(ds.data_source_id)

        if not relevant_ids:
            return [], errors

        if len(relevant_ids) > 1:
            errors.append(
                AgentError(
                    code="cross_source_query_not_supported",
                    message=(
                        f"Resolved tables span multiple physical data sources "
                        f"({sorted(relevant_ids)}); generating a single SQL statement "
                        f"across more than one data source is not supported yet"
                    ),
                    recoverable=False,
                )
            )
            return [], errors

        data_source_id = next(iter(relevant_ids))

        measure_columns = [c for c in columns if c.role == "measure"]
        dimension_columns = [c for c in columns if c.role != "measure"]

        schema_by_table: dict[str, str] = {}
        for column in columns:
            schema_by_table.setdefault(column.table_name, column.schema_name)

        from_clause, unjoined_tables = _build_from_clause(
            tables, payload.schema_mapping.joins, schema_by_table
        )

        if unjoined_tables:
            errors.append(
                AgentError(
                    code="unjoined_table_in_multi_table_query",
                    message=(
                        f"Table(s) {sorted(unjoined_tables)} could not be connected to the "
                        f"other resolved table(s) via any provided join; refusing to emit a "
                        f"comma-join (Cartesian product), which would silently repeat the "
                        f"same aggregate for every group instead of a real per-group "
                        f"breakdown"
                    ),
                    recoverable=False,
                )
            )
            return [], errors

        select_parts = [_qualified_col(c) for c in dimension_columns]
        is_count_question = _is_count_question(payload.original_question)
        if is_count_question:
            # A "how many"/"number of"/"count of" question always means
            # COUNT(*) -- never a SUM over whatever measure column upstream
            # happened to resolve (see this constant's own docstring and
            # LIMITATIONS.md item 38 for the real, live failure this fixes).
            select_parts.append("COUNT(*) AS RECORD_COUNT")
        else:
            for measure in measure_columns:
                agg = _aggregation_function(measure, payload.intent)
                select_parts.append(f"{agg}({_qualified_col(measure)}) AS {measure.column_name}_TOTAL")

        columns_by_name = {c.column_name: c for c in columns}
        where_clause, params = _build_where_clause(predicate_resolutions, columns_by_name)

        sql_lines = [f"SELECT {', '.join(select_parts)}", from_clause]
        if where_clause:
            sql_lines.append(where_clause)
        has_aggregate = bool(measure_columns) or is_count_question
        if has_aggregate and dimension_columns:
            sql_lines.append(
                f"GROUP BY {', '.join(_qualified_col(c) for c in dimension_columns)}"
            )

        statement = GeneratedSql(
            data_source_id=data_source_id,
            sql="\n".join(sql_lines),
            params=params,
            referenced_tables=sorted(tables),
            referenced_columns=[_qualified_col(c) for c in columns],
        )

        return [statement], errors

    @staticmethod
    def _parse_llm_response(
        llm_response: LLMResponse | None,
        columns: list[ResolvedColumnRef],
        errors: list[AgentError],
    ) -> tuple[list[PredicateResolution], list[str]]:
        """Parse the LLM's JSON response into (predicate_resolutions,
        unresolved_predicates).

        Handles every way the response can be malformed -- not valid JSON,
        a non-object top level, a non-list `predicates`, a malformed
        individual entry -- by recording a recoverable `AgentError` and
        falling back to no resolutions for the affected part, rather than
        raising, exactly like `IntentUnderstandingAgent._parse_llm_response`
        and `SemanticRetrievalAgent._parse_llm_response`.

        Critically -- mirroring `SemanticRetrievalAgent`'s single most
        important behavior -- every returned `column` is checked against the
        caller-supplied candidate list (`columns`, the same closed list the
        prompt was given). A `column` that isn't in that list is a
        hallucination and is never trusted: the predicate is dropped into
        `unresolved_predicates` and a recoverable `AgentError` with code
        `llm_returned_invalid_column` is recorded, not silently used.
        """

        valid_columns = {c.column_name for c in columns}

        if llm_response is None:
            # The LLM call itself already failed; llm_call_failed error was
            # already recorded by the caller.
            return [], []

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
            return [], []

        if not isinstance(data, dict):
            errors.append(
                AgentError(
                    code="llm_response_malformed",
                    message="LLM response JSON was not an object",
                    recoverable=True,
                )
            )
            return [], []

        raw_predicates = data.get("predicates")
        if not isinstance(raw_predicates, list):
            errors.append(
                AgentError(
                    code="llm_response_invalid_predicates",
                    message=f"LLM returned a non-list 'predicates': {raw_predicates!r}",
                    recoverable=True,
                )
            )
            return [], []

        resolved: list[PredicateResolution] = []
        unresolved: list[str] = []

        for entry in raw_predicates:
            if not isinstance(entry, dict):
                errors.append(
                    AgentError(
                        code="llm_response_invalid_predicate_entry",
                        message=f"Predicate entry was not an object: {entry!r}",
                        recoverable=True,
                    )
                )
                continue

            raw_phrase = entry.get("raw_phrase")
            if not isinstance(raw_phrase, str):
                errors.append(
                    AgentError(
                        code="llm_response_invalid_predicate_entry",
                        message=f"Predicate entry missing string 'raw_phrase': {entry!r}",
                        recoverable=True,
                    )
                )
                continue

            rationale = entry.get("rationale")
            if not isinstance(rationale, str):
                rationale = None

            column = entry.get("column")
            if not isinstance(column, str) or column not in valid_columns:
                # Hallucination: the LLM returned a column that is not in
                # the closed candidate list it was given. Never trust it.
                errors.append(
                    AgentError(
                        code="llm_returned_invalid_column",
                        message=(
                            f"LLM resolved phrase {raw_phrase!r} to column {column!r}, "
                            f"which is not one of the resolved candidate columns"
                        ),
                        recoverable=True,
                    )
                )
                unresolved.append(raw_phrase)
                continue

            operator = entry.get("operator")
            if operator not in _VALID_OPERATORS:
                errors.append(
                    AgentError(
                        code="llm_response_invalid_operator",
                        message=(
                            f"LLM returned an unrecognized operator {operator!r} "
                            f"for phrase {raw_phrase!r}"
                        ),
                        recoverable=True,
                    )
                )
                unresolved.append(raw_phrase)
                continue

            value = entry.get("value")

            if operator in ("IN", "BETWEEN"):
                if not isinstance(value, list) or not value or not all(
                    isinstance(v, str) for v in value
                ):
                    errors.append(
                        AgentError(
                            code="llm_response_invalid_predicate_value",
                            message=(
                                f"Operator {operator!r} requires a non-empty list of "
                                f"string values for phrase {raw_phrase!r}, got {value!r}"
                            ),
                            recoverable=True,
                        )
                    )
                    unresolved.append(raw_phrase)
                    continue
                if operator == "BETWEEN" and len(value) != 2:
                    errors.append(
                        AgentError(
                            code="llm_response_invalid_predicate_value",
                            message=(
                                f"BETWEEN requires exactly 2 values for phrase "
                                f"{raw_phrase!r}, got {len(value)}"
                            ),
                            recoverable=True,
                        )
                    )
                    unresolved.append(raw_phrase)
                    continue
                resolved_value: str | list[str] = value
            else:
                if not isinstance(value, str):
                    errors.append(
                        AgentError(
                            code="llm_response_invalid_predicate_value",
                            message=(
                                f"Operator {operator!r} requires a single string value "
                                f"for phrase {raw_phrase!r}, got {value!r}"
                            ),
                            recoverable=True,
                        )
                    )
                    unresolved.append(raw_phrase)
                    continue
                resolved_value = value

            resolved.append(
                PredicateResolution(
                    raw_phrase=raw_phrase,
                    column=column,
                    operator=operator,
                    resolved_value=resolved_value,
                    rationale=rationale,
                )
            )

        return resolved, unresolved
