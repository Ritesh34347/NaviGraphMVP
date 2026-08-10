"""Ontology Drafting agent implementation.

Onboarding-time only -- never invoked from live conversational traffic.
Reads the already-crawled catalog for one data source
(`navigraph_catalog.api.list_tables`/`list_columns`/`list_glossary`,
exactly like `MetadataDiscoveryAgent`'s session-access pattern) to build a
closed candidate inventory, then asks an LLM to propose candidate
entities, relationships, sensitive columns, and metric aggregations from
it -- following `SemanticRetrievalAgent`/`SqlGenerationAgent`'s
established "closed candidate list, validate every LLM-returned reference,
never trust a hallucination" discipline. Every proposal is designed to be
reviewed by a human before it becomes anything real (see `contracts.py`'s
module docstring) -- this agent never writes to the catalog, never calls
OPA, and never compiles a `navigraph_semantic_model.SemanticModel` itself.
"""

from __future__ import annotations

import json
import time
import uuid as uuid_module
from pathlib import Path
from typing import Any

from navigraph_catalog.api import list_columns, list_glossary, list_tables
from navigraph_catalog.db import session_scope
from navigraph_shared.contracts import AgentError, AgentMetadata, LineageEvent
from navigraph_shared.llm import LLMClient, LLMResponse, strip_json_code_fence
from navigraph_shared.telemetry import (
    get_tracer,
    record_agent_error,
    record_agent_invocation,
)
from opentelemetry.trace import Tracer
from sqlalchemy.orm import Session, sessionmaker

from navigraph_agents.understanding.ontology_drafting.contracts import (
    CatalogInventoryEntry,
    DraftEntity,
    DraftEntityBinding,
    DraftMetric,
    DraftRelationship,
    DraftSensitiveColumn,
    OntologyDraftingInput,
    OntologyDraftingOutput,
    OntologyDraftingResult,
)

AGENT_NAME = "understanding.ontology_drafting"
PROMPT_VERSION = "v1"

_PROMPT_PATH = Path(__file__).parent / "prompts" / "ontology_drafting.md"

_VALID_AGGREGATIONS = {"SUM", "COUNT", "AVG", "MIN", "MAX"}


def _load_system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _format_candidate_inventory(inventory: list[CatalogInventoryEntry]) -> str:
    return json.dumps(
        [
            {
                "table": entry.table_name,
                "schema": entry.schema_name,
                "column": entry.column_name,
                "data_type": entry.data_type,
                "nullable": entry.nullable,
                "is_pii": entry.is_pii,
                "business_name": entry.business_name,
                "synonyms": entry.synonyms,
                "description": entry.description,
            }
            for entry in inventory
        ],
        indent=2,
    )


