"""PII Exposure Checker agent implementation.

For every `GeneratedSql` statement, resolves each entry in
`referenced_columns` back to a real `CatalogColumn` row via
`navigraph_catalog.api.find_column` (scoped to the statement's
`data_source_id`), and rejects the statement if any resolved column is
tagged `is_pii=True` in the catalog UNLESS the caller's
`request_context.roles` includes one of `PII_AUTHORIZED_ROLES`.

Table/column qualification: `GeneratedSql.referenced_columns` entries are
`"TABLE.COLUMN"` qualified strings, not bare column names -- confirmed
against the real `sql_generation.agent._qualified_col` helper that actually
produces them. (An earlier version of this agent assumed an unqualified
flat list requiring a cross-product search against `referenced_tables` --
a real bug, caught live via `tests/integration/guardrail_pipeline/` the
same way the sibling Schema Constraint Validator agent's identical
assumption was caught: every real statement was silently treated as
"column not found anywhere", which this agent's fail-open-on-unresolvable
design turned into a false `cleared` rather than a loud failure -- a real,
security-relevant gap this fix closes, since a `TABLE.COLUMN` string that
never resolves would never be checked against `is_pii` at all.)
`_split_qualified_column` parses the `"TABLE.COLUMN"` form directly; an
entry with no `.` (defensive, not expected in real SQL Generation output
today) falls back to trying every entry in `referenced_tables`.

Scope boundary (deliberate, not a security gap): if a referenced column
does not resolve to any real catalog row at all across every referenced
table, that is an "unknown column" question -- squarely the Schema
Constraint Validator agent's job, not this one's. This agent fails OPEN on
that specific question (an unresolvable column is simply "not proven
PII", so it does not by itself block the statement) and only ever blocks
on a column it can positively confirm is `is_pii=True`.

Session-access design: matches
`navigraph_agents.query.data_source_discovery.agent.DataSourceDiscoveryAgent`
exactly -- the constructor takes a `sessionmaker[Session]` ("session
factory"), `run()` opens one `session_scope` per invocation, and every
`navigraph_catalog.api` call within that `with` block receives an
already-open `Session`.

A genuine catalog-lookup exception (an invalid/unparseable
`data_source_id`, or a real `find_column` failure) is handled per
statement, not per `run()` call: unlike Data Source Discovery (where one
catalog failure invalidates the whole batch), a bad `data_source_id` on
one statement should not prevent this agent from clearing an unrelated,
healthy statement in the same batch. That statement alone is rejected as
`catalog_lookup_failed` -- no partial trust, i.e. it is never added to
`cleared` based on whatever partial lookup results were obtained before
the exception.

Follows the same structural pattern as
`navigraph_agents.understanding.intent_understanding.agent`: open an OTel
span, never raise, always emit a `LineageEvent` and `AgentMetadata` with
`latency_ms` populated.
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

from navigraph_agents.guardrail.pii_exposure_checker.contracts import (
    GeneratedSql,
    PiiExposureCheckerInput,
    PiiExposureCheckerOutput,
    PiiExposureCheckerResult,
)

AGENT_NAME = "guardrail.pii_exposure_checker"

# Roles authorized to see PII columns. Checked directly here, not pushed
# through OPA/Rego -- mirrors `CatalogColumn.is_pii`'s docstring precedent
# (see navigraph_catalog.models) that PII enforcement is a distinct
# concern from general authorization; see DECISIONS.md.
PII_AUTHORIZED_ROLES = {"pii_viewer", "admin"}


class PiiExposureCheckerAgent:
    """Rejects statements that reference a catalog-tagged PII column
    unless the caller's roles authorize it. Has a real catalog dependency
    (`navigraph_catalog.api.find_column`)."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        tracer: Tracer | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._tracer = tracer or get_tracer("navigraph-agent-runtime")

    async def run(self, input: PiiExposureCheckerInput) -> PiiExposureCheckerOutput:
        start = time.perf_counter()
        request_context = input.request_context
        payload = input.payload

        authorized = bool(set(request_context.roles) & PII_AUTHORIZED_ROLES)

        with self._tracer.start_as_current_span("agent.pii_exposure_checker.run") as span:
            span.set_attribute("navigraph.tenant_id", request_context.tenant_id)
            span.set_attribute("navigraph.trace_id", request_context.trace_id)
            span.set_attribute("navigraph.agent_name", AGENT_NAME)

            cleared: list[GeneratedSql] = []
            rejected: list[AgentError] = []

            with session_scope(self._session_factory) as session:
                for statement in payload.statements:
                    self._check_statement(
                        session,
                        statement,
                        authorized=authorized,
                        roles=request_context.roles,
                        cleared=cleared,
                        rejected=rejected,
                    )

            result = PiiExposureCheckerResult(cleared=cleared, rejected=rejected)

            confidence = 0.0 if rejected else 1.0

            lineage_event = LineageEvent(
                agent_name=AGENT_NAME,
                input_summary=f"statements={len(payload.statements)}",
                output_summary=f"cleared={len(cleared)} rejected={len(rejected)}",
                tenant_id=request_context.tenant_id,
                trace_id=request_context.trace_id,
            )

            latency_ms = (time.perf_counter() - start) * 1000.0
            metadata = AgentMetadata(latency_ms=latency_ms)

            span.set_attribute("navigraph.cleared_count", len(cleared))
            span.set_attribute("navigraph.rejected_count", len(rejected))

        record_agent_invocation(AGENT_NAME, latency_ms=latency_ms, success=not rejected)
        for error in rejected:
            record_agent_error(AGENT_NAME, error_code=error.code, recoverable=error.recoverable)

        return PiiExposureCheckerOutput(
            result=result,
            confidence=confidence,
            lineage_events=[lineage_event],
            errors=[],
            metadata=metadata,
        )

    @staticmethod
    def _check_statement(
        session: Session,
        statement: GeneratedSql,
        *,
        authorized: bool,
        roles: list[str],
        cleared: list[GeneratedSql],
        rejected: list[AgentError],
    ) -> None:
        """Resolve `statement`'s referenced columns and route it into
        exactly one of `cleared`/`rejected` -- never both, and never
        neither."""

        try:
            has_pii = PiiExposureCheckerAgent._statement_references_pii(session, statement)
        except Exception as exc:  # noqa: BLE001 - never let a DB-side failure crash the agent
            rejected.append(
                AgentError(
                    code="catalog_lookup_failed",
                    message=(
                        f"data_source_id={statement.data_source_id}: catalog lookup failed: {exc}"
                    ),
                    recoverable=False,
                )
            )
            return

        if has_pii and not authorized:
            rejected.append(
                AgentError(
                    code="pii_column_access_denied",
                    message=(
                        f"role(s) {roles} not authorized for PII column(s) in "
                        f"data_source_id={statement.data_source_id}"
                    ),
                    recoverable=False,
                )
            )
            return

        cleared.append(statement)

    @staticmethod
    def _statement_references_pii(session: Session, statement: GeneratedSql) -> bool:
        """True if any of `statement.referenced_columns` resolves to a
        real, `is_pii=True` catalog column in any of
        `statement.referenced_tables`.

        A column that resolves to no real catalog row at all, in any
        referenced table, is "not proven PII" -- see this module's
        docstring for why that fail-open is a deliberate scope boundary
        (unknown-column detection belongs to Schema Constraint Validator),
        not a security gap.

        Any exception raised by `find_column` itself (a genuine
        catalog-lookup failure, not just "column not found") propagates
        to the caller unchanged, where it is turned into a
        `catalog_lookup_failed` rejection for this statement.
        """

        data_source_id = uuid.UUID(statement.data_source_id)

        for qualified_name in statement.referenced_columns:
            table_hint, column_name = _split_qualified_column(qualified_name)
            candidate_tables = [table_hint] if table_hint else statement.referenced_tables

            for table_name in candidate_tables:
                catalog_column = find_column(
                    session,
                    data_source_id=data_source_id,
                    table_name=table_name,
                    column_name=column_name,
                )
                if catalog_column is not None and catalog_column.is_pii:
                    return True

        return False


def _split_qualified_column(qualified_name: str) -> tuple[str | None, str]:
    """Split a `"TABLE.COLUMN"` reference into `(table, column)`, or
    `(None, qualified_name)` if it has no `.` at all -- see this module's
    docstring's "Table/column qualification" section."""

    if "." in qualified_name:
        table_part, _, column_part = qualified_name.rpartition(".")
        return table_part, column_part
    return None, qualified_name
