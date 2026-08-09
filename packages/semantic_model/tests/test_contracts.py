"""Unit tests for `SemanticModel` and its nested contracts -- fully
offline, no catalog/database involved (see `contracts.py`'s own docstring
for why this model must be constructible with zero DB dependency)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from navigraph_semantic_model.contracts import (
    Entity,
    EntityBinding,
    Metric,
    Relationship,
    RelationshipBinding,
    SemanticModel,
)


def _customer_entity() -> Entity:
    return Entity(
        name="Customer",
        bindings=[
            EntityBinding(
                data_source="fidelity_poc_snowflake_v2",
                table="STAGING.STAGING_CUSTOMER_INFORMATION",
                key="CUSTOMERID",
            )
        ],
    )


def _asset_entity() -> Entity:
    return Entity(
        name="Asset",
        bindings=[
            EntityBinding(
                data_source="fidelity_poc_snowflake_v2",
                table="FAR_TRANS.ASSET_INFORMATION",
                key="ISIN",
            )
        ],
    )


def _transaction_entity() -> Entity:
    return Entity(
        name="Transaction",
        bindings=[
            EntityBinding(
                data_source="fidelity_poc_snowflake_v2",
                table="STAGING.STAGING_TRANSACTIONS",
                key="TRANSACTIONID",
            )
        ],
    )


class TestSemanticModelConstruction:
    def test_a_minimal_valid_model_constructs(self) -> None:
        model = SemanticModel(tenant_id="navikenz-poc", version=1, entities=[_customer_entity()])

        assert model.tenant_id == "navikenz-poc"
        assert model.relationships == []
        assert model.metrics == []
        assert model.policy_bindings.allowed_roles == ["analyst", "pii_viewer", "admin"]

    def test_a_full_model_with_relationships_and_metrics_constructs(self) -> None:
        model = SemanticModel(
            tenant_id="navikenz-poc",
            version=3,
            entities=[_customer_entity(), _asset_entity(), _transaction_entity()],
            relationships=[
                Relationship(
                    name="Customer holds Asset",
                    subject="Customer",
                    predicate="HOLDS",
                    object="Asset",
                    via=RelationshipBinding(
                        data_source="fidelity_poc_snowflake_v2",
                        table="FAR_TRANS.CUSTOMER_ASSET_AGG",
                        subject_key="CUSTOMERID",
                        object_key="ISIN",
                    ),
                )
            ],
            metrics=[
                Metric(name="transaction_count", entity="Transaction", aggregation="COUNT"),
                Metric(
                    name="total_units_traded",
                    entity="Transaction",
                    aggregation="SUM",
                    column="UNITS",
                ),
            ],
        )

        assert len(model.entities) == 3
        assert model.get_entity("Asset").name == "Asset"

    def test_unknown_top_level_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SemanticModel.model_validate(
                {
                    "tenant_id": "navikenz-poc",
                    "version": 1,
                    "entities": [_customer_entity().model_dump()],
                    "not_a_real_field": True,
                }
            )

    def test_requires_at_least_one_entity(self) -> None:
        with pytest.raises(ValidationError):
            SemanticModel(tenant_id="navikenz-poc", version=1, entities=[])

    def test_duplicate_entity_names_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="unique"):
            SemanticModel(
                tenant_id="navikenz-poc",
                version=1,
                entities=[_customer_entity(), _customer_entity()],
            )

    def test_duplicate_relationship_names_are_rejected(self) -> None:
        relationship = Relationship(
            name="Customer holds Asset",
            subject="Customer",
            predicate="HOLDS",
            object="Asset",
            via=RelationshipBinding(
                data_source="fidelity_poc_snowflake_v2",
                table="FAR_TRANS.CUSTOMER_ASSET_AGG",
                subject_key="CUSTOMERID",
                object_key="ISIN",
            ),
        )
        with pytest.raises(ValidationError, match="unique"):
            SemanticModel(
                tenant_id="navikenz-poc",
                version=1,
                entities=[_customer_entity(), _asset_entity()],
                relationships=[relationship, relationship.model_copy()],
            )

    def test_relationship_referencing_undeclared_subject_entity_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="not a declared entity"):
            SemanticModel(
                tenant_id="navikenz-poc",
                version=1,
                entities=[_asset_entity()],
                relationships=[
                    Relationship(
                        name="Customer holds Asset",
                        subject="Customer",  # never declared
                        predicate="HOLDS",
                        object="Asset",
                        via=RelationshipBinding(
                            data_source="fidelity_poc_snowflake_v2",
                            table="FAR_TRANS.CUSTOMER_ASSET_AGG",
                            subject_key="CUSTOMERID",
                            object_key="ISIN",
                        ),
                    )
                ],
            )

    def test_metric_referencing_undeclared_entity_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="not a declared entity"):
            SemanticModel(
                tenant_id="navikenz-poc",
                version=1,
                entities=[_customer_entity()],
                metrics=[Metric(name="x", entity="Transaction", aggregation="COUNT")],
            )

    def test_get_entity_raises_key_error_for_unknown_name(self) -> None:
        model = SemanticModel(tenant_id="navikenz-poc", version=1, entities=[_customer_entity()])
        with pytest.raises(KeyError):
            model.get_entity("NoSuchEntity")


class TestMetric:
    def test_count_aggregation_may_omit_column(self) -> None:
        metric = Metric(name="transaction_count", entity="Transaction", aggregation="COUNT")
        assert metric.column is None

    @pytest.mark.parametrize("aggregation", ["SUM", "AVG", "MIN", "MAX"])
    def test_non_count_aggregation_requires_a_column(self, aggregation: str) -> None:
        with pytest.raises(ValidationError, match="'column' is required"):
            Metric(name="x", entity="Transaction", aggregation=aggregation)

    def test_non_count_aggregation_with_a_column_is_valid(self) -> None:
        metric = Metric(name="total_units_traded", entity="Transaction", aggregation="SUM", column="UNITS")
        assert metric.column == "UNITS"
