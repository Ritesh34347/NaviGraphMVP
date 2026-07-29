"""SQLAlchemy 2.0 declarative models for the metadata catalog.

Four tables, one strict parent/child chain, matching the crawl shape a
`navigraph_connectors.base.Connector.introspect_schema()` call returns:

    DataSource -> CatalogSchema -> CatalogTable -> CatalogColumn

This is deliberately RAW schema structure only -- no business glossary, no
ontology/semantic mapping, no embedding fields. That is a later phase's
responsibility, not this package's.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for every metadata-catalog table."""


class DataSource(Base):
    """A registered external data source (e.g. one Snowflake account).

    `source_type` is validated at the `navigraph_catalog.api` layer against
    `navigraph_connectors.registry.get_connector_class`, not by a DB
    constraint/enum -- registering a new connector's source type should
    never require a migration here.
    """

    __tablename__ = "data_sources"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_data_sources_tenant_name"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[str] = mapped_column(index=True, nullable=False)
    name: Mapped[str] = mapped_column(nullable=False)
    source_type: Mapped[str] = mapped_column(nullable=False)
    # Opaque pointer to where real connection details live (e.g. a secrets
    # manager path or an env-var prefix like {"env_prefix": "SNOWFLAKE"}) --
    # NEVER raw credentials. This column must never contain a password,
    # private key, token, or any other secret material.
    connection_ref: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    schemas: Mapped[list[CatalogSchema]] = relationship(
        back_populates="data_source",
        cascade="all, delete-orphan",
    )


class CatalogSchema(Base):
    """One schema (namespace) within a data source."""

    __tablename__ = "catalog_schemas"
    __table_args__ = (
        UniqueConstraint("data_source_id", "name", name="uq_catalog_schemas_source_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(nullable=False)

    data_source: Mapped[DataSource] = relationship(back_populates="schemas")
    tables: Mapped[list[CatalogTable]] = relationship(
        back_populates="schema",
        cascade="all, delete-orphan",
    )


class CatalogTable(Base):
    """One table (or view) within a schema."""

    __tablename__ = "catalog_tables"
    __table_args__ = (UniqueConstraint("schema_id", "name", name="uq_catalog_tables_schema_name"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    schema_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog_schemas.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(nullable=False)
    description: Mapped[str | None] = mapped_column(default=None)
    row_count_estimate: Mapped[int | None] = mapped_column(default=None)

    schema: Mapped[CatalogSchema] = relationship(back_populates="tables")
    columns: Mapped[list[CatalogColumn]] = relationship(
        back_populates="table",
        cascade="all, delete-orphan",
    )


class CatalogColumn(Base):
    """One column of a `CatalogTable`."""

    __tablename__ = "catalog_columns"
    __table_args__ = (UniqueConstraint("table_id", "name", name="uq_catalog_columns_table_name"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog_tables.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(nullable=False)
    data_type: Mapped[str] = mapped_column(nullable=False)
    nullable: Mapped[bool] = mapped_column(nullable=False)
    ordinal_position: Mapped[int] = mapped_column(nullable=False)
    description: Mapped[str | None] = mapped_column(default=None)

    table: Mapped[CatalogTable] = relationship(back_populates="columns")
    glossary_entry: Mapped[ColumnGlossary | None] = relationship(
        back_populates="column",
        cascade="all, delete-orphan",
    )


class ColumnGlossary(Base):
    """A business glossary entry for a single `CatalogColumn`.

    One-to-one with `CatalogColumn` (enforced by the `unique=True` FK below)
    -- `column_id` is the natural key `upsert_glossary` matches on. Deliberately
    separate from `CatalogColumn` itself (rather than extra nullable columns
    on it) so that raw crawled structure and business-glossary enrichment stay
    two distinct concerns, each with its own lifecycle: a glossary entry can be
    re-enriched, re-sourced, or removed without touching the crawled schema
    row it annotates.
    """

    __tablename__ = "column_glossary"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    column_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog_columns.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    business_name: Mapped[str] = mapped_column(nullable=False)
    # List of synonym strings. The source data (e.g. `SCHEMA_ENRICHMENT`'s
    # `SYNONYMS` column) is a comma-separated string -- splitting it into this
    # list happens at ingestion time in the crawler, not stored raw here.
    synonyms: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    description: Mapped[str | None] = mapped_column(default=None)
    # e.g. "schema_enrichment" -- which glossary source produced this entry,
    # so future glossary sources (hand-curated, other tools) are distinguishable.
    source: Mapped[str] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    column: Mapped[CatalogColumn] = relationship(back_populates="glossary_entry")
