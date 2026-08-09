"""Data Source Discovery agent implementation.

Fully deterministic: no LLM call, no `prompts/` directory. Resolves each
bare table name produced by Schema Mapping (`SchemaMappingResult.tables`) to
the concrete, tenant-registered `DataSource` that owns it
(`navigraph_catalog.api.list_data_sources` / `list_tables`), then -- for
each DISTINCT data source actually referenced -- constructs its connector
(`navigraph_connectors.registry.get_connector_class`) and calls
`test_connection()` for real, exactly once per distinct data source (not
once per resolved table; see `_check_connectivity`).

Session-access design: matches
`navigraph_agents.understanding.metadata_discovery.agent.MetadataDiscoveryAgent`
exactly -- the constructor takes a `sessionmaker[Session]` ("session
factory"), `run()` opens one `session_scope` per invocation, and every
`navigraph_catalog.api` call within that `with` block receives an
already-open `Session`.

Connector-credential resolution (LIMITATIONS.md item 21, RESOLVED
2026-08-09): this agent constructs each resolved `DataSource`'s connector via
`navigraph_connectors.registry.build_connector`, passing that `DataSource`'s
own `connection_ref` and an injected `SecretsProvider` -- so two `DataSource`
rows of the same `source_type` now resolve to genuinely distinct real
credentials instead of both reading the same global env vars. `secrets`
defaults to a real `EnvVarSecretsProvider()` (this project's local/dev
default) if the caller doesn't inject one.

SAFETY-RELEVANT DEVIATION FROM THE USUAL AGENT CONTRACT: every other agent
built so far treats every `AgentError` as a soft, recoverable-by-default
signal that still lets a caller use the rest of the result. This agent is
the first deliberate exception: when a resolved data source's real
`test_connection()` probe comes back `success=False`, that is reported as an
`AgentError(code="data_source_unreachable", recoverable=False)`. Per this
project's design, a pipeline that cannot verify a data source is live right
now must NOT proceed to generate/execute SQL against it. This agent still
never raises a Python exception -- the error is only ever surfaced via
`AgentOutput.errors`, per the universal "agents never raise" contract -- but
a caller (a future Coordinator, or this package's own integration tests)
MUST check `output.errors` for a non-recoverable entry and halt the
pipeline rather than proceeding, exactly as it would for any other
non-recoverable error, only more deliberately so here: this is a designed
gate, not a fallback path.
"""

from __future__ import annotations

import time

from navigraph_catalog.api import list_data_sources, list_tables
from navigraph_catalog.db import session_scope
from navigraph_catalog.models import DataSource
from navigraph_connectors.base import ConnectionTestResult
from navigraph_connectors.registry import build_connector
from navigraph_shared.contracts import AgentError, AgentMetadata, LineageEvent
from navigraph_shared.secrets import EnvVarSecretsProvider, SecretsProvider
from navigraph_shared.telemetry import (
    get_tracer,
    record_agent_error,
    record_agent_invocation,
)
from opentelemetry.trace import Tracer
from sqlalchemy.orm import Session, sessionmaker

from navigraph_agents.query.data_source_discovery.contracts import (
    DataSourceDiscoveryInput,
    DataSourceDiscoveryOutput,
    DataSourceDiscoveryResult,
    ResolvedDataSource,
)

AGENT_NAME = "query.data_source_discovery"


