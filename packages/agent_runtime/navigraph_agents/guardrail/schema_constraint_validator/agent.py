"""Schema Constraint Validator agent implementation.

Fully deterministic: no LLM call, no `prompts/` directory. For every
`GeneratedSql` statement it receives, verifies -- against the REAL,
already-crawled catalog (`navigraph_catalog.api.find_column`) -- that every
column the statement claims to reference actually exists on one of the
tables it claims to reference. This is the first real guardrail gate a
generated statement passes through after SQL Generation, before Policy
Authorization or Execution Planning ever see it.

Session-access design: matches
`navigraph_agents.understanding.metadata_discovery.agent.MetadataDiscoveryAgent`
and
`navigraph_agents.query.data_source_discovery.agent.DataSourceDiscoveryAgent`
exactly -- the constructor takes a `sessionmaker[Session]` ("session
factory"), and `run()` opens exactly one `session_scope` per invocation
(not one per statement); every `navigraph_catalog.api.find_column` call for
every statement happens inside that single `with` block.

TABLE/COLUMN QUALIFICATION (read this before touching the validation logic
below): `GeneratedSql.referenced_columns` entries are `"TABLE.COLUMN"`
qualified strings, not bare column names -- confirmed against the real
`sql_generation.agent._qualified_col` helper that actually produces them
(`f"{column.table_name}.{column.column_name}"`). This agent's first real
version assumed `referenced_columns` was a flat, unqualified list requiring
a cross-product search against `referenced_tables` -- a real bug, caught
live via `tests/integration/guardrail_pipeline/` (every real statement was
rejected as `unknown_column` because `find_column` was asked to look up a
column literally named `"STAGING_TRANSACTIONS.MARKETID"`, which of course
never matches). `_split_qualified_column` below parses the `"TABLE.COLUMN"`
form directly; an entry with no `.` (defensively handled, not expected in
real SQL Generation output today) falls back to trying every entry in
`referenced_tables`, exactly as this agent's original, incorrect-for-the-
common-case logic did.

WHAT THIS DOES AND DOES NOT VERIFY (documented explicitly, not overclaimed):
this only proves that every referenced COLUMN resolves against the table it
names (or, for the unqualified fallback case, against at least one of the
referenced TABLES). It does NOT independently verify that every entry in
`referenced_tables` is itself a real, existing table backed by at least one
column match -- a pathological statement listing a table in
`referenced_tables` with none of its columns appearing in
`referenced_columns` would not be independently caught here. That is a
real, narrow gap, not a silently swallowed one.

Error contract: an unresolvable column is a deliberate, non-recoverable
`AgentError(code="unknown_column", recoverable=False)` -- this project's
guardrails do not let SQL referencing a column that doesn't exist reach
execution. A catalog-lookup exception (e.g. the DB connection drops mid-scan)
is scoped PER STATEMENT here, unlike
`DataSourceDiscoveryAgent._resolve_table_owners`'s single whole-call
`catalog_lookup_failed` error: each statement in this agent's payload is
independent of every other, so one statement's lookup failure must not
discard an otherwise-valid sibling statement's `validated` result.

Follows the same structural pattern as
`navigraph_agents.query.execution_planning.agent`: a statement either
passes through unchanged into `validated`, or becomes an `AgentError` in
`rejected` -- never both, no post-hoc filtering step. Never raises, always
emits a `LineageEvent` and `AgentMetadata` with `latency_ms` populated.
"""

from __future__ import annotations

import time
import uuid

from navigraph_catalog.api import find_column
from navigraph_catalog.db import session_scope
from navigraph_shared.contracts import AgentError, AgentMetadata, LineageEvent
from navigraph_shared.telemetry import (
    get_tracer,
    record_agent_error,
    record_agent_invocation,
)
from opentelemetry.trace import Tracer
from sqlalchemy.orm import Session, sessionmaker

from navigraph_agents.guardrail.schema_constraint_validator.contracts import (
    GeneratedSql,
    SchemaConstraintValidatorInput,
    SchemaConstraintValidatorOutput,
    SchemaConstraintValidatorPayload,
    SchemaConstraintValidatorResult,
)

AGENT_NAME = "guardrail.schema_constraint_validator"


