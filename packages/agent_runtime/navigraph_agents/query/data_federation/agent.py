"""Data Federation agent implementation.

Fully deterministic: no LLM call, no `prompts/` directory. Executes each
`ExecutionPlan` Execution Planning produced for real -- either via a direct,
per-`DataSource` `Connector` (route `"direct_connector"`) or via
`navigraph_federation.trino_client.TrinoClient` (route `"trino"`) -- and
combines every plan's individual result into one final result set.

REAL VS. UNEXERCISED-AGAINST-A-REAL-SECOND-SOURCE, stated plainly up front:
this environment has exactly one real registered data source (see
`DECISIONS.md`'s 2026-07-28 Trino entry -- "Trino stood up for real
federation despite one registered source"), so the single-plan,
single-source path below is the only path this agent's real integration
tests can currently exercise end-to-end. The multi-source combine path
(`_combine_results`) is REAL code -- not a stub -- exercised by this
package's own unit tests using two fake `SourceQueryResult`s, but it has
never run against two genuinely distinct, live data sources. See
`_combine_results`'s docstring for the precise, honest limits of what that
combine step can and cannot correctly infer.

Session-access design: the constructor takes a `sessionmaker[Session]`
("session factory"), matching
`navigraph_agents.understanding.metadata_discovery.agent.MetadataDiscoveryAgent`
and
`navigraph_agents.query.data_source_discovery.agent.DataSourceDiscoveryAgent`'s
identical constructor pattern -- needed here to resolve a `direct_connector`
plan's `data_source_id` to the real `DataSource` row that tells this agent
which connector class to construct (`DataSource.source_type`).

Connector-credential resolution (LIMITATIONS.md item 21, RESOLVED
2026-08-09 -- same fix `DataSourceDiscoveryAgent`'s module docstring
describes): connectors are constructed via
`navigraph_connectors.registry.build_connector`, passing the resolved
`DataSource`'s own `connection_ref` and an injected `SecretsProvider`, so
this agent now resolves genuinely per-`DataSource` credentials rather than
every connector of a `source_type` sharing one global env-var-backed
settings object.

Catalog-lookup gap (documented, not solved here): `navigraph_catalog.api`
has no direct "get `DataSource` by id" function, only
`list_data_sources(session, *, tenant_id)`. `_get_data_source` below lists
every one of the tenant's registered data sources and matches by id in
Python -- correct, but O(number of tenant data sources) per plan rather
than a single indexed lookup; fine at this project's current scale (one
real registered source), worth revisiting if a tenant ever registers many
sources and a plan federates across a large subset of them.

Error contract: per `navigraph_connectors.base.Connector`'s documented
contract, `execute_query` MAY raise (unlike `test_connection`, which must
never raise) -- `TrinoClient.execute_query` mirrors that same contract. This
agent's `run()` catches any exception around EVERY execution attempt
(`_execute_plan`) and turns it into a non-recoverable
`AgentError(code="query_execution_failed")`; that plan's result is simply
omitted from `per_source_results` rather than crashing the whole federation
attempt, so other plans that could still succeed are unaffected. If it was
the only plan, `per_source_results` ends up empty, `final_rows` is empty,
and `confidence` collapses to `0.0` -- never a raised Python exception, per
the universal "agents never raise" contract.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from navigraph_catalog.api import list_data_sources
from navigraph_catalog.db import session_scope
from navigraph_catalog.models import DataSource
from navigraph_connectors.base import QueryResult
from navigraph_connectors.registry import build_connector
from navigraph_federation.dialect import rewrite_sql_for_trino
from navigraph_federation.trino_client import TrinoClient
from navigraph_shared.contracts import AgentError, AgentMetadata, LineageEvent
from navigraph_shared.secrets import EnvVarSecretsProvider, SecretsProvider
from navigraph_shared.telemetry import (
    get_tracer,
    record_agent_error,
    record_agent_invocation,
)
from opentelemetry.trace import Tracer
from sqlalchemy.orm import Session, sessionmaker

from navigraph_agents.query.data_federation.contracts import (
    DataFederationInput,
    DataFederationOutput,
    DataFederationResult,
    ExecutionPlan,
    SourceQueryResult,
)

AGENT_NAME = "query.data_federation"


class DataFederationAgent:
    """Executes every `ExecutionPlan` for real and combines the results."""

    def __init__(
        self,
        catalog_session_factory: sessionmaker[Session],
        trino_client: TrinoClient | None = None,
        secrets: SecretsProvider | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        self._session_factory = catalog_session_factory
        # Lazily constructed if not passed -- matches this project's
        # established lazy-client convention (see `TrinoClient.__init__`
        # itself, and `Neo4jClient.__init__`/`_get_driver`) and, more
        # importantly here, avoids ever constructing a real `TrinoClient`
        # (and thus reading `FederationSettings()` from the environment) for
        # a run whose plans never actually use the `"trino"` route.
        self._trino_client = trino_client
        self._secrets = secrets or EnvVarSecretsProvider()
        self._tracer = tracer or get_tracer("navigraph-agent-runtime")

    def _get_trino_client(self) -> TrinoClient:
        if self._trino_client is None:
            self._trino_client = TrinoClient()
        return self._trino_client

    async def run(self, input: DataFederationInput) -> DataFederationOutput:
        start = time.perf_counter()
        request_context = input.request_context
        plans = input.payload.plans

        errors: list[AgentError] = []
        per_source_results: list[SourceQueryResult] = []

        with self._tracer.start_as_current_span("agent.data_federation.run") as span:
            span.set_attribute("navigraph.tenant_id", request_context.tenant_id)
            span.set_attribute("navigraph.trace_id", request_context.trace_id)
            span.set_attribute("navigraph.agent_name", AGENT_NAME)
            span.set_attribute("navigraph.plan_count", len(plans))

            for plan in plans:
                try:
                    per_source_results.append(
                        self._execute_plan(plan, tenant_id=request_context.tenant_id)
                    )
                except Exception as exc:  # noqa: BLE001 - never let one plan's failure crash the run
                    errors.append(
                        AgentError(
                            code="query_execution_failed",
                            message=(
                                f"Execution failed for data_source_id={plan.data_source_id!r} "
                                f"via route={plan.route!r}: {exc}"
                            ),
                            recoverable=False,
                        )
                    )

            final_columns, final_rows = self._combine_results(per_source_results)
            distinct_sources = {r.data_source_id for r in per_source_results}
            federated = len(distinct_sources) > 1

            result = DataFederationResult(
                per_source_results=per_source_results,
                final_columns=final_columns,
                final_rows=final_rows,
                final_row_count=len(final_rows),
                federated=federated,
            )

            if not per_source_results:
                # Either there were no plans at all, or every single plan
                # failed -- either way there is nothing trustworthy to
                # return.
                confidence = 0.0
            elif errors:
                # Some, but not all, plans failed -- a genuine partial
                # result.
                confidence = 0.5
            else:
                confidence = 1.0

            lineage_event = LineageEvent(
                agent_name=AGENT_NAME,
                input_summary=f"plans={len(plans)}",
                output_summary=(
                    f"sources_queried={len(per_source_results)} "
                    f"federated={federated} final_row_count={len(final_rows)} "
                    f"errors={len(errors)}"
                ),
                tenant_id=request_context.tenant_id,
                trace_id=request_context.trace_id,
            )

            latency_ms = (time.perf_counter() - start) * 1000.0

            metadata = AgentMetadata(latency_ms=latency_ms)

            span.set_attribute("navigraph.sources_queried", len(per_source_results))
            span.set_attribute("navigraph.federated", federated)

        record_agent_invocation(AGENT_NAME, latency_ms=latency_ms, success=not errors)
        for error in errors:
            record_agent_error(AGENT_NAME, error_code=error.code, recoverable=error.recoverable)

        return DataFederationOutput(
            result=result,
            confidence=confidence,
            lineage_events=[lineage_event],
            errors=errors,
            metadata=metadata,
        )

    def _execute_plan(self, plan: ExecutionPlan, *, tenant_id: str) -> SourceQueryResult:
        """Execute one plan for real via its assigned route.

        Lets any exception from the underlying `execute_query` call (or
        from resolving the `DataSource`/connector for a `direct_connector`
        plan) propagate to `run()`, which is the one place that catches it
        and turns it into an `AgentError` -- see this module's docstring's
        "Error contract" section for why that catch happens there and not
        here.
        """

        plan_start = time.perf_counter()

        if plan.route == "direct_connector":
            query_result = self._execute_via_connector(plan, tenant_id=tenant_id)
        else:
            query_result = self._execute_via_trino(plan)

        latency_ms = (time.perf_counter() - plan_start) * 1000.0

        return SourceQueryResult(
            data_source_id=plan.data_source_id,
            columns=query_result.columns,
            rows=query_result.rows,
            row_count=query_result.row_count,
            route_used=plan.route,
            execution_latency_ms=latency_ms,
        )

    def _execute_via_connector(self, plan: ExecutionPlan, *, tenant_id: str) -> QueryResult:
        with session_scope(self._session_factory) as session:
            data_source = self._get_data_source(
                session, data_source_id=plan.data_source_id, tenant_id=tenant_id
            )
            source_type = data_source.source_type
            connection_ref = data_source.connection_ref

        connector = build_connector(
            source_type, connection_ref=connection_ref, secrets=self._secrets
        )
        return connector.execute_query(plan.sql, plan.params or None)

    def _execute_via_trino(self, plan: ExecutionPlan) -> QueryResult:
        client = self._get_trino_client()
        rewritten_sql = rewrite_sql_for_trino(plan.sql, catalog=client.catalog)
        return client.execute_query(rewritten_sql, plan.params or None)

    @staticmethod
    def _get_data_source(session: Session, *, data_source_id: str, tenant_id: str) -> DataSource:
        """Look up a single `DataSource` row by id, scoped to `tenant_id`.

        `navigraph_catalog.api` has no direct "get by id" function today
        (only `list_data_sources(session, *, tenant_id)`) -- see this
        module's docstring's "Catalog-lookup gap" section. Raises
        `ValueError` if `data_source_id` is not a valid UUID string, or if
        no data source with that id is registered for `tenant_id`; both are
        caught by `run()`'s per-plan try/except and turned into an
        `AgentError` rather than crashing the agent.
        """

        parsed_id = uuid.UUID(data_source_id)
        for data_source in list_data_sources(session, tenant_id=tenant_id):
            if data_source.id == parsed_id:
                return data_source

        raise ValueError(
            f"data_source_id={data_source_id!r} is not registered for tenant_id={tenant_id!r}"
        )

    @staticmethod
    def _combine_results(
        per_source_results: list[SourceQueryResult],
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """Combine every source's individual rows into one final result set.

        REAL, exercised path: zero or one source. Zero sources returns
        `([], [])`. Exactly one source passes its `columns`/`rows` straight
        through unchanged -- this is the only branch of this function any
        real run in this environment can currently reach, since only one
        data source is registered today (see this module's docstring).

        STRUCTURALLY-PRESENT-BUT-ONLY-UNIT-TESTED-AGAINST-FAKE-SOURCES path:
        two or more sources. This performs a real in-memory join keyed on
        whatever column NAMES every source's result happens to share (the
        intersection of all `columns` lists) -- rows from each source are
        grouped by the tuple of their shared-column values, and rows
        sharing the same key across sources are merged into one row per
        key. Columns that are NOT part of the shared join key are
        namespaced as `"{data_source_id}.{column}"` in the combined output,
        so two sources that happen to both select a same-named non-key
        column (e.g. both select `"amount"`, meaning different things) never
        silently overwrite one value with the other.

        Documented limitations of that join, honestly:

        - Treating "every column name shared across all sources" as the
          join key is a heuristic, not a real join predicate: it has no way
          to know a shared column name is coincidental rather than a real
          join key. `ExecutionPlan` carries no explicit join-key hint today
          -- the correct real fix is a future field on `ExecutionPlan`
          naming the intended join key(s), produced by whichever upstream
          agent (Execution Planning) actually determined a cross-source
          join was needed in the first place, not this generic function
          guessing from column-name overlap alone.
        - If the sources share NO column names at all, there is no
          derivable join key whatsoever -- this function falls back to a
          plain UNION (concatenating every source's rows, filling in every
          distinct column name seen across all sources), rather than
          silently dropping any source's data. This is explicitly a
          different combine strategy (union, not join) applied only when a
          join is structurally impossible to infer.
        - This whole branch has been exercised ONLY by this package's own
          unit tests, which construct two or three FAKE `SourceQueryResult`
          objects by hand -- it has never run against two genuinely live,
          distinct data sources, since only one is registered in this
          environment today.
        """

        if not per_source_results:
            return [], []

        if len(per_source_results) == 1:
            only = per_source_results[0]
            return list(only.columns), [dict(row) for row in only.rows]

        shared_columns: set[str] = set(per_source_results[0].columns)
        for source_result in per_source_results[1:]:
            shared_columns &= set(source_result.columns)

        if not shared_columns:
            # No derivable join key at all -- union rather than silently
            # drop data. See this function's docstring.
            final_columns: list[str] = []
            for source_result in per_source_results:
                for column in source_result.columns:
                    if column not in final_columns:
                        final_columns.append(column)

            final_rows = [
                dict(row) for source_result in per_source_results for row in source_result.rows
            ]
            return final_columns, final_rows

        join_keys = sorted(shared_columns)

        groups: dict[tuple[Any, ...], dict[str, Any]] = {}
        ordered_keys: list[tuple[Any, ...]] = []
        final_columns = list(join_keys)

        for source_result in per_source_results:
            non_key_columns = [c for c in source_result.columns if c not in shared_columns]
            for column in non_key_columns:
                namespaced = f"{source_result.data_source_id}.{column}"
                if namespaced not in final_columns:
                    final_columns.append(namespaced)

            for row in source_result.rows:
                key = tuple(row.get(join_key) for join_key in join_keys)
                if key not in groups:
                    groups[key] = {join_key: row.get(join_key) for join_key in join_keys}
                    ordered_keys.append(key)
                for column in non_key_columns:
                    groups[key][f"{source_result.data_source_id}.{column}"] = row.get(column)

        final_rows = [groups[key] for key in ordered_keys]
        return final_columns, final_rows