class DataSourceDiscoveryAgent:
    """Resolves requested tables to their owning data sources and probes
    each distinct data source's real connectivity."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        secrets: SecretsProvider | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._secrets = secrets or EnvVarSecretsProvider()
        self._tracer = tracer or get_tracer("navigraph-agent-runtime")

    async def run(self, input: DataSourceDiscoveryInput) -> DataSourceDiscoveryOutput:
        start = time.perf_counter()
        request_context = input.request_context
        requested_tables = input.payload.tables

        errors: list[AgentError] = []
        resolved: list[ResolvedDataSource] = []
        matched: list[tuple[str, DataSource]] = []
        unresolved_tables: list[str] = list(requested_tables)

        with self._tracer.start_as_current_span("agent.data_source_discovery.run") as span:
            span.set_attribute("navigraph.tenant_id", request_context.tenant_id)
            span.set_attribute("navigraph.trace_id", request_context.trace_id)
            span.set_attribute("navigraph.agent_name", AGENT_NAME)

            try:
                matched, unresolved_tables = self._resolve_table_owners(
                    requested_tables, tenant_id=request_context.tenant_id
                )
            except Exception as exc:  # noqa: BLE001 - never let a DB-side failure crash the agent
                errors.append(
                    AgentError(
                        code="catalog_lookup_failed",
                        message=f"Catalog lookup failed: {exc}",
                        recoverable=False,
                    )
                )
                # No partial result is trustworthy once the catalog lookup
                # itself failed -- every requested table is unresolved,
                # mirroring MetadataDiscoveryAgent/OntologyAgent's identical
                # "the whole lookup failed, don't return a part-before /
                # part-after mix" convention.
                matched = []
                unresolved_tables = list(requested_tables)

            # Real connectivity probe, cached per distinct `data_source_id`
            # so a data source shared by several resolved tables is only
            # ever probed once per `run()` call.
            connection_test_cache: dict[str, ConnectionTestResult] = {}
            for table_name, data_source in matched:
                data_source_id = str(data_source.id)
                if data_source_id not in connection_test_cache:
                    connection_test_cache[data_source_id] = self._check_connectivity(data_source)
                test_result = connection_test_cache[data_source_id]

                resolved.append(
                    ResolvedDataSource(
                        table_name=table_name,
                        data_source_id=data_source_id,
                        source_type=data_source.source_type,
                        reachable=test_result.success,
                        connection_test_latency_ms=test_result.latency_ms,
                        connection_test_message=test_result.message,
                    )
                )

            # See this module's docstring: an unreachable data source is a
            # deliberate NON-recoverable error, one per distinct
            # unreachable data source (not per resolved table), matching
            # the connectivity probe itself only running once per distinct
            # data source.
            for data_source_id, test_result in connection_test_cache.items():
                if not test_result.success:
                    errors.append(
                        AgentError(
                            code="data_source_unreachable",
                            message=(
                                f"Data source {data_source_id} is not reachable: "
                                f"{test_result.message}"
                            ),
                            recoverable=False,
                        )
                    )

            is_multi_source = len({r.data_source_id for r in resolved}) > 1

            result = DataSourceDiscoveryResult(
                resolved=resolved,
                is_multi_source=is_multi_source,
                unresolved_tables=unresolved_tables,
            )

            # Any error here is non-recoverable by construction (both
            # `catalog_lookup_failed` and `data_source_unreachable` are
            # always `recoverable=False`), so confidence collapses straight
            # to 0.0 rather than the softer 0.5 partial-success value used
            # when tables are merely unresolved but every reachable data
            # source stayed reachable.
            confidence = 0.0 if errors else (1.0 if not unresolved_tables else 0.5)

            lineage_event = LineageEvent(
                agent_name=AGENT_NAME,
                input_summary=f"tables={requested_tables}",
                output_summary=(
                    f"resolved={len(resolved)} unresolved={unresolved_tables} "
                    f"is_multi_source={is_multi_source}"
                ),
                tenant_id=request_context.tenant_id,
                trace_id=request_context.trace_id,
            )

            latency_ms = (time.perf_counter() - start) * 1000.0

            metadata = AgentMetadata(latency_ms=latency_ms)

            span.set_attribute("navigraph.tables_resolved", len(resolved))
            span.set_attribute("navigraph.tables_unresolved", len(unresolved_tables))
            span.set_attribute("navigraph.is_multi_source", is_multi_source)

        record_agent_invocation(AGENT_NAME, latency_ms=latency_ms, success=not errors)
        for error in errors:
            record_agent_error(AGENT_NAME, error_code=error.code, recoverable=error.recoverable)

        return DataSourceDiscoveryOutput(
            result=result,
            confidence=confidence,
            lineage_events=[lineage_event],
            errors=errors,
            metadata=metadata,
        )

    def _resolve_table_owners(
        self, requested_tables: list[str], *, tenant_id: str
    ) -> tuple[list[tuple[str, DataSource]], list[str]]:
        """Resolve each requested (bare, verbatim) table name to the
        `DataSource` that owns it.

        Matches case-insensitively, mirroring the convention already
        established by
        `navigraph_catalog.ingestion.schema_enrichment_crawler._find_catalog_column`
        (real crawled Snowflake identifiers are typically uppercase, but a
        caller like Schema Mapping's `SchemaMappingResult.tables` is not
        guaranteed to match that case exactly). Builds a single
        lowercased-table-name -> `DataSource` index across every one of
        this tenant's registered data sources ONCE, then looks up each
        requested name in it, rather than re-scanning per requested table.

        Tie-break: if the same table name exists in more than one of this
        tenant's data sources (rare, but nothing upstream prevents it), the
        tenant's marked `is_default` data source wins when exactly one of
        the colliding sources is marked default (LIMITATIONS.md item 26,
        real navikenz-poc case) -- resolving to a meaningful signal instead
        of guessing. When no colliding source is marked default (or more
        than one somehow is, though the partial unique index at the DB
        level prevents that within one tenant), the first one encountered
        -- in `list_data_sources`' own return order -- wins, and the rest
        are silently shadowed; there is no ordering guarantee on
        `list_data_sources`' underlying query, so that fallback "first"
        means only "deterministic for a given DB state", not a ranking.
        """

        with session_scope(self._session_factory) as session:
            data_sources = list_data_sources(session, tenant_id=tenant_id)
            # Default-marked sources processed first so they win any
            # collision against `if key not in table_owner` below, without
            # changing relative order among non-default sources.
            ordered_data_sources = sorted(
                data_sources, key=lambda data_source: not data_source.is_default
            )

            table_owner: dict[str, DataSource] = {}
            for data_source in ordered_data_sources:
                for table in list_tables(session, data_source_id=data_source.id):
                    key = table.name.lower()
                    if key not in table_owner:
                        table_owner[key] = data_source

        matched: list[tuple[str, DataSource]] = []
        unresolved_tables: list[str] = []
        for requested_name in requested_tables:
            owner = table_owner.get(requested_name.lower())
            if owner is None:
                unresolved_tables.append(requested_name)
            else:
                matched.append((requested_name, owner))

        return matched, unresolved_tables

    def _check_connectivity(self, data_source: DataSource) -> ConnectionTestResult:
        """Construct a real, per-`DataSource` connector for `data_source`
        and probe it for real.

        `build_connector` raises `ValueError` for an unregistered
        `source_type` or a `connection_ref` missing a required field, and
        constructing the resolved connector class could in principle raise
        too (e.g. a future connector's `__init__` doing eager validation).
        `Connector.test_connection()` itself is documented to never raise,
        but everything upstream of it is still fallible -- both are caught
        here and folded into the same "unreachable" `ConnectionTestResult`
        shape the rest of this agent already treats as ordinary data, not
        as an exception to handle specially.
        """

        try:
            connector = build_connector(
                data_source.source_type,
                connection_ref=data_source.connection_ref,
                secrets=self._secrets,
            )
            return connector.test_connection()
        except Exception as exc:  # noqa: BLE001 - fold any construction failure into "unreachable"
            return ConnectionTestResult(
                success=False,
                message=f"Failed to construct connector for source_type={data_source.source_type!r}: {exc}",
            )
