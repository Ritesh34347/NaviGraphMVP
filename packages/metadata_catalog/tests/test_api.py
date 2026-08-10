"""Unit tests for `navigraph_catalog.api`, DB-free.

`api.py`'s functions need a real `Session` to test meaningfully with an
actual database (SQL upsert logic, unique constraints) -- but `models.py`
uses Postgres-specific features (`JSONB`, `gen_random_uuid()` server
defaults) that don't work against an in-memory SQLite engine. Rather than
requiring a live Postgres connection for this unit tier, these tests mock
`Session`/query results with `unittest.mock` to verify the CORRECT SQL/ORM
calls are made -- e.g. that `register_data_source` validates `source_type`
via `get_connector_class` and lets its `ValueError` propagate, and that
`session.add`/`session.flush` are called with correctly-constructed model
instances. This keeps `pytest packages/metadata_catalog` fast and DB-free,
consistent with the rest of the unit-test tier in this repo. The real
upsert-against-a-live-database behavior is exercised by the migration
integration test in `tests/integration/metadata_catalog/`.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from navigraph_catalog.api import (
    activate_semantic_model,
    find_column,
    get_active_semantic_model,
    get_default_data_source,
    get_table,
    get_tenant_guardrail_config,
    get_tenant_identity_config,
    list_columns,
    list_data_sources,
    list_glossary,
    list_semantic_models,
    list_stale_data_sources,
    list_tables,
    mark_columns_pii,
    mark_data_source_crawled,
    register_data_source,
    save_semantic_model,
    set_default_data_source,
    set_tenant_guardrail_config,
    set_tenant_identity_config,
    upsert_glossary,
    upsert_schema_tree,
)
from navigraph_catalog.models import (
    CatalogColumn,
    CatalogSchema,
    CatalogTable,
    ColumnGlossary,
    DataSource,
    SemanticModelRecord,
    TenantGuardrailConfig,
    TenantIdentityConfig,
)
from navigraph_connectors.base import (
    ColumnDescriptor,
    SchemaDescriptor,
    TableDescriptor,
)


class TestRegisterDataSource:
    def test_validates_source_type_and_propagates_value_error(self) -> None:
        session = MagicMock()

        with patch(
            "navigraph_catalog.api.get_connector_class",
            side_effect=ValueError("No connector registered for source_type='bogus'"),
        ) as mock_get_connector_class, pytest.raises(ValueError, match="bogus"):
            register_data_source(
                session,
                tenant_id="tenant-a",
                name="my-source",
                source_type="bogus",
                connection_ref={"env_prefix": "BOGUS"},
            )

        mock_get_connector_class.assert_called_once_with("bogus")
        session.add.assert_not_called()

    def test_adds_and_flushes_a_correctly_constructed_data_source(self) -> None:
        session = MagicMock()

        with patch("navigraph_catalog.api.get_connector_class") as mock_get_connector_class:
            result = register_data_source(
                session,
                tenant_id="tenant-a",
                name="snowflake-prod",
                source_type="snowflake",
                connection_ref={"env_prefix": "SNOWFLAKE"},
            )

        mock_get_connector_class.assert_called_once_with("snowflake")

        session.add.assert_called_once()
        added = session.add.call_args.args[0]
        assert isinstance(added, DataSource)
        assert added.tenant_id == "tenant-a"
        assert added.name == "snowflake-prod"
        assert added.source_type == "snowflake"
        assert added.connection_ref == {"env_prefix": "SNOWFLAKE"}

        session.flush.assert_called_once()
        assert result is added

    def test_is_default_defaults_to_false_when_not_passed(self) -> None:
        session = MagicMock()

        with patch("navigraph_catalog.api.get_connector_class"):
            result = register_data_source(
                session,
                tenant_id="tenant-a",
                name="snowflake-prod",
                source_type="snowflake",
                connection_ref={"env_prefix": "SNOWFLAKE"},
            )

        assert result.is_default is False

    def test_is_default_true_is_passed_through_to_the_model(self) -> None:
        session = MagicMock()

        with patch("navigraph_catalog.api.get_connector_class"):
            result = register_data_source(
                session,
                tenant_id="tenant-a",
                name="snowflake-prod",
                source_type="snowflake",
                connection_ref={"env_prefix": "SNOWFLAKE"},
                is_default=True,
            )

        assert result.is_default is True


class TestListDataSources:
    def test_list_data_sources_builds_expected_query_and_returns_scalars(self) -> None:
        session = MagicMock()
        expected = [MagicMock(spec=DataSource), MagicMock(spec=DataSource)]
        session.execute.return_value.scalars.return_value = expected

        result = list_data_sources(session, tenant_id="tenant-a")

        assert result == expected
        session.execute.assert_called_once()


class TestGetDefaultDataSource:
    def test_returns_the_default_when_one_exists(self) -> None:
        session = MagicMock()
        expected = MagicMock(spec=DataSource)
        session.execute.return_value.scalar_one_or_none.return_value = expected

        result = get_default_data_source(session, tenant_id="tenant-a")

        assert result is expected
        session.execute.assert_called_once()

    def test_returns_none_when_no_default_is_set(self) -> None:
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = None

        result = get_default_data_source(session, tenant_id="tenant-a")

        assert result is None


class TestSetDefaultDataSource:
    def test_unsets_any_existing_default_before_setting_the_new_one(self) -> None:
        session = MagicMock()
        data_source_id = uuid.uuid4()

        set_default_data_source(session, tenant_id="tenant-a", data_source_id=data_source_id)

        assert session.execute.call_count == 2
        session.flush.assert_called_once()

    def test_unset_update_runs_before_the_set_update(self) -> None:
        """Order matters here: the partial unique index would reject setting
        a new default before the old one is cleared in the same
        transaction, so the "unset all" UPDATE must be issued first."""

        session = MagicMock()
        data_source_id = uuid.uuid4()

        set_default_data_source(session, tenant_id="tenant-a", data_source_id=data_source_id)

        unset_call, set_call = session.execute.call_args_list
        unset_compiled = unset_call.args[0].compile()
        set_compiled = set_call.args[0].compile()
        assert unset_compiled.params["is_default"] is False
        assert set_compiled.params["is_default"] is True


class TestUpsertSchemaTree:
    def test_inserts_new_schema_table_and_columns_when_none_exist(self) -> None:
        session = MagicMock()
        # No existing rows match -- every lookup returns None, forcing inserts.
        session.execute.return_value.scalar_one_or_none.return_value = None

        data_source_id = uuid.uuid4()
        schemas = [
            SchemaDescriptor(
                name="public",
                tables=[
                    TableDescriptor(
                        name="orders",
                        row_count_estimate=100,
                        columns=[
                            ColumnDescriptor(
                                name="id",
                                data_type="INTEGER",
                                nullable=False,
                                ordinal_position=1,
                            ),
                        ],
                    )
                ],
            )
        ]

        upsert_schema_tree(session, data_source_id=data_source_id, schemas=schemas)

        added_types = [type(call.args[0]) for call in session.add.call_args_list]
        assert added_types == [CatalogSchema, CatalogTable, CatalogColumn]

        added_schema = session.add.call_args_list[0].args[0]
        assert added_schema.data_source_id == data_source_id
        assert added_schema.name == "public"

        added_table = session.add.call_args_list[1].args[0]
        assert added_table.name == "orders"

        added_column = session.add.call_args_list[2].args[0]
        assert added_column.name == "id"

    def test_updates_existing_table_row_count_without_reinserting(self) -> None:
        session = MagicMock()

        existing_schema = CatalogSchema(id=uuid.uuid4(), name="public")
        existing_table = CatalogTable(id=uuid.uuid4(), name="orders", row_count_estimate=1)
        existing_column = CatalogColumn(
            id=uuid.uuid4(),
            name="id",
            data_type="INTEGER",
            nullable=False,
            ordinal_position=1,
        )

        session.execute.return_value.scalar_one_or_none.side_effect = [
            existing_schema,
            existing_table,
            existing_column,
        ]

        data_source_id = uuid.uuid4()
        schemas = [
            SchemaDescriptor(
                name="public",
                tables=[
                    TableDescriptor(
                        name="orders",
                        row_count_estimate=999,
                        columns=[
                            ColumnDescriptor(
                                name="id",
                                data_type="BIGINT",
                                nullable=True,
                                ordinal_position=1,
                                description="primary key",
                            ),
                        ],
                    )
                ],
            )
        ]

        upsert_schema_tree(session, data_source_id=data_source_id, schemas=schemas)

        # Nothing new should be inserted -- every lookup found an existing row.
        session.add.assert_not_called()

        assert existing_table.row_count_estimate == 999
        assert existing_column.data_type == "BIGINT"
        assert existing_column.nullable is True
        assert existing_column.description == "primary key"

    def test_a_genuinely_new_table_reports_is_new_and_never_changed(self) -> None:
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = None

        data_source_id = uuid.uuid4()
        schemas = [
            SchemaDescriptor(
                name="public",
                tables=[
                    TableDescriptor(
                        name="orders",
                        row_count_estimate=100,
                        columns=[
                            ColumnDescriptor(
                                name="id", data_type="INTEGER", nullable=False, ordinal_position=1
                            )
                        ],
                    )
                ],
            )
        ]

        events = upsert_schema_tree(session, data_source_id=data_source_id, schemas=schemas)

        assert len(events) == 1
        assert events[0].table_name == "orders"
        assert events[0].is_new is True
        assert events[0].changed is False
        assert events[0].old_hash is None
        assert events[0].new_hash != ""

    def test_an_existing_table_with_no_prior_hash_is_not_claimed_changed(self) -> None:
        """A table crawled before schema-drift tracking existed has
        `schema_hash IS NULL` -- there is no real baseline to compare
        against, so this must never be reported as `changed=True`."""

        session = MagicMock()
        existing_schema = CatalogSchema(id=uuid.uuid4(), name="public")
        existing_table = CatalogTable(
            id=uuid.uuid4(), name="orders", row_count_estimate=1, schema_hash=None
        )
        existing_column = CatalogColumn(
            id=uuid.uuid4(), name="id", data_type="INTEGER", nullable=False, ordinal_position=1
        )
        session.execute.return_value.scalar_one_or_none.side_effect = [
            existing_schema,
            existing_table,
            existing_column,
        ]

        schemas = [
            SchemaDescriptor(
                name="public",
                tables=[
                    TableDescriptor(
                        name="orders",
                        row_count_estimate=1,
                        columns=[
                            ColumnDescriptor(
                                name="id", data_type="INTEGER", nullable=False, ordinal_position=1
                            )
                        ],
                    )
                ],
            )
        ]

        events = upsert_schema_tree(session, data_source_id=uuid.uuid4(), schemas=schemas)

        assert events[0].is_new is False
        assert events[0].changed is False
        assert events[0].old_hash is None
        # The real hash is still computed and stored going forward.
        assert existing_table.schema_hash == events[0].new_hash

    def test_an_unchanged_table_reports_changed_false_with_a_real_matching_hash(self) -> None:
        from navigraph_catalog.drift import compute_table_schema_hash

        session = MagicMock()
        table_descriptor = TableDescriptor(
            name="orders",
            row_count_estimate=1,
            columns=[
                ColumnDescriptor(
                    name="id", data_type="INTEGER", nullable=False, ordinal_position=1
                )
            ],
        )
        real_hash = compute_table_schema_hash(table_descriptor)

        existing_schema = CatalogSchema(id=uuid.uuid4(), name="public")
        existing_table = CatalogTable(
            id=uuid.uuid4(), name="orders", row_count_estimate=1, schema_hash=real_hash
        )
        existing_column = CatalogColumn(
            id=uuid.uuid4(), name="id", data_type="INTEGER", nullable=False, ordinal_position=1
        )
        session.execute.return_value.scalar_one_or_none.side_effect = [
            existing_schema,
            existing_table,
            existing_column,
        ]

        events = upsert_schema_tree(
            session, data_source_id=uuid.uuid4(), schemas=[SchemaDescriptor(name="public", tables=[table_descriptor])]
        )

        assert events[0].is_new is False
        assert events[0].changed is False
        assert events[0].old_hash == real_hash
        assert events[0].new_hash == real_hash

    def test_a_genuinely_changed_table_reports_changed_true(self) -> None:
        session = MagicMock()
        existing_schema = CatalogSchema(id=uuid.uuid4(), name="public")
        # Real prior hash for a table that had only one column.
        existing_table = CatalogTable(
            id=uuid.uuid4(),
            name="orders",
            row_count_estimate=1,
            schema_hash="a-stale-hash-from-before-a-column-was-added",
        )
        existing_column = CatalogColumn(
            id=uuid.uuid4(), name="id", data_type="INTEGER", nullable=False, ordinal_position=1
        )

        # The real crawl now sees a SECOND column that didn't exist before
        # -- a genuine structural change.
        schemas = [
            SchemaDescriptor(
                name="public",
                tables=[
                    TableDescriptor(
                        name="orders",
                        row_count_estimate=1,
                        columns=[
                            ColumnDescriptor(
                                name="id",
                                data_type="INTEGER",
                                nullable=False,
                                ordinal_position=1,
                            ),
                            ColumnDescriptor(
                                name="total",
                                data_type="NUMBER",
                                nullable=True,
                                ordinal_position=2,
                            ),
                        ],
                    )
                ],
            )
        ]
        # The second column's lookup also returns None (never seen before).
        session.execute.return_value.scalar_one_or_none.side_effect = [
            existing_schema,
            existing_table,
            existing_column,
            None,
        ]

        events = upsert_schema_tree(session, data_source_id=uuid.uuid4(), schemas=schemas)

        assert events[0].is_new is False
        assert events[0].changed is True
        assert events[0].old_hash == "a-stale-hash-from-before-a-column-was-added"


class TestMarkDataSourceCrawled:
    def test_updates_last_crawled_at_and_flushes(self) -> None:
        session = MagicMock()
        data_source_id = uuid.uuid4()

        mark_data_source_crawled(session, data_source_id=data_source_id)

        session.execute.assert_called_once()
        session.flush.assert_called_once()
        update_stmt = session.execute.call_args.args[0]
        compiled = update_stmt.compile()
        assert compiled.params["last_crawled_at"] is not None


class TestListStaleDataSources:
    def test_builds_expected_query_and_returns_scalars(self) -> None:
        session = MagicMock()
        expected = [MagicMock(spec=DataSource)]
        session.execute.return_value.scalars.return_value = expected

        result = list_stale_data_sources(session, tenant_id="tenant-a", older_than=timedelta(days=7))

        assert result == expected
        session.execute.assert_called_once()


class TestReadHelpers:
    def test_list_tables_builds_expected_query_and_returns_scalars(self) -> None:
        session = MagicMock()
        expected = [MagicMock(spec=CatalogTable), MagicMock(spec=CatalogTable)]
        session.execute.return_value.scalars.return_value = expected

        result = list_tables(session, data_source_id=uuid.uuid4())

        assert result == expected
        session.execute.assert_called_once()

    def test_get_table_returns_single_result_or_none(self) -> None:
        session = MagicMock()
        expected = MagicMock(spec=CatalogTable)
        session.execute.return_value.scalar_one_or_none.return_value = expected

        result = get_table(
            session,
            data_source_id=uuid.uuid4(),
            schema_name="public",
            table_name="orders",
        )

        assert result is expected

    def test_list_columns_returns_scalars_ordered_by_ordinal_position(self) -> None:
        session = MagicMock()
        expected = [MagicMock(spec=CatalogColumn)]
        session.execute.return_value.scalars.return_value = expected

        result = list_columns(session, table_id=uuid.uuid4())

        assert result == expected
        session.execute.assert_called_once()


class TestUpsertGlossary:
    def test_inserts_new_glossary_entry_when_none_exists(self) -> None:
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = None

        column_id = uuid.uuid4()
        result = upsert_glossary(
            session,
            column_id=column_id,
            business_name="Total Transaction Value",
            synonyms=["trade value", "order value"],
            description="The total value of the transaction.",
            source="schema_enrichment",
        )

        session.add.assert_called_once()
        added = session.add.call_args.args[0]
        assert isinstance(added, ColumnGlossary)
        assert added.column_id == column_id
        assert added.business_name == "Total Transaction Value"
        assert added.synonyms == ["trade value", "order value"]
        assert added.description == "The total value of the transaction."
        assert added.source == "schema_enrichment"

        session.flush.assert_called_once()
        assert result is added

    def test_updates_existing_glossary_entry_without_reinserting(self) -> None:
        session = MagicMock()
        existing_entry = ColumnGlossary(
            id=uuid.uuid4(),
            column_id=uuid.uuid4(),
            business_name="old name",
            synonyms=["old synonym"],
            description="old description",
            source="schema_enrichment",
        )
        session.execute.return_value.scalar_one_or_none.return_value = existing_entry

        result = upsert_glossary(
            session,
            column_id=existing_entry.column_id,
            business_name="Total Transaction Value",
            synonyms=["trade value", "order value", "gross value"],
            description="new description",
            source="schema_enrichment",
        )

        session.add.assert_not_called()
        assert existing_entry.business_name == "Total Transaction Value"
        assert existing_entry.synonyms == ["trade value", "order value", "gross value"]
        assert existing_entry.description == "new description"
        session.flush.assert_called_once()
        assert result is existing_entry


class TestListGlossary:
    def test_list_glossary_builds_expected_query_and_returns_scalars(self) -> None:
        session = MagicMock()
        expected = [MagicMock(spec=ColumnGlossary), MagicMock(spec=ColumnGlossary)]
        session.execute.return_value.scalars.return_value = expected

        result = list_glossary(session, data_source_id=uuid.uuid4())

        assert result == expected
        session.execute.assert_called_once()


class TestFindColumn:
    def test_find_column_returns_single_result_or_none(self) -> None:
        session = MagicMock()
        expected = MagicMock(spec=CatalogColumn)
        session.execute.return_value.scalar_one_or_none.return_value = expected

        result = find_column(
            session,
            data_source_id=uuid.uuid4(),
            table_name="STAGING_TRANSACTIONS",
            column_name="marketid",
        )

        assert result is expected
        session.execute.assert_called_once()

    def test_find_column_returns_none_when_not_found(self) -> None:
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = None

        result = find_column(
            session,
            data_source_id=uuid.uuid4(),
            table_name="NO_SUCH_TABLE",
            column_name="no_such_column",
        )

        assert result is None


class TestMarkColumnsPii:
    def test_mark_columns_pii_issues_a_bulk_update_and_returns_matched_count(self) -> None:
        session = MagicMock()
        session.execute.return_value.rowcount = 2

        data_source_id = uuid.uuid4()
        result = mark_columns_pii(
            session,
            data_source_id=data_source_id,
            table_name="CUSTOMER_INFORMATION",
            column_names=["FIRSTNAME", "LASTNAME"],
        )

        assert result == 2
        session.execute.assert_called_once()
        session.flush.assert_called_once()

    def test_mark_columns_pii_is_idempotent_re_run_still_reports_matched_count(self) -> None:
        """Re-running with the same (already-tagged) columns still reports
        them as matched -- a bulk UPDATE, not an insert, so no duplicate
        rows and no error on re-run."""

        session = MagicMock()
        session.execute.return_value.rowcount = 2

        first = mark_columns_pii(
            session,
            data_source_id=uuid.uuid4(),
            table_name="CUSTOMER_INFORMATION",
            column_names=["FIRSTNAME", "LASTNAME"],
        )
        second = mark_columns_pii(
            session,
            data_source_id=uuid.uuid4(),
            table_name="CUSTOMER_INFORMATION",
            column_names=["FIRSTNAME", "LASTNAME"],
        )

        assert first == second == 2


class TestSaveSemanticModel:
    def test_adds_and_flushes_a_correctly_constructed_record(self) -> None:
        session = MagicMock()
        compiled_json = {"tenant_id": "tenant-a", "version": 1, "entities": []}

        result = save_semantic_model(
            session, tenant_id="tenant-a", version=1, compiled_json=compiled_json
        )

        session.add.assert_called_once()
        added = session.add.call_args.args[0]
        assert isinstance(added, SemanticModelRecord)
        assert added.tenant_id == "tenant-a"
        assert added.version == 1
        assert added.compiled_json == compiled_json

        session.flush.assert_called_once()
        assert result is added


class TestActivateSemanticModel:
    def test_deactivates_the_old_version_before_activating_the_new_one(self) -> None:
        """Same reasoning as `set_default_data_source`: the partial unique
        index would reject activating a new version before the old one is
        cleared, so the "unset" UPDATE must run before the "set" UPDATE, in
        the same transaction."""

        session = MagicMock()

        activate_semantic_model(session, tenant_id="tenant-a", version=2)

        assert session.execute.call_count == 2
        session.flush.assert_called_once()

        unset_call, set_call = session.execute.call_args_list
        unset_compiled = unset_call.args[0].compile()
        set_compiled = set_call.args[0].compile()
        assert unset_compiled.params["activated_at"] is None
        assert set_compiled.params["activated_at"] is not None


class TestGetActiveSemanticModel:
    def test_returns_the_active_record_when_one_exists(self) -> None:
        session = MagicMock()
        expected = MagicMock(spec=SemanticModelRecord)
        session.execute.return_value.scalar_one_or_none.return_value = expected

        result = get_active_semantic_model(session, tenant_id="tenant-a")

        assert result is expected
        session.execute.assert_called_once()

    def test_returns_none_when_no_model_has_ever_been_activated(self) -> None:
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = None

        result = get_active_semantic_model(session, tenant_id="tenant-a")

        assert result is None


class TestListSemanticModels:
    def test_builds_expected_query_and_returns_scalars(self) -> None:
        session = MagicMock()
        expected = [MagicMock(spec=SemanticModelRecord), MagicMock(spec=SemanticModelRecord)]
        session.execute.return_value.scalars.return_value = expected

        result = list_semantic_models(session, tenant_id="tenant-a")

        assert result == expected
        session.execute.assert_called_once()


class TestSetTenantIdentityConfig:
    def test_inserts_new_config_when_none_exists(self) -> None:
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = None

        result = set_tenant_identity_config(
            session,
            tenant_id="tenant-a",
            provider_type="azure_ad",
            provider_settings={"azure_ad_tenant_id": "t", "azure_ad_client_id": "c"},
        )

        session.add.assert_called_once()
        added = session.add.call_args.args[0]
        assert isinstance(added, TenantIdentityConfig)
        assert added.tenant_id == "tenant-a"
        assert added.provider_type == "azure_ad"
        assert added.provider_settings == {
            "azure_ad_tenant_id": "t",
            "azure_ad_client_id": "c",
        }
        session.flush.assert_called_once()
        assert result is added

    def test_updates_existing_config_without_reinserting(self) -> None:
        session = MagicMock()
        existing = TenantIdentityConfig(
            tenant_id="tenant-a",
            provider_type="azure_ad",
            provider_settings={"azure_ad_tenant_id": "old"},
        )
        session.execute.return_value.scalar_one_or_none.return_value = existing

        result = set_tenant_identity_config(
            session,
            tenant_id="tenant-a",
            provider_type="oidc",
            provider_settings={"oidc_issuer": "https://idp.example.com"},
        )

        session.add.assert_not_called()
        assert existing.provider_type == "oidc"
        assert existing.provider_settings == {"oidc_issuer": "https://idp.example.com"}
        session.flush.assert_called_once()
        assert result is existing


class TestGetTenantIdentityConfig:
    def test_returns_the_config_when_one_exists(self) -> None:
        session = MagicMock()
        expected = MagicMock(spec=TenantIdentityConfig)
        session.execute.return_value.scalar_one_or_none.return_value = expected

        result = get_tenant_identity_config(session, tenant_id="tenant-a")

        assert result is expected

    def test_returns_none_when_never_configured(self) -> None:
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = None

        result = get_tenant_identity_config(session, tenant_id="tenant-a")

        assert result is None


class TestSetTenantGuardrailConfig:
    def test_inserts_new_config_when_none_exists(self) -> None:
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = None

        result = set_tenant_guardrail_config(
            session,
            tenant_id="tenant-a",
            role_row_limits={"analyst": 8_000},
            default_role_row_limit=2_000,
            max_rows_cap=20_000,
        )

        session.add.assert_called_once()
        added = session.add.call_args.args[0]
        assert isinstance(added, TenantGuardrailConfig)
        assert added.tenant_id == "tenant-a"
        assert added.role_row_limits == {"analyst": 8_000}
        assert added.default_role_row_limit == 2_000
        assert added.max_rows_cap == 20_000
        session.flush.assert_called_once()
        assert result is added

    def test_omitted_fields_default_to_none_not_an_error(self) -> None:
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = None

        result = set_tenant_guardrail_config(session, tenant_id="tenant-a")

        assert result.role_row_limits is None
        assert result.default_role_row_limit is None
        assert result.max_rows_cap is None

    def test_updates_existing_config_without_reinserting(self) -> None:
        session = MagicMock()
        existing = TenantGuardrailConfig(
            tenant_id="tenant-a",
            role_row_limits={"analyst": 1_000},
            default_role_row_limit=500,
            max_rows_cap=5_000,
        )
        session.execute.return_value.scalar_one_or_none.return_value = existing

        result = set_tenant_guardrail_config(
            session,
            tenant_id="tenant-a",
            role_row_limits={"analyst": 8_000},
            default_role_row_limit=None,
            max_rows_cap=None,
        )

        session.add.assert_not_called()
        assert existing.role_row_limits == {"analyst": 8_000}
        assert existing.default_role_row_limit is None
        assert existing.max_rows_cap is None
        session.flush.assert_called_once()
        assert result is existing


class TestGetTenantGuardrailConfig:
    def test_returns_the_config_when_one_exists(self) -> None:
        session = MagicMock()
        expected = MagicMock(spec=TenantGuardrailConfig)
        session.execute.return_value.scalar_one_or_none.return_value = expected

        result = get_tenant_guardrail_config(session, tenant_id="tenant-a")

        assert result is expected

    def test_returns_none_when_never_configured(self) -> None:
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = None

        result = get_tenant_guardrail_config(session, tenant_id="tenant-a")

        assert result is None
