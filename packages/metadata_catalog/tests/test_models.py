"""Validate the SQLAlchemy model definitions themselves, DB-free.

These tests inspect `Base.metadata` directly (table names, column names/
types, FK constraints, unique constraints) rather than executing anything
against a live engine -- this tier stays fast and DB-free per the
established unit/integration split; the real-Postgres migration/schema
assertions live in `tests/integration/metadata_catalog/test_migrations.py`.
"""

from __future__ import annotations

from navigraph_catalog.models import Base
from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID


def test_expected_tables_exist() -> None:
    assert set(Base.metadata.tables) == {
        "data_sources",
        "catalog_schemas",
        "catalog_tables",
        "catalog_columns",
        "column_glossary",
        "semantic_models",
        "tenant_identity_configs",
    }


def test_data_sources_columns() -> None:
    table = Base.metadata.tables["data_sources"]
    columns = table.columns

    assert isinstance(columns["id"].type, UUID)
    assert columns["id"].primary_key

    assert not columns["tenant_id"].nullable
    assert columns["tenant_id"].index

    assert not columns["name"].nullable
    assert not columns["source_type"].nullable

    assert isinstance(columns["connection_ref"].type, JSONB)
    assert not columns["connection_ref"].nullable

    assert not columns["created_at"].nullable

    unique_constraints = {
        tuple(sorted(c.name for c in uc.columns))
        for uc in table.constraints
        if isinstance(uc, UniqueConstraint)
    }
    assert ("name", "tenant_id") in unique_constraints


def test_catalog_schemas_columns_and_fk() -> None:
    table = Base.metadata.tables["catalog_schemas"]
    columns = table.columns

    assert isinstance(columns["id"].type, UUID)
    assert columns["id"].primary_key
    assert not columns["name"].nullable
    assert not columns["data_source_id"].nullable

    fk = next(iter(columns["data_source_id"].foreign_keys))
    assert fk.column.table.name == "data_sources"
    assert fk.ondelete == "CASCADE"

    unique_constraints = {
        tuple(sorted(c.name for c in uc.columns))
        for uc in table.constraints
        if isinstance(uc, UniqueConstraint)
    }
    assert ("data_source_id", "name") in unique_constraints


def test_catalog_tables_columns_and_fk() -> None:
    table = Base.metadata.tables["catalog_tables"]
    columns = table.columns

    assert isinstance(columns["id"].type, UUID)
    assert not columns["name"].nullable
    assert columns["description"].nullable
    assert columns["row_count_estimate"].nullable
    assert not columns["schema_id"].nullable

    fk = next(iter(columns["schema_id"].foreign_keys))
    assert fk.column.table.name == "catalog_schemas"
    assert fk.ondelete == "CASCADE"

    unique_constraints = {
        tuple(sorted(c.name for c in uc.columns))
        for uc in table.constraints
        if isinstance(uc, UniqueConstraint)
    }
    assert ("name", "schema_id") in unique_constraints


def test_catalog_columns_columns_and_fk() -> None:
    table = Base.metadata.tables["catalog_columns"]
    columns = table.columns

    assert isinstance(columns["id"].type, UUID)
    assert not columns["name"].nullable
    assert not columns["data_type"].nullable
    assert not columns["nullable"].nullable
    assert not columns["ordinal_position"].nullable
    assert columns["description"].nullable
    assert not columns["table_id"].nullable

    fk = next(iter(columns["table_id"].foreign_keys))
    assert fk.column.table.name == "catalog_tables"
    assert fk.ondelete == "CASCADE"

    unique_constraints = {
        tuple(sorted(c.name for c in uc.columns))
        for uc in table.constraints
        if isinstance(uc, UniqueConstraint)
    }
    assert ("name", "table_id") in unique_constraints


def test_column_glossary_columns_and_fk() -> None:
    table = Base.metadata.tables["column_glossary"]
    columns = table.columns

    assert isinstance(columns["id"].type, UUID)
    assert columns["id"].primary_key

    assert not columns["column_id"].nullable
    assert not columns["business_name"].nullable

    assert isinstance(columns["synonyms"].type, JSONB)
    assert not columns["synonyms"].nullable

    assert columns["description"].nullable
    assert not columns["source"].nullable
    assert not columns["created_at"].nullable

    fk = next(iter(columns["column_id"].foreign_keys))
    assert fk.column.table.name == "catalog_columns"
    assert fk.ondelete == "CASCADE"

    unique_constraints = {
        tuple(sorted(c.name for c in uc.columns))
        for uc in table.constraints
        if isinstance(uc, UniqueConstraint)
    }
    assert ("column_id",) in unique_constraints


def test_semantic_models_columns() -> None:
    from sqlalchemy import Index

    table = Base.metadata.tables["semantic_models"]
    columns = table.columns

    assert isinstance(columns["id"].type, UUID)
    assert columns["id"].primary_key

    assert not columns["tenant_id"].nullable
    assert columns["tenant_id"].index

    assert not columns["version"].nullable
    assert isinstance(columns["compiled_json"].type, JSONB)
    assert not columns["compiled_json"].nullable
    assert columns["activated_at"].nullable
    assert not columns["created_at"].nullable

    unique_constraints = {
        tuple(sorted(c.name for c in uc.columns))
        for uc in table.constraints
        if isinstance(uc, UniqueConstraint)
    }
    assert ("tenant_id", "version") in unique_constraints

    active_index = next(
        idx for idx in table.indexes if idx.name == "uq_semantic_models_tenant_active"
    )
    assert isinstance(active_index, Index)
    assert active_index.unique


def test_tenant_identity_configs_columns() -> None:
    table = Base.metadata.tables["tenant_identity_configs"]
    columns = table.columns

    assert isinstance(columns["id"].type, UUID)
    assert columns["id"].primary_key

    assert not columns["tenant_id"].nullable
    assert columns["tenant_id"].index

    assert not columns["provider_type"].nullable
    assert isinstance(columns["provider_settings"].type, JSONB)
    assert not columns["provider_settings"].nullable
    assert not columns["created_at"].nullable

    unique_constraints = {
        tuple(sorted(c.name for c in uc.columns))
        for uc in table.constraints
        if isinstance(uc, UniqueConstraint)
    }
    assert ("tenant_id",) in unique_constraints


def test_relationships_navigate_parent_to_child() -> None:
    from navigraph_catalog.models import (
        CatalogColumn,
        CatalogSchema,
        CatalogTable,
        DataSource,
    )

    assert "schemas" in DataSource.__mapper__.relationships
    assert "tables" in CatalogSchema.__mapper__.relationships
    assert "columns" in CatalogTable.__mapper__.relationships
    assert "glossary_entry" in CatalogColumn.__mapper__.relationships
