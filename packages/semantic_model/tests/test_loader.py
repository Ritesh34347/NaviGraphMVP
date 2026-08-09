"""Unit tests for `navigraph_semantic_model.loader`, catalog-DB-free.

Mocks `navigraph_catalog.api`'s `list_data_sources`/`get_table`/
`find_column`/`mark_columns_pii` at the point they're imported into
`loader.py` -- mirrors `navigraph_agents`' established "mock the catalog
lookup layer, assert on shape" convention. A `MagicMock()` session is
passed through untouched (it's forwarded to the mocked functions, never
actually used by them).
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
import yaml

from navigraph_semantic_model.contracts import (
    ColumnSensitivity,
    Entity,
    EntityBinding,
    Metric,
    ReferenceLookup,
    Relationship,
    RelationshipBinding,
    SemanticModel,
)
from navigraph_semantic_model.loader import (
    SemanticModelValidationError,
    compile_sensitivity,
    load_and_validate_semantic_model,
    load_semantic_model,
    validate_semantic_model_against_catalog,
)

_LOADER_MODULE = "navigraph_semantic_model.loader"
_DATA_SOURCE_NAME = "fidelity_poc_snowflake_v2"


def _data_source() -> MagicMock:
    ds = MagicMock()
    ds.id = uuid.uuid4()
    ds.name = _DATA_SOURCE_NAME
    return ds


def _model(**overrides: object) -> SemanticModel:
    defaults: dict[str, object] = {
        "tenant_id": "navikenz-poc",
        "version": 1,
        "entities": [
            Entity(
                name="Customer",
                bindings=[
                    EntityBinding(
                        data_source=_DATA_SOURCE_NAME,
                        table="STAGING.STAGING_CUSTOMER_INFORMATION",
                        key="CUSTOMERID",
                    )
                ],
                sensitive_columns=[ColumnSensitivity(column="CUSTOMERID")],
            ),
            Entity(
                name="Asset",
                bindings=[
                    EntityBinding(
                        data_source=_DATA_SOURCE_NAME,
                        table="FAR_TRANS.ASSET_INFORMATION",
                        key="ISIN",
                    )
                ],
            ),
            Entity(
                name="Transaction",
                bindings=[
                    EntityBinding(
                        data_source=_DATA_SOURCE_NAME,
                        table="STAGING.STAGING_TRANSACTIONS",
                        key="TRANSACTIONID",
                    )
                ],
            ),
        ],
        "relationships": [
            Relationship(
                name="Customer holds Asset",
                subject="Customer",
                predicate="HOLDS",
                object="Asset",
                via=RelationshipBinding(
                    data_source=_DATA_SOURCE_NAME,
                    table="FAR_TRANS.CUSTOMER_ASSET_AGG",
                    subject_key="CUSTOMERID",
                    object_key="ISIN",
                ),
            )
        ],
        "metrics": [
            Metric(name="transaction_count", entity="Transaction", aggregation="COUNT"),
            Metric(name="total_units_traded", entity="Transaction", aggregation="SUM", column="UNITS"),
        ],
    }
    defaults.update(overrides)
    return SemanticModel(**defaults)


class TestLoadSemanticModel:
    def test_loads_from_a_dict(self) -> None:
        model = load_semantic_model(
            {
                "tenant_id": "navikenz-poc",
                "version": 1,
                "entities": [
                    {
                        "name": "Customer",
                        "bindings": [
                            {"data_source": "ds", "table": "S.T", "key": "ID"},
                        ],
                    }
                ],
            }
        )
        assert model.tenant_id == "navikenz-poc"

    def test_loads_from_a_yaml_file(self, tmp_path) -> None:
        path = tmp_path / "semantic_model.yaml"
        path.write_text(
            yaml.dump(
                {
                    "tenant_id": "navikenz-poc",
                    "version": 2,
                    "entities": [
                        {
                            "name": "Customer",
                            "bindings": [{"data_source": "ds", "table": "S.T", "key": "ID"}],
                        }
                    ],
                }
            )
        )
        model = load_semantic_model(path)
        assert model.version == 2


class TestValidateSemanticModelAgainstCatalog:
    def test_a_fully_resolvable_model_has_no_issues(self) -> None:
        model = _model()
        session = MagicMock()

        with (
            patch(f"{_LOADER_MODULE}.list_data_sources", return_value=[_data_source()]),
            patch(f"{_LOADER_MODULE}.get_table", return_value=MagicMock()),
            patch(f"{_LOADER_MODULE}.find_column", return_value=MagicMock()),
        ):
            issues = validate_semantic_model_against_catalog(model, session)

        assert issues == []

    def test_unregistered_data_source_is_an_issue(self) -> None:
        model = _model()
        session = MagicMock()

        with patch(f"{_LOADER_MODULE}.list_data_sources", return_value=[]):
            issues = validate_semantic_model_against_catalog(model, session)

        assert any("no DataSource named" in issue for issue in issues)

    def test_table_ref_without_a_dot_is_an_issue(self) -> None:
        model = _model(
            entities=[
                Entity(
                    name="Customer",
                    bindings=[
                        EntityBinding(
                            data_source=_DATA_SOURCE_NAME, table="NO_SCHEMA_HERE", key="ID"
                        )
                    ],
                )
            ],
            relationships=[],
            metrics=[],
        )
        session = MagicMock()

        with patch(f"{_LOADER_MODULE}.list_data_sources", return_value=[_data_source()]):
            issues = validate_semantic_model_against_catalog(model, session)

        assert any("SCHEMA.TABLE" in issue for issue in issues)

    def test_missing_table_is_an_issue(self) -> None:
        model = _model()
        session = MagicMock()

        with (
            patch(f"{_LOADER_MODULE}.list_data_sources", return_value=[_data_source()]),
            patch(f"{_LOADER_MODULE}.get_table", return_value=None),
        ):
            issues = validate_semantic_model_against_catalog(model, session)

        assert any("not found in data source" in issue for issue in issues)

    def test_missing_binding_key_column_is_an_issue(self) -> None:
        model = _model()
        session = MagicMock()

        with (
            patch(f"{_LOADER_MODULE}.list_data_sources", return_value=[_data_source()]),
            patch(f"{_LOADER_MODULE}.get_table", return_value=MagicMock()),
            patch(f"{_LOADER_MODULE}.find_column", return_value=None),
        ):
            issues = validate_semantic_model_against_catalog(model, session)

        # Every binding's key column is missing, plus the sensitive column,
        # the relationship's two join columns, and the metric's column.
        assert any("column 'CUSTOMERID' not found on" in issue for issue in issues)

    def test_sensitive_column_missing_from_every_binding_is_an_issue(self) -> None:
        model = _model(
            entities=[
                Entity(
                    name="Customer",
                    bindings=[
                        EntityBinding(
                            data_source=_DATA_SOURCE_NAME,
                            table="STAGING.STAGING_CUSTOMER_INFORMATION",
                            key="CUSTOMERID",
                        )
                    ],
                    sensitive_columns=[ColumnSensitivity(column="SSN")],
                )
            ],
            relationships=[],
            metrics=[],
        )
        session = MagicMock()

        def _find_column(session, *, data_source_id, table_name, column_name):
            # The binding's own key column resolves; the sensitive column does not.
            return MagicMock() if column_name == "CUSTOMERID" else None

        with (
            patch(f"{_LOADER_MODULE}.list_data_sources", return_value=[_data_source()]),
            patch(f"{_LOADER_MODULE}.get_table", return_value=MagicMock()),
            patch(f"{_LOADER_MODULE}.find_column", side_effect=_find_column),
        ):
            issues = validate_semantic_model_against_catalog(model, session)

        assert any("sensitive column 'SSN' not found" in issue for issue in issues)
        # The binding's own key column check must not ALSO spuriously fail.
        assert not any("column 'CUSTOMERID' not found" in issue for issue in issues)

    def test_metric_column_not_found_on_any_binding_is_an_issue(self) -> None:
        model = _model()
        session = MagicMock()

        def _find_column(session, *, data_source_id, table_name, column_name):
            return None if column_name == "UNITS" else MagicMock()

        with (
            patch(f"{_LOADER_MODULE}.list_data_sources", return_value=[_data_source()]),
            patch(f"{_LOADER_MODULE}.get_table", return_value=MagicMock()),
            patch(f"{_LOADER_MODULE}.find_column", side_effect=_find_column),
        ):
            issues = validate_semantic_model_against_catalog(model, session)

        assert any(
            "metric 'total_units_traded': column 'UNITS' not found" in issue for issue in issues
        )

    def test_reference_lookup_column_missing_is_an_issue(self) -> None:
        model = _model(reference_lookups=[
            ReferenceLookup(
                node_label="Channel",
                data_source=_DATA_SOURCE_NAME,
                table="FAR_TRANS.TRANSACTIONS",
                column="CHANNEL",
            )
        ])
        session = MagicMock()

        def _find_column(session, *, data_source_id, table_name, column_name):
            return None if column_name == "CHANNEL" else MagicMock()

        with (
            patch(f"{_LOADER_MODULE}.list_data_sources", return_value=[_data_source()]),
            patch(f"{_LOADER_MODULE}.get_table", return_value=MagicMock()),
            patch(f"{_LOADER_MODULE}.find_column", side_effect=_find_column),
        ):
            issues = validate_semantic_model_against_catalog(model, session)

        assert any("reference_lookup 'Channel'" in issue for issue in issues)

    def test_count_metric_with_no_column_is_never_checked(self) -> None:
        model = _model()
        session = MagicMock()

        with (
            patch(f"{_LOADER_MODULE}.list_data_sources", return_value=[_data_source()]),
            patch(f"{_LOADER_MODULE}.get_table", return_value=MagicMock()),
            patch(f"{_LOADER_MODULE}.find_column", return_value=MagicMock()) as mock_find_column,
        ):
            validate_semantic_model_against_catalog(model, session)

        # "transaction_count" (COUNT, no column) never triggers a find_column
        # call for a None column name.
        assert None not in [call.kwargs.get("column_name") for call in mock_find_column.call_args_list]


class TestLoadAndValidateSemanticModel:
    def test_raises_with_every_issue_when_catalog_validation_fails(self) -> None:
        model_dict = _model().model_dump()

        with (
            patch(f"{_LOADER_MODULE}.list_data_sources", return_value=[]),
            pytest.raises(SemanticModelValidationError) as exc_info,
        ):
            load_and_validate_semantic_model(model_dict, MagicMock())

        assert len(exc_info.value.issues) > 0

    def test_returns_the_model_when_catalog_validation_succeeds(self) -> None:
        model_dict = _model().model_dump()

        with (
            patch(f"{_LOADER_MODULE}.list_data_sources", return_value=[_data_source()]),
            patch(f"{_LOADER_MODULE}.get_table", return_value=MagicMock()),
            patch(f"{_LOADER_MODULE}.find_column", return_value=MagicMock()),
        ):
            result = load_and_validate_semantic_model(model_dict, MagicMock())

        assert isinstance(result, SemanticModel)
        assert result.tenant_id == "navikenz-poc"


class TestCompileSensitivity:
    def test_marks_only_entities_with_sensitive_columns_declared(self) -> None:
        model = _model()  # only "Customer" declares sensitive_columns
        session = MagicMock()

        with (
            patch(f"{_LOADER_MODULE}.list_data_sources", return_value=[_data_source()]),
            patch(f"{_LOADER_MODULE}.mark_columns_pii", return_value=1) as mock_mark,
        ):
            total = compile_sensitivity(model, session)

        assert total == 1
        mock_mark.assert_called_once()
        call = mock_mark.call_args
        assert call.kwargs["table_name"] == "STAGING_CUSTOMER_INFORMATION"
        assert call.kwargs["column_names"] == ["CUSTOMERID"]

    def test_unregistered_data_source_is_skipped_not_raised(self) -> None:
        model = _model()
        session = MagicMock()

        with (
            patch(f"{_LOADER_MODULE}.list_data_sources", return_value=[]),
            patch(f"{_LOADER_MODULE}.mark_columns_pii") as mock_mark,
        ):
            total = compile_sensitivity(model, session)

        assert total == 0
        mock_mark.assert_not_called()