class SchemaConstraintValidatorAgent:
    """Validates every generated statement's referenced tables/columns
    actually exist in the real, crawled catalog; a statement that fails is
    routed to `rejected` and never becomes `validated`."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        tracer: Tracer | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._tracer = tracer or get_tracer("navigraph-agent-runtime")

    async def run(
        self, input: SchemaConstraintValidatorInput
    ) -> SchemaConstraintValidatorOutput:
        start = time.perf_counter()
        request_context = input.request_context
        payload = input.payload

        with self._tracer.start_as_current_span(
            "agent.schema_constraint_validator.run"
        ) as span:
            span.set_attribute("navigraph.tenant_id", request_context.tenant_id)
            span.set_attribute("navigraph.trace_id", request_context.trace_id)
            span.set_attribute("navigraph.agent_name", AGENT_NAME)

            validated, rejected = self._validate_statements(payload)

            result = SchemaConstraintValidatorResult(validated=validated, rejected=rejected)

            # A rejected statement is always a non-recoverable finding by
            # construction (both `unknown_column` and `catalog_lookup_failed`
            # are always `recoverable=False`), so confidence collapses
            # straight to 0.0 rather than a softer partial value -- mirrors
            # `ExecutionPlanningAgent`'s identical confidence rule.
            confidence = 0.0 if rejected else 1.0

            lineage_event = LineageEvent(
                agent_name=AGENT_NAME,
                input_summary=f"statements={len(payload.statements)}",
                output_summary=(
                    f"validated={len(validated)} rejected={len(rejected)}"
                ),
                tenant_id=request_context.tenant_id,
                trace_id=request_context.trace_id,
            )

            latency_ms = (time.perf_counter() - start) * 1000.0
            metadata = AgentMetadata(latency_ms=latency_ms)

            span.set_attribute("navigraph.validated_count", len(validated))
            span.set_attribute("navigraph.rejected_count", len(rejected))

        record_agent_invocation(AGENT_NAME, latency_ms=latency_ms, success=not rejected)
        for error in rejected:
            record_agent_error(AGENT_NAME, error_code=error.code, recoverable=error.recoverable)

        return SchemaConstraintValidatorOutput(
            result=result,
            confidence=confidence,
            lineage_events=[lineage_event],
            errors=[],
            metadata=metadata,
        )

    def _validate_statements(
        self, payload: SchemaConstraintValidatorPayload
    ) -> tuple[list[GeneratedSql], list[AgentError]]:
        validated: list[GeneratedSql] = []
        rejected: list[AgentError] = []

        with session_scope(self._session_factory) as session:
            for statement in payload.statements:
                try:
                    parsed_data_source_id = uuid.UUID(statement.data_source_id)
                except ValueError as exc:
                    rejected.append(
                        AgentError(
                            code="invalid_data_source_id",
                            message=(
                                f"'{statement.data_source_id}' is not a valid data "
                                f"source id: {exc}"
                            ),
                            recoverable=False,
                        )
                    )
                    continue

                try:
                    unknown_column = self._find_unresolvable_column(
                        session, statement, parsed_data_source_id
                    )
                except Exception as exc:  # noqa: BLE001 - never let a DB-side failure crash the agent
                    # Scoped to THIS statement only -- see this module's
                    # docstring for why that differs from
                    # `DataSourceDiscoveryAgent`'s single whole-call error.
                    rejected.append(
                        AgentError(
                            code="catalog_lookup_failed",
                            message=(
                                f"Catalog lookup failed for "
                                f"data_source_id={statement.data_source_id}: {exc}"
                            ),
                            recoverable=False,
                        )
                    )
                    continue

                if unknown_column is not None:
                    rejected.append(
                        AgentError(
                            code="unknown_column",
                            message=(
                                f"Column '{unknown_column}' was not found under any "
                                f"of referenced_tables={statement.referenced_tables} "
                                f"for data_source_id={statement.data_source_id}"
                            ),
                            recoverable=False,
                        )
                    )
                    continue

                validated.append(statement)

        return validated, rejected

    @staticmethod
    def _find_unresolvable_column(
        session: Session, statement: GeneratedSql, data_source_id: uuid.UUID
    ) -> str | None:
        """Return the first entry of `statement.referenced_columns` that
        does not resolve as a real `CatalogColumn`, or `None` if every
        column resolves -- see this module's docstring for exactly what is
        and isn't verified by this check.
        """

        for qualified_name in statement.referenced_columns:
            table_hint, column_name = _split_qualified_column(qualified_name)
            candidate_tables = [table_hint] if table_hint else statement.referenced_tables

            resolved = any(
                find_column(
                    session,
                    data_source_id=data_source_id,
                    table_name=table_name,
                    column_name=column_name,
                )
                is not None
                for table_name in candidate_tables
            )
            if not resolved:
                return qualified_name

        return None


def _split_qualified_column(qualified_name: str) -> tuple[str | None, str]:
    """Split a `"TABLE.COLUMN"` reference into `(table, column)`, or
    `(None, qualified_name)` if it has no `.` at all -- see this module's
    docstring's "TABLE/COLUMN QUALIFICATION" section."""

    if "." in qualified_name:
        table_part, _, column_part = qualified_name.rpartition(".")
        return table_part, column_part
    return None, qualified_name
