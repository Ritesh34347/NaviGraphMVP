"""Real catalog operations: register sources, crawl results in, read them back.

Every function here takes an already-open `Session` (dependency injection --
see `navigraph_catalog.db.session_scope` for how callers obtain one) rather
than creating its own. Functions `flush` where they need generated
PKs/relationships visible within the same transaction, but never `commit` --
that is the caller's `session_scope`'s job.
"""

from __future__ import annotations

import uuid
from typing import cast

from navigraph_connectors.base import SchemaDescriptor
from navigraph_connectors.registry import get_connector_class
from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from navigraph_catalog.models import (
    CatalogColumn,
    CatalogSchema,
    CatalogTable,
    ColumnGlossary,
    DataSource,
)


def register_data_source(
    session: Session,
    *,
    tenant_id: str,
    name: str,
    source_type: str,
    connection_ref: dict,
    is_default: bool = False,
) -> DataSource:
    """Register a new data source after validating `source_type`.

    Validates `source_type` by calling
    `navigraph_connectors.registry.get_connector_class` first -- an
    unregistered `source_type` raises `ValueError`, which is allowed to
    propagate to the caller unchanged rather than being caught and
    re-wrapped.

    `is_default=True` here only makes sense when this is the FIRST
    `DataSource` registered for `tenant_id` -- registering a second
    `is_default=True` row raises a real `IntegrityError` from the partial
    unique index (`uq_data_sources_tenant_default`), by design: use
    `set_default_data_source` instead, which atomically unsets any
    existing default first.
    """

    get_connector_class(source_type)

    data_source = DataSource(
        tenant_id=tenant_id,
        name=name,
        source_type=source_type,
        connection_ref=connection_ref,
        is_default=is_default,
    )
    session.add(data_source)
    session.flush()
    return data_source


def list_data_sources(session: Session, *, tenant_id: str) -> list[DataSource]:
    """List every `DataSource` registered for `tenant_id`."""

    return list(
        session.execute(select(DataSource).where(DataSource.tenant_id == tenant_id)).scalars()
    )


def get_default_data_source(session: Session, *, tenant_id: str) -> DataSource | None:
    """Return `tenant_id`'s one default `DataSource`, or `None` if it has
    none marked (either zero registered, or several with no default set).
    """

    return session.execute(
        select(DataSource).where(DataSource.tenant_id == tenant_id, DataSource.is_default)
    ).scalar_one_or_none()


def set_default_data_source(
    session: Session, *, tenant_id: str, data_source_id: uuid.UUID
) -> None:
    """Atomically mark `data_source_id` as `tenant_id`'s one default
    `DataSource`, unsetting any previous default first.

    Two `UPDATE`s in the same transaction, not one -- the partial unique
    index (`uq_data_sources_tenant_default`, at most one `is_default=true`
    row per tenant) would reject setting a new default before the old one
    is cleared. Resolves LIMITATIONS.md items 26/42.
    """

    session.execute(
        update(DataSource)
        .where(DataSource.tenant_id == tenant_id, DataSource.is_default)
        .values(is_default=False)
    )
    session.execute(
        update(DataSource)
        .where(DataSource.id == data_source_id, DataSource.tenant_id == tenant_id)
        .values(is_default=True)
    )
    session.flush()


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


def upsert_glossary(
    session: Session,
    *,
    column_id: uuid.UUID,
    business_name: str,
    synonyms: list[str],
    description: str | None,
    source: str,
) -> ColumnGlossary:
    """Idempotently upsert a `ColumnGlossary` entry for `column_id`.

    Matches the existing row by `column_id` (its unique-constraint natural
    key) and updates it in place; inserts a new row when none exists yet.
    Mirrors `upsert_schema_tree`'s idempotent-upsert style so repeated crawls
    of the same glossary source never duplicate rows for the same column.
    """

    glossary_entry = session.execute(
        select(ColumnGlossary).where(ColumnGlossary.column_id == column_id)
    ).scalar_one_or_none()

    if glossary_entry is None:
        glossary_entry = ColumnGlossary(column_id=column_id)
        session.add(glossary_entry)

    glossary_entry.business_name = business_name
    glossary_entry.synonyms = synonyms
    glossary_entry.description = description
    glossary_entry.source = source

    session.flush()
    return glossary_entry


