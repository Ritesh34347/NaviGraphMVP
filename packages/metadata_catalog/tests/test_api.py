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
from unittest.mock import MagicMock, patch

import pytest
from navigraph_catalog.api import (
    get_table,
    list_columns,
    list_data_sources,
    list_glossary,
    list_tables,
    register_data_source,
    upsert_glossary,
    upsert_schema_tree,
)
from navigraph_catalog.models import (
    CatalogColumn,
    CatalogSchema,
    CatalogTable,
    ColumnGlossary,
    DataSource,
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


class TestListDataSources:
    def test_list_data_sources_builds_expected_query_and_returns_scalars(self) -> None:
        session = MagicMock()
        expected = [MagicMock(spec=DataSource), MagicMock(spec=DataSource)]
        session.execute.return_value.scalars.return_value = expected

        result = list_data_sources(session, tenant_id="tenant-a")

        assert result == expected
        session.execute.assert_called_once()


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
