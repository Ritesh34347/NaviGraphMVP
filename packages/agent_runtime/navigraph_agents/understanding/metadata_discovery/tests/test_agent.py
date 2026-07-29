"""Real unit tests for the Metadata Discovery agent, DB-free.

Mirrors `packages/metadata_catalog/tests/test_api.py`'s "mock the session
layer, assert on shape" convention rather than requiring a live Postgres:
`navigraph_catalog.api.list_tables` / `list_columns` / `list_glossary` and
`navigraph_catalog.db.session_scope` are patched at the point they're
imported into `agent.py`, and fed plain `SimpleNamespace` stand-ins for
`CatalogTable` / `CatalogColumn` / `ColumnGlossary` rows (the agent only
reads plain attributes off them -- `.id`, `.name`, `.schema.name`,
`.data_type`, `.nullable`, `.column_id`, `.business_name`, `.synonyms`,
`.description` -- so a real ORM instance isn't needed).

`asyncio_mode = "auto"` is set in packages/agent_runtime/pyproject.toml, so
these `async def test_...` functions run without an explicit
`@pytest.mark.asyncio` decorator.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from navigraph_shared.contracts import RequestContext

from navigraph_agents.understanding.metadata_discovery.agent import (
    MetadataDiscoveryAgent,
)
from navigraph_agents.understanding.metadata_discovery.contracts import (
    MetadataDiscoveryInput,
    MetadataDiscoveryPayload,
)

_AGENT_MODULE = "navigraph_agents.understanding.metadata_discovery.agent"


def _make_input(data_source_id: str) -> MetadataDiscoveryInput:
    return MetadataDiscoveryInput(
        request_context=RequestContext(
            tenant_id="tenant-acme",
            user_id="user-1",
            trace_id="trace-1",
            roles=["analyst"],
        ),
        payload=MetadataDiscoveryPayload(data_source_id=data_source_id),
    )


def _table(table_id: uuid.UUID, name: str, schema_name: str) -> SimpleNamespace:
    return SimpleNamespace(id=table_id, name=name, schema=SimpleNamespace(name=schema_name))


def _column(
    column_id: uuid.UUID,
    table_id: uuid.UUID,
    name: str,
    data_type: str,
    nullable: bool,
    is_pii: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=column_id,
        table_id=table_id,
        name=name,
        data_type=data_type,
        nullable=nullable,
        is_pii=is_pii,
    )


def _glossary(
    column_id: uuid.UUID, business_name: str, synonyms: list[str], description: str | None
) -> SimpleNamespace:
    return SimpleNamespace(
        column_id=column_id,
        business_name=business_name,
        synonyms=synonyms,
        description=description,
    )


@contextmanager
def _fake_session_scope(session_factory):
    yield MagicMock()


async def test_agent_discovers_columns_across_tables_and_schemas_with_partial_glossary() -> None:
    data_source_id = str(uuid.uuid4())

    table_a_id, table_b_id = uuid.uuid4(), uuid.uuid4()
    col_id, email_col_id, name_col_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    tables = [
        _table(table_a_id, "orders", "public"),
        _table(table_b_id, "customers", "sales"),
    ]
    columns_by_table = {
        table_a_id: [_column(col_id, table_a_id, "id", "INTEGER", False)],
        table_b_id: [
            _column(email_col_id, table_b_id, "email", "TEXT", True),
            _column(name_col_id, table_b_id, "name", "TEXT", False),
        ],
    }
    # Only "email" has a glossary entry -- "id" and "name" deliberately don't,
    # exercising the expected common case of an unenriched column.
    glossary = [
        _glossary(email_col_id, "Customer Email", ["email address"], "Primary contact email."),
    ]

    agent = MetadataDiscoveryAgent(session_factory=MagicMock())

    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        patch(f"{_AGENT_MODULE}.list_tables", return_value=tables),
        patch(
            f"{_AGENT_MODULE}.list_columns",
            side_effect=lambda session, *, table_id: columns_by_table[table_id],
        ),
        patch(f"{_AGENT_MODULE}.list_glossary", return_value=glossary),
    ):
        output = await agent.run(_make_input(data_source_id))

    assert output.errors == []
    assert output.confidence == 1.0
    assert output.result.data_source_id == data_source_id
    assert len(output.result.columns) == 3

    by_name = {c.column_name: c for c in output.result.columns}

    id_col = by_name["id"]
    assert id_col.table_name == "orders"
    assert id_col.schema_name == "public"
    assert id_col.data_type == "INTEGER"
    assert id_col.nullable is False
    assert id_col.business_name is None
    assert id_col.synonyms == []
    assert id_col.description is None

    email_col = by_name["email"]
    assert email_col.table_name == "customers"
    assert email_col.schema_name == "sales"
    assert email_col.business_name == "Customer Email"
    assert email_col.synonyms == ["email address"]
    assert email_col.description == "Primary contact email."

    name_col = by_name["name"]
    assert name_col.business_name is None
    assert name_col.synonyms == []

    assert len(output.lineage_events) == 1
    lineage = output.lineage_events[0]
    assert lineage.agent_name == "understanding.metadata_discovery"
    assert lineage.tenant_id == "tenant-acme"
    assert lineage.trace_id == "trace-1"
    assert lineage.output_summary == "3 columns discovered"

    assert output.metadata.latency_ms >= 0
    assert output.metadata.model_version is None
    assert output.metadata.prompt_version is None
    assert output.metadata.tokens_input is None
    assert output.metadata.tokens_output is None


async def test_agent_handles_invalid_data_source_id_gracefully() -> None:
    """Must not raise: a non-UUID `data_source_id` becomes a recoverable error."""

    agent = MetadataDiscoveryAgent(session_factory=MagicMock())

    output = await agent.run(_make_input("not-a-uuid"))

    assert output.result.columns == []
    assert output.result.data_source_id == "not-a-uuid"
    assert output.confidence == 0.0
    assert len(output.errors) == 1
    assert output.errors[0].code == "invalid_data_source_id"
    assert output.errors[0].recoverable is False

    # Lineage and metadata are still produced even on the fallback path.
    assert len(output.lineage_events) == 1
    assert output.metadata.latency_ms >= 0


async def test_agent_handles_catalog_lookup_failure_gracefully() -> None:
    """Must not raise: a DB-side failure becomes a recoverable-marked-false error."""

    agent = MetadataDiscoveryAgent(session_factory=MagicMock())

    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        patch(f"{_AGENT_MODULE}.list_tables", side_effect=RuntimeError("connection refused")),
    ):
        output = await agent.run(_make_input(str(uuid.uuid4())))

    assert output.result.columns == []
    assert output.confidence == 0.0
    assert len(output.errors) == 1
    assert output.errors[0].code == "catalog_lookup_failed"
    assert output.errors[0].recoverable is False
