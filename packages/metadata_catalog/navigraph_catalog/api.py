"""Real catalog operations: register sources, crawl results in, read them back.

Every function here takes an already-open `Session` (dependency injection --
see `navigraph_catalog.db.session_scope` for how callers obtain one) rather
than creating its own. Functions `flush` where they need generated
PKs/relationships visible within the same transaction, but never `commit` --
that is the caller's `session_scope`'s job.
"""

from __future__ import annotations

import uuid

from navigraph_connectors.base import SchemaDescriptor
from navigraph_connectors.registry import get_connector_class
from sqlalchemy import select
from sqlalchemy.orm import Session

from navigraph_catalog.models import (
    CatalogColumn,
    CatalogSchema,
    CatalogTable,
    DataSource,
)


def register_data_source(
    session: Session,
    *,
    tenant_id: str,
    name: str,
    source_type: str,
    connection_ref: dict,
) -> DataSource:
    """Register a new data source after validating `source_type`.

    Validates `source_type` by calling
    `navigraph_connectors.registry.get_connector_class` first -- an
    unregistered `source_type` raises `ValueError`, which is allowed to
    propagate to the caller unchanged rather than being caught and
    re-wrapped.
    """

    get_connector_class(source_type)

    data_source = DataSource(
        tenant_id=tenant_id,
        name=name,
        source_type=source_type,
        connection_ref=connection_ref,
    )
    session.add(data_source)
    session.flush()
    return data_source


def upsert_schema_tree(
    session: Session,
    *,
    data_source_id: uuid.UUID,
    schemas: list[SchemaDescriptor],
) -> None:
    """Idempotently upsert a connector's `introspect_schema()` output.

    Matches existing rows by their unique-constraint fields (schema name
    within a data source, table name within a schema, column name within a
    table) and updates them in place; inserts rows that don't exist yet.
    Safe to call repeatedly for the same data source -- e.g. on every crawl
    -- without duplicating rows or leaving stale ones from a previous
    revision of a table/column untouched within this call's schemas.
    """

    for schema_descriptor in schemas:
        catalog_schema = session.execute(
            select(CatalogSchema).where(
                CatalogSchema.data_source_id == data_source_id,
                CatalogSchema.name == schema_descriptor.name,
            )
        ).scalar_one_or_none()

        if catalog_schema is None:
            catalog_schema = CatalogSchema(
                data_source_id=data_source_id,
                name=schema_descriptor.name,
            )
            session.add(catalog_schema)
            session.flush()

        for table_descriptor in schema_descriptor.tables:
            catalog_table = session.execute(
                select(CatalogTable).where(
                    CatalogTable.schema_id == catalog_schema.id,
                    CatalogTable.name == table_descriptor.name,
                )
            ).scalar_one_or_none()

            if catalog_table is None:
                catalog_table = CatalogTable(
                    schema_id=catalog_schema.id,
                    name=table_descriptor.name,
                )
                session.add(catalog_table)
                session.flush()

            catalog_table.row_count_estimate = table_descriptor.row_count_estimate

            for column_descriptor in table_descriptor.columns:
                catalog_column = session.execute(
                    select(CatalogColumn).where(
                        CatalogColumn.table_id == catalog_table.id,
                        CatalogColumn.name == column_descriptor.name,
                    )
                ).scalar_one_or_none()

                if catalog_column is None:
                    catalog_column = CatalogColumn(
                        table_id=catalog_table.id,
                        name=column_descriptor.name,
                    )
                    session.add(catalog_column)

                catalog_column.data_type = column_descriptor.data_type
                catalog_column.nullable = column_descriptor.nullable
                catalog_column.ordinal_position = column_descriptor.ordinal_position
                catalog_column.description = column_descriptor.description

            session.flush()


def list_tables(session: Session, *, data_source_id: uuid.UUID) -> list[CatalogTable]:
    """List every `CatalogTable` belonging to `data_source_id`, across all its schemas."""

    return list(
        session.execute(
            select(CatalogTable)
            .join(CatalogSchema, CatalogTable.schema_id == CatalogSchema.id)
            .where(CatalogSchema.data_source_id == data_source_id)
        ).scalars()
    )


def get_table(
    session: Session,
    *,
    data_source_id: uuid.UUID,
    schema_name: str,
    table_name: str,
) -> CatalogTable | None:
    """Look up a single table by data source + schema name + table name."""

    return session.execute(
        select(CatalogTable)
        .join(CatalogSchema, CatalogTable.schema_id == CatalogSchema.id)
        .where(
            CatalogSchema.data_source_id == data_source_id,
            CatalogSchema.name == schema_name,
            CatalogTable.name == table_name,
        )
    ).scalar_one_or_none()


def list_columns(session: Session, *, table_id: uuid.UUID) -> list[CatalogColumn]:
    """List every `CatalogColumn` belonging to `table_id`, ordered by their source ordinal position."""

    return list(
        session.execute(
            select(CatalogColumn)
            .where(CatalogColumn.table_id == table_id)
            .order_by(CatalogColumn.ordinal_position)
        ).scalars()
    )
