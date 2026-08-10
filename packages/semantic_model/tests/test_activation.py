"""Unit tests for `navigraph_semantic_model.activation`, DB- and
OPA-network-free.

Patches the catalog-level functions where `activation.py` imports them
(matching this repo's established "patch where imported" convention --
see e.g. `packages/knowledge_graph/tests/ingestion/test_pipeline.py`), and
uses `navigraph_shared.opa.FakeOpaClient` for the real OPA call.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from navigraph_shared.opa import FakeOpaClient

from navigraph_semantic_model.activation import activate_semantic_model
from navigraph_semantic_model.contracts import Entity, EntityBinding, SemanticModel
from navigraph_semantic_model.loader import SemanticModelValidationError


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


class TestActivateSemanticModel:
    async def test_validation_failure_raises_and_persists_nothing(self) -> None:
        model = _model()
        session = MagicMock()
        opa_client = FakeOpaClient()

        with (
            patch(
                "navigraph_semantic_model.activation.validate_semantic_model_against_catalog",
                return_value=["entity 'Customer' binding (ds, S.T): table not found"],
            ),
            patch("navigraph_semantic_model.activation.compile_sensitivity") as mock_tag,
            patch("navigraph_semantic_model.activation.save_semantic_model") as mock_save,
            patch(
                "navigraph_semantic_model.activation._mark_semantic_model_active"
            ) as mock_mark,
            pytest.raises(SemanticModelValidationError) as exc_info,
        ):
            await activate_semantic_model(model, session, opa_client)

        assert exc_info.value.issues == [
            "entity 'Customer' binding (ds, S.T): table not found"
        ]
        mock_tag.assert_not_called()
        mock_save.assert_not_called()
        mock_mark.assert_not_called()
        assert opa_client.data_calls == []

    async def test_validation_success_tags_persists_activates_and_syncs_opa_in_order(
        self,
    ) -> None:
        model = _model()
        session = MagicMock()
        opa_client = FakeOpaClient()
        call_order: list[str] = []

        async def _fake_sync(client: object, m: SemanticModel) -> None:
            call_order.append("sync_opa")

        with (
            patch(
                "navigraph_semantic_model.activation.validate_semantic_model_against_catalog",
                return_value=[],
            ),
            patch(
                "navigraph_semantic_model.activation.compile_sensitivity",
                side_effect=lambda *a, **k: call_order.append("tag") or 3,
            ),
            patch(
                "navigraph_semantic_model.activation.save_semantic_model",
                side_effect=lambda *a, **k: call_order.append("save"),
            ),
            patch(
                "navigraph_semantic_model.activation._mark_semantic_model_active",
                side_effect=lambda *a, **k: call_order.append("mark_active"),
            ),
            patch(
                "navigraph_semantic_model.activation.sync_policy_bindings",
                side_effect=_fake_sync,
            ),
        ):
            result = await activate_semantic_model(model, session, opa_client)

        assert call_order == ["tag", "save", "mark_active", "sync_opa"]
        assert result.tagged_pii_columns == 3
