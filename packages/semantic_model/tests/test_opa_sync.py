"""Unit tests for `navigraph_semantic_model.opa_sync`, OPA-network-free.

Uses `navigraph_shared.opa.FakeOpaClient` (a real, shared test double, not
a bespoke mock) -- `tests/security/test_per_tenant_policy_bindings.py`
covers the real, live-OPA end of this same mechanism.
"""

from __future__ import annotations

import pytest
from navigraph_shared.opa import FakeOpaClient

from navigraph_semantic_model.contracts import Entity, EntityBinding, PolicyBindings, SemanticModel
from navigraph_semantic_model.opa_sync import compile_policy_bindings_document, sync_policy_bindings


def _model(**overrides: object) -> SemanticModel:
    defaults: dict[str, object] = {
        "tenant_id": "navikenz-poc",
        "version": 1,
        "entities": [
            Entity(
                name="Customer",
                bindings=[EntityBinding(data_source="ds", table="S.T", key="ID")],
            )
        ],
    }
    defaults.update(overrides)
    return SemanticModel(**defaults)


class TestCompilePolicyBindingsDocument:
    def test_compiles_the_declared_allowed_roles(self) -> None:
        model = _model(
            policy_bindings=PolicyBindings(allowed_roles=["analyst", "compliance_officer"])
        )

        document = compile_policy_bindings_document(model)

        assert document == {"allowed_roles": ["analyst", "compliance_officer"]}

    def test_compiles_the_default_allowed_roles_when_not_overridden(self) -> None:
        model = _model()

        document = compile_policy_bindings_document(model)

        assert document == {"allowed_roles": ["analyst", "pii_viewer", "admin"]}

    def test_compiles_an_empty_list_as_a_real_lockout_not_omitted(self) -> None:
        model = _model(policy_bindings=PolicyBindings(allowed_roles=[]))

        document = compile_policy_bindings_document(model)

        assert document == {"allowed_roles": []}


class TestSyncPolicyBindings:
    async def test_pushes_the_compiled_document_to_the_tenant_scoped_path(self) -> None:
        model = _model(
            tenant_id="navikenz-poc",
            policy_bindings=PolicyBindings(allowed_roles=["analyst"]),
        )
        opa_client = FakeOpaClient()

        await sync_policy_bindings(opa_client, model)

        assert opa_client.data_calls == [
            {
                "path": "navigraph/tenants/navikenz-poc",
                "document": {"allowed_roles": ["analyst"]},
            }
        ]

    async def test_propagates_a_real_opa_failure(self) -> None:
        model = _model()
        opa_client = FakeOpaClient(set_data_raise_exc=ConnectionError("opa unreachable"))

        with pytest.raises(ConnectionError, match="opa unreachable"):
            await sync_policy_bindings(opa_client, model)