class OntologyDraftingAgent:
    """Proposes a first-draft Semantic Model (entities, relationships,
    sensitive columns, metric aggregations) from a freshly-crawled
    catalog -- a drafting aid for a human reviewer, never auto-applied."""

    def __init__(
        self,
        llm_client: LLMClient,
        session_factory: sessionmaker[Session],
        tracer: Tracer | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._session_factory = session_factory
        self._tracer = tracer or get_tracer("navigraph-agent-runtime")
        self._system_prompt = _load_system_prompt()

    async def run(self, input: OntologyDraftingInput) -> OntologyDraftingOutput:
        start = time.perf_counter()
        request_context = input.request_context
        data_source_id = input.payload.data_source_id

        errors: list[AgentError] = []
        result = OntologyDraftingResult(data_source_id=data_source_id)
        llm_response: LLMResponse | None = None
        model_version: str | None = None
        prompt_version: str | None = None
        tokens_input: int | None = None
        tokens_output: int | None = None
        inventory: list[CatalogInventoryEntry] = []

        with self._tracer.start_as_current_span("agent.ontology_drafting.run") as span:
            span.set_attribute("navigraph.tenant_id", request_context.tenant_id)
            span.set_attribute("navigraph.trace_id", request_context.trace_id)
            span.set_attribute("navigraph.agent_name", AGENT_NAME)
            span.set_attribute("navigraph.data_source_id", data_source_id)

            try:
                inventory = self._build_inventory(data_source_id)
            except ValueError as exc:
                errors.append(
                    AgentError(
                        code="invalid_data_source_id",
                        message=f"{data_source_id!r} is not a valid data source id: {exc}",
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

            if not errors and not inventory:
                errors.append(
                    AgentError(
                        code="no_crawled_columns",
                        message=(
                            f"No crawled columns found for data source {data_source_id!r} -- "
                            "nothing to draft an ontology from"
                        ),
                        recoverable=False,
                    )
                )

            if not errors:
                user_message = (
                    f"Data source ID: {data_source_id}\n\n"
                    f"Catalog inventory (the ONLY valid tables/columns to reference):\n"
                    f"{_format_candidate_inventory(inventory)}"
                )
                try:
                    llm_response = await self._llm_client.complete(
                        system=self._system_prompt,
                        messages=[{"role": "user", "content": user_message}],
                        # 4096 was too small for a real, non-trivial schema (confirmed
                        # live: a 59-column inventory produced a response that hit this
                        # ceiling exactly and got cut off mid-JSON, failing to parse).
                        # 8192 mirrors the headroom semantic_retrieval's agent.py
                        # already gives its own large-candidate-list completion call.
                        max_tokens=8192,
                    )
                except Exception as exc:  # noqa: BLE001 - never let an LLM-side failure crash the agent
                    errors.append(
                        AgentError(
                            code="llm_call_failed",
                            message=f"LLM call failed: {exc}",
                            recoverable=False,
                        )
                    )

                result = self._parse_llm_response(llm_response, inventory, data_source_id, errors)

                model_version = llm_response.model if llm_response else None
                prompt_version = PROMPT_VERSION
                tokens_input = llm_response.tokens_input if llm_response else None
                tokens_output = llm_response.tokens_output if llm_response else None

            non_recoverable = any(not error.recoverable for error in errors)
            confidence = 0.0 if non_recoverable else (0.5 if errors else 1.0)

            lineage_event = LineageEvent(
                agent_name=AGENT_NAME,
                input_summary=(
                    f"data_source_id={data_source_id!r} candidate_columns={len(inventory)}"
                ),
                output_summary=(
                    f"entities={len(result.entities)} "
                    f"relationships={len(result.relationships)} "
                    f"sensitive_columns={len(result.sensitive_columns)} "
                    f"metrics={len(result.metrics)}"
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

            span.set_attribute("navigraph.entities_proposed", len(result.entities))

        record_agent_invocation(AGENT_NAME, latency_ms=latency_ms, success=not non_recoverable)
        for error in errors:
            record_agent_error(AGENT_NAME, error_code=error.code, recoverable=error.recoverable)

        return OntologyDraftingOutput(
            result=result,
            confidence=confidence,
            lineage_events=[lineage_event],
            errors=errors,
            metadata=metadata,
        )

    def _build_inventory(self, data_source_id: str) -> list[CatalogInventoryEntry]:
        data_source_uuid = uuid_module.UUID(data_source_id)

        with session_scope(self._session_factory) as session:
            tables = list_tables(session, data_source_id=data_source_uuid)
            glossary_by_column_id = {
                glossary.column_id: glossary
                for glossary in list_glossary(session, data_source_id=data_source_uuid)
            }

            inventory: list[CatalogInventoryEntry] = []
            for table in tables:
                for column in list_columns(session, table_id=table.id):
                    glossary = glossary_by_column_id.get(column.id)
                    inventory.append(
                        CatalogInventoryEntry(
                            catalog_column_id=str(column.id),
                            table_name=table.name,
                            schema_name=table.schema.name,
                            column_name=column.name,
                            data_type=column.data_type,
                            nullable=column.nullable,
                            is_pii=column.is_pii,
                            business_name=glossary.business_name if glossary else None,
                            synonyms=list(glossary.synonyms) if glossary else [],
                            description=glossary.description if glossary else None,
                        )
                    )

        return inventory

    @staticmethod
    def _parse_llm_response(
        llm_response: LLMResponse | None,
        inventory: list[CatalogInventoryEntry],
        data_source_id: str,
        errors: list[AgentError],
    ) -> OntologyDraftingResult:
        """Parse the LLM's JSON draft, validating every table/column/schema
        reference against the real, closed catalog `inventory` -- a
        hallucinated reference is dropped with a recoverable `AgentError`,
        never silently trusted, mirroring `SqlGenerationAgent
        ._parse_llm_response`'s identical discipline."""

        empty = OntologyDraftingResult(data_source_id=data_source_id)

        if llm_response is None:
            return empty

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
            return empty

        if not isinstance(data, dict):
            errors.append(
                AgentError(
                    code="llm_response_malformed",
                    message="LLM response JSON was not an object",
                    recoverable=True,
                )
            )
            return empty

        valid_columns = {(entry.table_name, entry.column_name) for entry in inventory}
        valid_schema_by_table = {entry.table_name: entry.schema_name for entry in inventory}
        valid_column_names = {entry.column_name for entry in inventory}

        entities = _parse_entities(data.get("entities"), valid_columns, valid_schema_by_table, errors)
        relationships = _parse_relationships(
            data.get("relationships"), valid_columns, valid_schema_by_table, errors
        )
        sensitive_columns = _parse_sensitive_columns(
            data.get("sensitive_columns"), valid_columns, errors
        )
        metrics = _parse_metrics(data.get("metrics"), valid_column_names, errors)

        return OntologyDraftingResult(
            data_source_id=data_source_id,
            entities=entities,
            relationships=relationships,
            sensitive_columns=sensitive_columns,
            metrics=metrics,
        )


def _parse_entities(
    raw_entities: Any,
    valid_columns: set[tuple[str, str]],
    valid_schema_by_table: dict[str, str],
    errors: list[AgentError],
) -> list[DraftEntity]:
    if raw_entities is None:
        return []
    if not isinstance(raw_entities, list):
        errors.append(
            AgentError(
                code="llm_response_invalid_entities",
                message=f"LLM returned a non-list 'entities': {raw_entities!r}",
                recoverable=True,
            )
        )
        return []

    entities: list[DraftEntity] = []
    for raw_entity in raw_entities:
        if not isinstance(raw_entity, dict):
            errors.append(
                AgentError(
                    code="llm_response_invalid_entity_entry",
                    message=f"Entity entry was not an object: {raw_entity!r}",
                    recoverable=True,
                )
            )
            continue

        name = raw_entity.get("name")
        rationale = raw_entity.get("rationale")
        raw_bindings = raw_entity.get("bindings")
        if not isinstance(name, str) or not isinstance(rationale, str) or not isinstance(
            raw_bindings, list
        ):
            errors.append(
                AgentError(
                    code="llm_response_invalid_entity_entry",
                    message=f"Entity entry missing required fields: {raw_entity!r}",
                    recoverable=True,
                )
            )
            continue

        bindings: list[DraftEntityBinding] = []
        for raw_binding in raw_bindings:
            if not isinstance(raw_binding, dict):
                continue
            table_name = raw_binding.get("table")
            schema_name = raw_binding.get("schema")
            key_column = raw_binding.get("key_column")
            if (
                not isinstance(table_name, str)
                or not isinstance(schema_name, str)
                or not isinstance(key_column, str)
                or (table_name, key_column) not in valid_columns
                or valid_schema_by_table.get(table_name) != schema_name
            ):
                errors.append(
                    AgentError(
                        code="llm_returned_invalid_binding",
                        message=(
                            f"Entity {name!r} proposed a binding to "
                            f"{schema_name!r}.{table_name!r}.{key_column!r}, which is not in "
                            "the candidate catalog inventory"
                        ),
                        recoverable=True,
                    )
                )
                continue
            bindings.append(
                DraftEntityBinding(table_name=table_name, schema_name=schema_name, key_column=key_column)
            )

        if not bindings:
            # Every proposed binding was a hallucination -- this entity has
            # nothing real behind it, drop it entirely rather than keep an
            # entity with zero real bindings.
            continue

        synonyms = raw_entity.get("synonyms")
        description = raw_entity.get("description")
        entities.append(
            DraftEntity(
                name=name,
                bindings=bindings,
                synonyms=[s for s in synonyms if isinstance(s, str)] if isinstance(synonyms, list) else [],
                description=description if isinstance(description, str) else None,
                rationale=rationale,
            )
        )

    return entities


def _parse_relationships(
    raw_relationships: Any,
    valid_columns: set[tuple[str, str]],
    valid_schema_by_table: dict[str, str],
    errors: list[AgentError],
) -> list[DraftRelationship]:
    if raw_relationships is None:
        return []
    if not isinstance(raw_relationships, list):
        errors.append(
            AgentError(
                code="llm_response_invalid_relationships",
                message=f"LLM returned a non-list 'relationships': {raw_relationships!r}",
                recoverable=True,
            )
        )
        return []

    relationships: list[DraftRelationship] = []
    for raw_relationship in raw_relationships:
        if not isinstance(raw_relationship, dict):
            errors.append(
                AgentError(
                    code="llm_response_invalid_relationship_entry",
                    message=f"Relationship entry was not an object: {raw_relationship!r}",
                    recoverable=True,
                )
            )
            continue

        fields = {
            key: raw_relationship.get(key)
            for key in (
                "name",
                "subject",
                "predicate",
                "object",
                "realizing_table",
                "realizing_schema",
                "subject_key_column",
                "object_key_column",
                "rationale",
            )
        }
        if not all(isinstance(value, str) for value in fields.values()):
            errors.append(
                AgentError(
                    code="llm_response_invalid_relationship_entry",
                    message=f"Relationship entry missing required string fields: {raw_relationship!r}",
                    recoverable=True,
                )
            )
            continue

        # Already confirmed str above; str() only narrows the type for mypy.
        realizing_table = str(fields["realizing_table"])
        realizing_schema = str(fields["realizing_schema"])
        subject_key_column = str(fields["subject_key_column"])
        object_key_column = str(fields["object_key_column"])

        if (
            (realizing_table, subject_key_column) not in valid_columns
            or (realizing_table, object_key_column) not in valid_columns
            or valid_schema_by_table.get(realizing_table) != realizing_schema
        ):
            errors.append(
                AgentError(
                    code="llm_returned_invalid_binding",
                    message=(
                        f"Relationship {fields['name']!r} proposed a realizing table/columns "
                        f"({realizing_schema!r}.{realizing_table!r}, {subject_key_column!r}, "
                        f"{object_key_column!r}) not in the candidate catalog inventory"
                    ),
                    recoverable=True,
                )
            )
            continue

        relationships.append(DraftRelationship(**fields))  # type: ignore[arg-type]

    return relationships


def _parse_sensitive_columns(
    raw_sensitive_columns: Any,
    valid_columns: set[tuple[str, str]],
    errors: list[AgentError],
) -> list[DraftSensitiveColumn]:
    if raw_sensitive_columns is None:
        return []
    if not isinstance(raw_sensitive_columns, list):
        errors.append(
            AgentError(
                code="llm_response_invalid_sensitive_columns",
                message=f"LLM returned a non-list 'sensitive_columns': {raw_sensitive_columns!r}",
                recoverable=True,
            )
        )
        return []

    sensitive_columns: list[DraftSensitiveColumn] = []
    for raw_entry in raw_sensitive_columns:
        if not isinstance(raw_entry, dict):
            continue
        table_name = raw_entry.get("table")
        column_name = raw_entry.get("column")
        rationale = raw_entry.get("rationale")
        if (
            not isinstance(table_name, str)
            or not isinstance(column_name, str)
            or not isinstance(rationale, str)
            or (table_name, column_name) not in valid_columns
        ):
            errors.append(
                AgentError(
                    code="llm_returned_invalid_binding",
                    message=(
                        f"Proposed sensitive column {table_name!r}.{column_name!r} is not in "
                        "the candidate catalog inventory"
                    ),
                    recoverable=True,
                )
            )
            continue
        sensitive_columns.append(
            DraftSensitiveColumn(table_name=table_name, column_name=column_name, rationale=rationale)
        )

    return sensitive_columns


def _parse_metrics(
    raw_metrics: Any,
    valid_column_names: set[str],
    errors: list[AgentError],
) -> list[DraftMetric]:
    if raw_metrics is None:
        return []
    if not isinstance(raw_metrics, list):
        errors.append(
            AgentError(
                code="llm_response_invalid_metrics",
                message=f"LLM returned a non-list 'metrics': {raw_metrics!r}",
                recoverable=True,
            )
        )
        return []

    metrics: list[DraftMetric] = []
    for raw_metric in raw_metrics:
        if not isinstance(raw_metric, dict):
            errors.append(
                AgentError(
                    code="llm_response_invalid_metric_entry",
                    message=f"Metric entry was not an object: {raw_metric!r}",
                    recoverable=True,
                )
            )
            continue

        name = raw_metric.get("name")
        entity = raw_metric.get("entity")
        aggregation = raw_metric.get("aggregation")
        rationale = raw_metric.get("rationale")
        column = raw_metric.get("column")

        if (
            not isinstance(name, str)
            or not isinstance(entity, str)
            or not isinstance(rationale, str)
            or aggregation not in _VALID_AGGREGATIONS
        ):
            errors.append(
                AgentError(
                    code="llm_response_invalid_metric_entry",
                    message=f"Metric entry missing required fields or invalid aggregation: {raw_metric!r}",
                    recoverable=True,
                )
            )
            continue

        if column is not None and (not isinstance(column, str) or column not in valid_column_names):
            errors.append(
                AgentError(
                    code="llm_returned_invalid_binding",
                    message=(
                        f"Metric {name!r} proposed column {column!r}, which is not in the "
                        "candidate catalog inventory"
                    ),
                    recoverable=True,
                )
            )
            continue

        try:
            metrics.append(
                DraftMetric(
                    name=name, entity=entity, aggregation=aggregation, column=column, rationale=rationale
                )
            )
        except ValueError as exc:
            # e.g. a non-COUNT aggregation with no column -- a real,
            # structurally invalid proposal, not a hallucinated reference.
            errors.append(
                AgentError(
                    code="llm_response_invalid_metric_entry",
                    message=str(exc),
                    recoverable=True,
                )
            )

    return metrics
