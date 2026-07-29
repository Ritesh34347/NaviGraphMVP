"""Metadata Discovery agent implementation.

Fully deterministic: reads the already-crawled catalog for one data source
(`navigraph_catalog.api.list_tables` / `list_columns` / `list_glossary`) and
assembles a flat, glossary-enriched column listing. No LLM call is involved,
so `AgentMetadata.model_version` / `prompt_version` / `tokens_input` /
`tokens_output` are always `None` -- that is correct for this agent, not a
gap (compare `IntentUnderstandingAgent`, which populates all four from its
`LLMResponse`).

Session-access design: the constructor takes a `sessionmaker[Session]`
("session factory"), not a bare `Session` -- matching
`navigraph_catalog.db.session_scope`'s own documented usage pattern (that
module is the one place in the catalog package that owns commit / rollback /
close, and every caller is expected to hand it a session factory, not a
live session). This agent's `run()` opens one `session_scope` per
invocation, matching a typical per-request lifecycle, and every
`navigraph_catalog.api` call within that `with` block still receives an
already-open `Session`, consistent with that package's "functions never
create their own session" convention.
"""

from __future__ import annotations

import time
import uuid

from navigraph_catalog.api import list_columns, list_glossary, list_tables
from navigraph_catalog.db import session_scope
from navigraph_catalog.models import ColumnGlossary
from navigraph_shared.contracts import AgentError, AgentMetadata, LineageEvent
from navigraph_shared.telemetry import (
    get_tracer,
    record_agent_error,
    record_agent_invocation,
)
from opentelemetry.trace import Tracer
from sqlalchemy.orm import Session, sessionmaker

from navigraph_agents.understanding.metadata_discovery.contracts import (
    CatalogColumnEntry,
    MetadataDiscoveryInput,
    MetadataDiscoveryOutput,
    MetadataDiscoveryResult,
)

AGENT_NAME = "understanding.metadata_discovery"


class MetadataDiscoveryAgent:
    """Discovers a data source's crawled catalog metadata, glossary-enriched."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        tracer: Tracer | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._tracer = tracer or get_tracer("navigraph-agent-runtime")

    async def run(self, input: MetadataDiscoveryInput) -> MetadataDiscoveryOutput:
        start = time.perf_counter()
        request_context = input.request_context
        data_source_id = input.payload.data_source_id

        errors: list[AgentError] = []
        columns: list[CatalogColumnEntry] = []

        with self._tracer.start_as_current_span("agent.metadata_discovery.run") as span:
            span.set_attribute("navigraph.tenant_id", request_context.tenant_id)
            span.set_attribute("navigraph.trace_id", request_context.trace_id)
            span.set_attribute("navigraph.agent_name", AGENT_NAME)
            span.set_attribute("navigraph.data_source_id", data_source_id)

            try:
                columns = self._discover_columns(data_source_id)
            except ValueError as exc:
                errors.append(
                    AgentError(
                        code="invalid_data_source_id",
                        message=f"'{data_source_id}' is not a valid data source id: {exc}",
                        recoverable=False,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - never let a DB-side failure crash the agent
                errors.append(
                    AgentError(
                        code="catalog_lookup_failed",
                        message=f"Catalog lookup failed: {exc}",
                        recoverable=False,
                    )
                )

            result = MetadataDiscoveryResult(data_source_id=data_source_id, columns=columns)
            confidence = 0.0 if errors else 1.0

            lineage_event = LineageEvent(
                agent_name=AGENT_NAME,
                input_summary=f"data_source_id={data_source_id!r}",
                output_summary=f"{len(columns)} columns discovered",
                tenant_id=request_context.tenant_id,
                trace_id=request_context.trace_id,
            )

            latency_ms = (time.perf_counter() - start) * 1000.0

            metadata = AgentMetadata(latency_ms=latency_ms)

            span.set_attribute("navigraph.columns_discovered", len(columns))

        record_agent_invocation(AGENT_NAME, latency_ms=latency_ms, success=not errors)
        for error in errors:
            record_agent_error(AGENT_NAME, error_code=error.code, recoverable=error.recoverable)

        return MetadataDiscoveryOutput(
            result=result,
            confidence=confidence,
            lineage_events=[lineage_event],
            errors=errors,
            metadata=metadata,
        )

    def _discover_columns(self, data_source_id: str) -> list[CatalogColumnEntry]:
        """List every column for `data_source_id`, enriched with its glossary entry.

        Raises `ValueError` if `data_source_id` is not a valid UUID string,
        and lets any other (e.g. DB-connectivity) exception propagate --
        both are caught by `run()` and turned into an `AgentError` rather
        than crashing the agent.
        """

        parsed_data_source_id = uuid.UUID(data_source_id)

        with session_scope(self._session_factory) as session:
            tables = list_tables(session, data_source_id=parsed_data_source_id)
            glossary_by_column_id: dict[uuid.UUID, ColumnGlossary] = {
                entry.column_id: entry
                for entry in list_glossary(session, data_source_id=parsed_data_source_id)
            }

            entries: list[CatalogColumnEntry] = []
            for table in tables:
                for column in list_columns(session, table_id=table.id):
                    glossary_entry = glossary_by_column_id.get(column.id)
                    entries.append(
                        CatalogColumnEntry(
                            catalog_column_id=str(column.id),
                            table_name=table.name,
                            schema_name=table.schema.name,
                            column_name=column.name,
                            data_type=column.data_type,
                            nullable=column.nullable,
                            business_name=(
                                glossary_entry.business_name if glossary_entry else None
                            ),
                            synonyms=list(glossary_entry.synonyms) if glossary_entry else [],
                            description=(
                                glossary_entry.description if glossary_entry else None
                            ),
                        )
                    )

            return entries
