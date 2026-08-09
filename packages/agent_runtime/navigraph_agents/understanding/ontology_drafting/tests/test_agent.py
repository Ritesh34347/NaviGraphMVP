"""Real unit tests for the Ontology Drafting agent, DB-free and LLM-network-free.

Mirrors two established conventions from this repo's other agents:
  - `metadata_discovery`'s "mock the session layer, feed plain
    `SimpleNamespace` stand-ins for catalog rows" pattern for
    `_build_inventory` (the agent only reads plain attributes off them).
  - `sql_generation`'s "use `FakeLLMClient` exclusively, assert on the
    closed-candidate-list hallucination-rejection behavior" pattern for
    `_parse_llm_response` and its four per-category helpers.

`asyncio_mode = "auto"` is set in packages/agent_runtime/pyproject.toml, so
these `async def test_...` functions run without an explicit
`@pytest.mark.asyncio` decorator.
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from navigraph_shared.contracts import RequestContext
from navigraph_shared.llm import FakeLLMClient, LLMResponse

from navigraph_agents.understanding.ontology_drafting.agent import OntologyDraftingAgent
from navigraph_agents.understanding.ontology_drafting.contracts import (
    OntologyDraftingInput,
    OntologyDraftingPayload,
)

_AGENT_MODULE = "navigraph_agents.understanding.ontology_drafting.agent"


def _make_input(data_source_id: str) -> OntologyDraftingInput:
    return OntologyDraftingInput(
        request_context=RequestContext(
            tenant_id="tenant-acme",
            user_id="user-1",
            trace_id="trace-1",
            roles=["analyst"],
        ),
        payload=OntologyDraftingPayload(data_source_id=data_source_id),
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


# A single "customers" table with a key column and an email column -- enough
# surface area to exercise entities, relationships (self-referential is fine
# for a unit test), sensitive columns, and metrics all at once.
_CUSTOMERS_TABLE_ID = uuid.uuid4()
_ORDERS_TABLE_ID = uuid.uuid4()
_CUSTOMER_ID_COL = uuid.uuid4()
_EMAIL_COL = uuid.uuid4()
_ORDER_ID_COL = uuid.uuid4()
_ORDER_CUSTOMER_ID_COL = uuid.uuid4()
_ORDER_AMOUNT_COL = uuid.uuid4()


def _catalog_fixture() -> tuple[list[SimpleNamespace], dict[uuid.UUID, list[SimpleNamespace]], list[SimpleNamespace]]:
    tables = [
        _table(_CUSTOMERS_TABLE_ID, "customers", "public"),
        _table(_ORDERS_TABLE_ID, "orders", "public"),
    ]
    columns_by_table = {
        _CUSTOMERS_TABLE_ID: [
            _column(_CUSTOMER_ID_COL, _CUSTOMERS_TABLE_ID, "customer_id", "INTEGER", False),
            _column(_EMAIL_COL, _CUSTOMERS_TABLE_ID, "email", "TEXT", True),
        ],
        _ORDERS_TABLE_ID: [
            _column(_ORDER_ID_COL, _ORDERS_TABLE_ID, "order_id", "INTEGER", False),
            _column(_ORDER_CUSTOMER_ID_COL, _ORDERS_TABLE_ID, "customer_id", "INTEGER", False),
            _column(_ORDER_AMOUNT_COL, _ORDERS_TABLE_ID, "amount", "NUMBER", False),
        ],
    }
    glossary = [
        _glossary(_EMAIL_COL, "Customer Email", ["email address"], "Primary contact email."),
    ]
    return tables, columns_by_table, glossary


@contextmanager
def _patch_catalog():
    tables, columns_by_table, glossary = _catalog_fixture()
    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        patch(f"{_AGENT_MODULE}.list_tables", return_value=tables),
        patch(
            f"{_AGENT_MODULE}.list_columns",
            side_effect=lambda session, *, table_id: columns_by_table[table_id],
        ),
        patch(f"{_AGENT_MODULE}.list_glossary", return_value=glossary),
    ):
        yield


def _valid_llm_json() -> dict:
    return {
        "entities": [
            {
                "name": "Customer",
                "bindings": [
                    {"table": "customers", "schema": "public", "key_column": "customer_id"}
                ],
                "synonyms": ["client"],
                "description": "A customer.",
                "rationale": "customers table holds customer attributes.",
            }
        ],
        "relationships": [
            {
                "name": "Customer places Order",
                "subject": "Customer",
                "predicate": "PLACES",
                "object": "Order",
                "realizing_table": "orders",
                "realizing_schema": "public",
                "subject_key_column": "customer_id",
                "object_key_column": "order_id",
                "rationale": "orders.customer_id joins to customers.customer_id.",
            }
        ],
        "sensitive_columns": [
            {
                "table": "customers",
                "column": "email",
                "rationale": "column name suggests PII.",
            }
        ],
        "metrics": [
            {
                "name": "total_order_amount",
                "entity": "Order",
                "aggregation": "SUM",
                "column": "amount",
                "rationale": "amount is an additive quantity.",
            },
            {
                "name": "order_count",
                "entity": "Order",
                "aggregation": "COUNT",
                "rationale": "counts matching rows.",
            },
        ],
    }


async def test_agent_drafts_a_full_ontology_from_a_valid_llm_response() -> None:
    fake_llm = FakeLLMClient(response=json.dumps(_valid_llm_json()))
    agent = OntologyDraftingAgent(llm_client=fake_llm, session_factory=MagicMock())

    data_source_id = str(uuid.uuid4())
    with _patch_catalog():
        output = await agent.run(_make_input(data_source_id))

    assert output.errors == []
    assert output.confidence == 1.0
    assert output.result.data_source_id == data_source_id

    assert len(output.result.entities) == 1
    entity = output.result.entities[0]
    assert entity.name == "Customer"
    assert len(entity.bindings) == 1
    assert entity.bindings[0].table_name == "customers"
    assert entity.bindings[0].schema_name == "public"
    assert entity.bindings[0].key_column == "customer_id"

    assert len(output.result.relationships) == 1
    relationship = output.result.relationships[0]
    assert relationship.realizing_table == "orders"
    assert relationship.subject_key_column == "customer_id"
    assert relationship.object_key_column == "order_id"

    assert len(output.result.sensitive_columns) == 1
    assert output.result.sensitive_columns[0].column_name == "email"

    assert len(output.result.metrics) == 2
    metrics_by_name = {m.name: m for m in output.result.metrics}
    assert metrics_by_name["total_order_amount"].aggregation == "SUM"
    assert metrics_by_name["total_order_amount"].column == "amount"
    assert metrics_by_name["order_count"].aggregation == "COUNT"
    assert metrics_by_name["order_count"].column is None

    assert len(output.lineage_events) == 1
    lineage = output.lineage_events[0]
    assert lineage.agent_name == "understanding.ontology_drafting"
    assert lineage.tenant_id == "tenant-acme"
    assert lineage.trace_id == "trace-1"
    assert "entities=1" in lineage.output_summary
    assert "relationships=1" in lineage.output_summary
    assert "sensitive_columns=1" in lineage.output_summary
    assert "metrics=2" in lineage.output_summary

    assert output.metadata.model_version == "fake-model"
    assert output.metadata.prompt_version == "v1"
    assert output.metadata.latency_ms >= 0

    # The system prompt was actually sent, and the candidate inventory was
    # actually included in the user message -- not an empty/placeholder call.
    assert len(fake_llm.calls) == 1
    sent = fake_llm.calls[0]
    assert "closed candidate list" in sent["system"].lower() or "candidate list" in sent["system"].lower()
    assert "customer_id" in sent["messages"][0]["content"]


async def test_agent_drops_a_hallucinated_entity_binding_but_keeps_valid_ones() -> None:
    payload = {
        "entities": [
            {
                "name": "Customer",
                "bindings": [
                    {"table": "customers", "schema": "public", "key_column": "customer_id"},
                    {"table": "nonexistent_table", "schema": "public", "key_column": "made_up"},
                ],
                "rationale": "one real binding, one hallucinated.",
            }
        ]
    }
    fake_llm = FakeLLMClient(response=json.dumps(payload))
    agent = OntologyDraftingAgent(llm_client=fake_llm, session_factory=MagicMock())

    with _patch_catalog():
        output = await agent.run(_make_input(str(uuid.uuid4())))

    assert len(output.result.entities) == 1
    assert len(output.result.entities[0].bindings) == 1
    assert output.result.entities[0].bindings[0].table_name == "customers"

    assert len(output.errors) == 1
    assert output.errors[0].code == "llm_returned_invalid_binding"
    assert output.errors[0].recoverable is True
    assert output.confidence == 0.5


async def test_agent_drops_an_entity_entirely_when_every_binding_is_hallucinated() -> None:
    payload = {
        "entities": [
            {
                "name": "GhostEntity",
                "bindings": [{"table": "made_up", "schema": "public", "key_column": "id"}],
                "rationale": "entirely hallucinated.",
            }
        ]
    }
    fake_llm = FakeLLMClient(response=json.dumps(payload))
    agent = OntologyDraftingAgent(llm_client=fake_llm, session_factory=MagicMock())

    with _patch_catalog():
        output = await agent.run(_make_input(str(uuid.uuid4())))

    assert output.result.entities == []
    assert len(output.errors) == 1
    assert output.errors[0].code == "llm_returned_invalid_binding"


async def test_agent_rejects_a_relationship_with_a_hallucinated_realizing_table() -> None:
    payload = {
        "relationships": [
            {
                "name": "Customer places Order",
                "subject": "Customer",
                "predicate": "PLACES",
                "object": "Order",
                "realizing_table": "made_up_table",
                "realizing_schema": "public",
                "subject_key_column": "customer_id",
                "object_key_column": "order_id",
                "rationale": "hallucinated table.",
            }
        ]
    }
    fake_llm = FakeLLMClient(response=json.dumps(payload))
    agent = OntologyDraftingAgent(llm_client=fake_llm, session_factory=MagicMock())

    with _patch_catalog():
        output = await agent.run(_make_input(str(uuid.uuid4())))

    assert output.result.relationships == []
    assert len(output.errors) == 1
    assert output.errors[0].code == "llm_returned_invalid_binding"


async def test_agent_rejects_a_relationship_with_a_mismatched_schema() -> None:
    payload = {
        "relationships": [
            {
                "name": "Customer places Order",
                "subject": "Customer",
                "predicate": "PLACES",
                "object": "Order",
                "realizing_table": "orders",
                "realizing_schema": "wrong_schema",
                "subject_key_column": "customer_id",
                "object_key_column": "order_id",
                "rationale": "real table, wrong schema.",
            }
        ]
    }
    fake_llm = FakeLLMClient(response=json.dumps(payload))
    agent = OntologyDraftingAgent(llm_client=fake_llm, session_factory=MagicMock())

    with _patch_catalog():
        output = await agent.run(_make_input(str(uuid.uuid4())))

    assert output.result.relationships == []
    assert len(output.errors) == 1
    assert output.errors[0].code == "llm_returned_invalid_binding"


async def test_agent_rejects_a_sensitive_column_not_in_the_catalog() -> None:
    payload = {
        "sensitive_columns": [
            {"table": "customers", "column": "ssn_made_up", "rationale": "hallucinated."}
        ]
    }
    fake_llm = FakeLLMClient(response=json.dumps(payload))
    agent = OntologyDraftingAgent(llm_client=fake_llm, session_factory=MagicMock())

    with _patch_catalog():
        output = await agent.run(_make_input(str(uuid.uuid4())))

    assert output.result.sensitive_columns == []
    assert len(output.errors) == 1
    assert output.errors[0].code == "llm_returned_invalid_binding"


async def test_agent_rejects_a_metric_referencing_a_column_not_in_the_catalog() -> None:
    payload = {
        "metrics": [
            {
                "name": "bogus_metric",
                "entity": "Order",
                "aggregation": "SUM",
                "column": "nonexistent_column",
                "rationale": "hallucinated column.",
            }
        ]
    }
    fake_llm = FakeLLMClient(response=json.dumps(payload))
    agent = OntologyDraftingAgent(llm_client=fake_llm, session_factory=MagicMock())

    with _patch_catalog():
        output = await agent.run(_make_input(str(uuid.uuid4())))

    assert output.result.metrics == []
    assert len(output.errors) == 1
    assert output.errors[0].code == "llm_returned_invalid_binding"


async def test_agent_rejects_a_non_count_metric_missing_its_column_as_a_recoverable_error() -> None:
    """A structurally invalid proposal (not a hallucination) -- the LLM
    proposed SUM with no column. `DraftMetric`'s own validator raises
    `ValueError`; the agent must catch it as a recoverable error, never
    crash."""

    payload = {
        "metrics": [
            {
                "name": "bad_metric",
                "entity": "Order",
                "aggregation": "SUM",
                "rationale": "forgot to set a column.",
            }
        ]
    }
    fake_llm = FakeLLMClient(response=json.dumps(payload))
    agent = OntologyDraftingAgent(llm_client=fake_llm, session_factory=MagicMock())

    with _patch_catalog():
        output = await agent.run(_make_input(str(uuid.uuid4())))

    assert output.result.metrics == []
    assert len(output.errors) == 1
    assert output.errors[0].code == "llm_response_invalid_metric_entry"
    assert output.errors[0].recoverable is True
    assert output.confidence == 0.5


async def test_agent_rejects_a_metric_with_an_invalid_aggregation() -> None:
    payload = {
        "metrics": [
            {
                "name": "bad_metric",
                "entity": "Order",
                "aggregation": "MEDIAN",
                "column": "amount",
                "rationale": "not a supported aggregation.",
            }
        ]
    }
    fake_llm = FakeLLMClient(response=json.dumps(payload))
    agent = OntologyDraftingAgent(llm_client=fake_llm, session_factory=MagicMock())

    with _patch_catalog():
        output = await agent.run(_make_input(str(uuid.uuid4())))

    assert output.result.metrics == []
    assert len(output.errors) == 1
    assert output.errors[0].code == "llm_response_invalid_metric_entry"


async def test_agent_handles_a_non_json_llm_response_gracefully() -> None:
    fake_llm = FakeLLMClient(response="not json at all")
    agent = OntologyDraftingAgent(llm_client=fake_llm, session_factory=MagicMock())

    with _patch_catalog():
        output = await agent.run(_make_input(str(uuid.uuid4())))

    assert output.result.entities == []
    assert len(output.errors) == 1
    assert output.errors[0].code == "llm_response_not_json"
    assert output.errors[0].recoverable is True
    assert output.confidence == 0.5


async def test_agent_handles_a_json_response_that_is_not_an_object() -> None:
    fake_llm = FakeLLMClient(response=json.dumps(["not", "an", "object"]))
    agent = OntologyDraftingAgent(llm_client=fake_llm, session_factory=MagicMock())

    with _patch_catalog():
        output = await agent.run(_make_input(str(uuid.uuid4())))

    assert output.result.entities == []
    assert len(output.errors) == 1
    assert output.errors[0].code == "llm_response_malformed"


async def test_agent_handles_invalid_data_source_id_gracefully() -> None:
    """Must not raise: a non-UUID `data_source_id` becomes a non-recoverable
    error, and the LLM is never called."""

    fake_llm = FakeLLMClient(response="should never be read")
    agent = OntologyDraftingAgent(llm_client=fake_llm, session_factory=MagicMock())

    output = await agent.run(_make_input("not-a-uuid"))

    assert fake_llm.calls == []
    assert output.result.entities == []
    assert output.result.data_source_id == "not-a-uuid"
    assert output.confidence == 0.0
    assert len(output.errors) == 1
    assert output.errors[0].code == "invalid_data_source_id"
    assert output.errors[0].recoverable is False

    assert len(output.lineage_events) == 1
    assert output.metadata.latency_ms >= 0


async def test_agent_handles_catalog_lookup_failure_gracefully() -> None:
    fake_llm = FakeLLMClient(response="should never be read")
    agent = OntologyDraftingAgent(llm_client=fake_llm, session_factory=MagicMock())

    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        patch(f"{_AGENT_MODULE}.list_tables", side_effect=RuntimeError("connection refused")),
    ):
        output = await agent.run(_make_input(str(uuid.uuid4())))

    assert fake_llm.calls == []
    assert output.confidence == 0.0
    assert len(output.errors) == 1
    assert output.errors[0].code == "catalog_lookup_failed"
    assert output.errors[0].recoverable is False


async def test_agent_handles_a_data_source_with_no_crawled_columns() -> None:
    fake_llm = FakeLLMClient(response="should never be read")
    agent = OntologyDraftingAgent(llm_client=fake_llm, session_factory=MagicMock())

    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        patch(f"{_AGENT_MODULE}.list_tables", return_value=[]),
        patch(f"{_AGENT_MODULE}.list_glossary", return_value=[]),
    ):
        output = await agent.run(_make_input(str(uuid.uuid4())))

    assert fake_llm.calls == []
    assert output.confidence == 0.0
    assert len(output.errors) == 1
    assert output.errors[0].code == "no_crawled_columns"
    assert output.errors[0].recoverable is False


async def test_agent_handles_an_llm_call_failure_gracefully() -> None:
    agent = OntologyDraftingAgent(
        llm_client=_RaisingLLMClient(), session_factory=MagicMock()
    )

    with _patch_catalog():
        output = await agent.run(_make_input(str(uuid.uuid4())))

    assert output.result.entities == []
    assert output.confidence == 0.0
    assert len(output.errors) == 1
    assert output.errors[0].code == "llm_call_failed"
    assert output.errors[0].recoverable is False


class _RaisingLLMClient(FakeLLMClient):
    async def complete(self, *, system: str, messages: list[dict], max_tokens: int = 1024) -> LLMResponse:  # type: ignore[override]
        raise RuntimeError("network is down")