def find_column(
    session: Session,
    *,
    data_source_id: uuid.UUID,
    table_name: str,
    column_name: str,
) -> CatalogColumn | None:
    """Case-insensitive point lookup of a single column, scoped to a data
    source -- joins `CatalogColumn -> CatalogTable -> CatalogSchema ->
    DataSource`. Matches case-insensitively (real crawled Snowflake
    identifiers are typically uppercase, but a caller like SQL Generation's
    `GeneratedSql.referenced_tables`/`referenced_columns` is not guaranteed
    to match that case exactly) -- mirrors
    `DataSourceDiscoveryAgent._resolve_table_owners`' identical
    case-insensitive-matching rationale, at the SQL layer via `func.lower()`
    rather than fetching every row and comparing in Python, since this is a
    single targeted lookup, not a build-an-index-once pass over the whole
    catalog.

    Used by the Guardrail domain's Schema Constraint Validator and PII
    Exposure Checker agents -- both need to resolve a `(table_name,
    column_name)` pair from `GeneratedSql.referenced_tables`/
    `referenced_columns` back to the real `CatalogColumn` row it names.
    """

    return session.execute(
        select(CatalogColumn)
        .join(CatalogTable, CatalogColumn.table_id == CatalogTable.id)
        .join(CatalogSchema, CatalogTable.schema_id == CatalogSchema.id)
        .where(
            CatalogSchema.data_source_id == data_source_id,
            func.lower(CatalogTable.name) == table_name.lower(),
            func.lower(CatalogColumn.name) == column_name.lower(),
        )
    ).scalar_one_or_none()


def mark_columns_pii(
    session: Session,
    *,
    data_source_id: uuid.UUID,
    table_name: str,
    column_names: list[str],
) -> int:
    """Idempotently set `is_pii = true` on every column in `column_names`
    for `table_name`, scoped to `data_source_id`. Matches case-
    insensitively, same rationale as `find_column`. Safe to call repeatedly
    (a bulk `UPDATE`, not an insert) -- re-running the same backfill list
    is a no-op on rows already tagged. Returns the number of rows matched
    (not necessarily the number actually changed, since a row already
    `is_pii = true` still counts as matched) so
    `tools/scripts/tag_pii_columns.py` can report real, verifiable output
    rather than assuming success silently.
    """

    lowered_names = [name.lower() for name in column_names]

    table_ids = select(CatalogTable.id).where(
        CatalogTable.schema_id.in_(
            select(CatalogSchema.id).where(CatalogSchema.data_source_id == data_source_id)
        ),
        func.lower(CatalogTable.name) == table_name.lower(),
    )

    result = session.execute(
        update(CatalogColumn)
        .where(
            CatalogColumn.table_id.in_(table_ids),
            func.lower(CatalogColumn.name).in_(lowered_names),
        )
        .values(is_pii=True)
    )
    session.flush()
    return cast(CursorResult, result).rowcount


def list_glossary(session: Session, *, data_source_id: uuid.UUID) -> list[ColumnGlossary]:
    """List every `ColumnGlossary` entry belonging to `data_source_id`.

    Joins `ColumnGlossary -> CatalogColumn -> CatalogTable -> CatalogSchema ->
    DataSource` to find every glossary entry attached to a column that
    ultimately belongs to this data source.
    """

    return list(
        session.execute(
            select(ColumnGlossary)
            .join(CatalogColumn, ColumnGlossary.column_id == CatalogColumn.id)
            .join(CatalogTable, CatalogColumn.table_id == CatalogTable.id)
            .join(CatalogSchema, CatalogTable.schema_id == CatalogSchema.id)
            .where(CatalogSchema.data_source_id == data_source_id)
        ).scalars()
    )
