"""Unit tests for `navigraph_semantic_model.onboarding`, fully offline.

`compile_draft_to_semantic_model` takes the same plain dict shape
`OntologyDraftingResult` serializes to -- these tests build that shape by
hand rather than importing `navigraph_agents` (this package has no
dependency on it, by design -- see `onboarding.py`'s module docstring).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from navigraph_semantic_model.onboarding import compile_draft_to_semantic_model

_TENANT_ID = "tenant-acme"
_DATA_SOURCE_NAME = "fidelity_poc_snowflake_v2"


def _full_draft() -> dict:
    return {
        "entities": [
            {
                "name": "Customer",
                "bindings": [
                    {"table_name": "customers", "schema_name": "public", "key_column": "customer_id"}
                ],
                "synonyms": ["client"],
                "description": "A customer.",
            },
            {
                "name": "Order",
                "bindings": [
                    {"table_name": "orders", "schema_name": "public", "key_column": "order_id"}
                ],
                "synonyms": [],
                "description": None,
            },
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
            }
        ],
        "sensitive_columns": [
            {"table_name": "customers", "column_name": "email"},
        ],
        "metrics": [
            {"name": "total_order_amount", "entity": "Order", "aggregation": "SUM", "column": "amount"},
            {"name": "order_count", "entity": "Order", "aggregation": "COUNT", "column": None},
        ],
    }


def test_compiles_a_full_draft_into_a_valid_semantic_model_with_no_warnings() -> None:
    model, warnings = compile_draft_to_semantic_model(
        _full_draft(), tenant_id=_TENANT_ID, data_source_name=_DATA_SOURCE_NAME
    )

    assert warnings == []
    assert model.tenant_id == _TENANT_ID
    assert model.version == 1

    assert len(model.entities) == 2
    customer = model.get_entity("Customer")
    assert customer.synonyms == ["client"]
    assert customer.description == "A customer."
    assert len(customer.bindings) == 1
    assert customer.bindings[0].data_source == _DATA_SOURCE_NAME
    assert customer.bindings[0].table == "public.customers"
    assert customer.bindings[0].key == "customer_id"

    # The sensitive column was attached to Customer (the entity that binds
    # the "customers" table), not left dangling at the top level.
    assert len(customer.sensitive_columns) == 1
    assert customer.sensitive_columns[0].column == "email"

    order = model.get_entity("Order")
    assert order.sensitive_columns == []

    assert len(model.relationships) == 1
    relationship = model.relationships[0]
    assert relationship.name == "Customer places Order"
    assert relationship.via.data_source == _DATA_SOURCE_NAME
    assert relationship.via.table == "public.orders"
    assert relationship.via.subject_key == "customer_id"
    assert relationship.via.object_key == "order_id"

    assert len(model.metrics) == 2
    metrics_by_name = {m.name: m for m in model.metrics}
    assert metrics_by_name["total_order_amount"].aggregation == "SUM"
    assert metrics_by_name["total_order_amount"].column == "amount"
    assert metrics_by_name["order_count"].aggregation == "COUNT"
    assert metrics_by_name["order_count"].column is None


def test_a_sensitive_column_with_no_matching_entity_is_dropped_with_a_warning() -> None:
    draft = _full_draft()
    draft["sensitive_columns"] = [{"table_name": "no_entity_binds_this_table", "column_name": "ssn"}]

    model, warnings = compile_draft_to_semantic_model(
        draft, tenant_id=_TENANT_ID, data_source_name=_DATA_SOURCE_NAME
    )

    assert model.get_entity("Customer").sensitive_columns == []
    assert model.get_entity("Order").sensitive_columns == []
    assert len(warnings) == 1
    assert "no_entity_binds_this_table" in warnings[0]
    assert "dropped" in warnings[0]


def test_a_metric_naming_a_rejected_entity_is_dropped_with_a_warning() -> None:
    """A human reviewer may reject/rename an entity between drafting and
    compiling -- a metric that still names the old entity must be dropped,
    not silently kept dangling or allowed to fail `SemanticModel`'s own
    constructor validation with a confusing error."""

    draft = _full_draft()
    draft["entities"] = [e for e in draft["entities"] if e["name"] != "Order"]
    # The relationship also references "Order" as a free-form label, which
    # is fine -- Relationship.subject/object need not be declared entities
    # (see contracts.py). Only the metric's `entity` field is checked.

    model, warnings = compile_draft_to_semantic_model(
        draft, tenant_id=_TENANT_ID, data_source_name=_DATA_SOURCE_NAME
    )

    assert model.metrics == []
    assert len(warnings) == 2
    assert all("Order" in w and "dropped" in w for w in warnings)


def test_compiling_with_no_entities_raises_because_semantic_model_requires_at_least_one() -> None:
    """Not this function's own validation -- `SemanticModel.entities` has
    `Field(min_length=1)`. A draft with every entity rejected has nothing
    left to compile, and that must surface loudly, not as an empty,
    silently-accepted document."""

    draft = {"entities": [], "relationships": [], "sensitive_columns": [], "metrics": []}

    with pytest.raises(ValidationError):
        compile_draft_to_semantic_model(
            draft, tenant_id=_TENANT_ID, data_source_name=_DATA_SOURCE_NAME
        )


def test_compiling_a_minimal_draft_with_only_entities_produces_empty_relationships_and_metrics() -> None:
    draft = {
        "entities": [
            {
                "name": "Customer",
                "bindings": [
                    {"table_name": "customers", "schema_name": "public", "key_column": "customer_id"}
                ],
            }
        ]
    }

    model, warnings = compile_draft_to_semantic_model(
        draft, tenant_id=_TENANT_ID, data_source_name=_DATA_SOURCE_NAME
    )

    assert warnings == []
    assert len(model.entities) == 1
    assert model.relationships == []
    assert model.metrics == []
    assert model.get_entity("Customer").synonyms == []
    assert model.get_entity("Customer").description is None


def test_version_is_honored_when_explicitly_passed() -> None:
    draft = {
        "entities": [
            {
                "name": "Customer",
                "bindings": [
                    {"table_name": "customers", "schema_name": "public", "key_column": "customer_id"}
                ],
            }
        ]
    }

    model, _ = compile_draft_to_semantic_model(
        draft, tenant_id=_TENANT_ID, data_source_name=_DATA_SOURCE_NAME, version=3
    )

    assert model.version == 3
