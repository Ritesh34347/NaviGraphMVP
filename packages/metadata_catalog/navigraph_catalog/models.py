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

from sqlalchemy import ForeignKey, Index, UniqueConstraint, func, text
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
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_data_sources_tenant_name"),
        # Enforces "at most one default per tenant" at the DB level, not
        # just in application code -- a partial unique index (only rows
        # where is_default is true) rather than a plain unique constraint,
        # since a plain one would also forbid more than one NON-default row
        # per tenant, which is the normal case. Resolves LIMITATIONS.md
        # items 26/42: real DataSource duplication for one tenant with no
        # resolution order, and no `is_default` concept at all.
        Index(
            "uq_data_sources_tenant_default",
            "tenant_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[str] = mapped_column(index=True, nullable=False)
    name: Mapped[str] = mapped_column(nullable=False)
    source_type: Mapped[str] = mapped_column(nullable=False)
    # Opaque pointer to where real connection details live (e.g.
    # {"secret_scope": "navikenz_poc_snowflake"}, resolved via a real
    # navigraph_shared.secrets.SecretsProvider -- see
    # navigraph_connectors.registry.build_connector) -- NEVER raw
    # credentials. This column must never contain a password, private key,
    # token, or any other secret material.
    connection_ref: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Which of this tenant's (possibly several) registered DataSource rows
    # the Request Orchestrator should resolve to when a caller omits an
    # explicit data_source_id and more than one is registered. Defaults
    # false; at most one row per tenant may be true (see the partial unique
    # index above). Resolves LIMITATIONS.md items 26/42.
    is_default: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=text("false")
    )
    # When this DataSource's schema structure was last successfully
    # crawled (`navigraph_catalog.ingestion.snowflake_crawler
    # .crawl_and_store` -> `mark_data_source_crawled`) -- `NULL` for a
    # freshly-registered DataSource that has never been crawled at all.
    # This is the real signal a re-crawl scheduler needs to answer "how
    # stale is this" (Phase 13, LIMITATIONS.md item 61's "still open"
    # re-validation-scheduler bullet).
    last_crawled_at: Mapped[datetime | None] = mapped_column(default=None)
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
    # A stable structural hash of this table's real shape (see
    # `navigraph_catalog.drift.compute_table_schema_hash`) -- `NULL` for a
    # table that predates schema-drift tracking and hasn't been re-crawled
    # since. Compared against the newly-computed hash on every
    # `upsert_schema_tree` call to produce a real `SchemaDriftEvent`,
    # rather than upserting blindly with no signal of what changed.
    schema_hash: Mapped[str | None] = mapped_column(default=None)

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
    # Phase 6 (Guardrail domain): whether this column carries personally
    # identifiable information. Defaults false -- crawling never infers
    # this on its own; a real value is only ever set by a deliberate,
    # human-run backfill (see tools/scripts/tag_pii_columns.py), never
    # guessed from a naming heuristic at crawl time. Checked directly by
    # the PII Exposure Checker agent, not pushed through OPA/Rego -- see
    # DECISIONS.md for why these are two separate enforcement layers.
    is_pii: Mapped[bool] = mapped_column(nullable=False, default=False, server_default=text("false"))

    table: Mapped[CatalogTable] = relationship(back_populates="columns")
    glossary_entry: Mapped[ColumnGlossary | None] = relationship(
        back_populates="column",
        cascade="all, delete-orphan",
    )


class SemanticModelRecord(Base):
    """One persisted, versioned snapshot of a tenant's compiled
    `navigraph_semantic_model.contracts.SemanticModel`.

    Named `*Record` (not `SemanticModel`) to avoid colliding with the
    Pydantic model of that name in `packages/semantic_model` -- this is the
    storage row; `navigraph_semantic_model.contracts.SemanticModel` is the
    validated document it holds (`compiled_json`, that model's own
    `model_dump()`). `navigraph_kg.ingestion.pipeline` reads the one
    activated row per tenant as its real source of truth for which
    relationships to sync, replacing `navigraph_kg.ontology
    .RELATIONSHIP_CONCEPTS`'s hardcoded list for any tenant that has
    onboarded one.
    """

    __tablename__ = "semantic_models"
    __table_args__ = (
        UniqueConstraint("tenant_id", "version", name="uq_semantic_models_tenant_version"),
        # At most one ACTIVATED version per tenant at a time -- mirrors
        # data_sources.uq_data_sources_tenant_default's partial-unique-index
        # pattern exactly. Many inactive (draft/superseded) versions may
        # coexist; activating a new one deactivates whichever was active.
        Index(
            "uq_semantic_models_tenant_active",
            "tenant_id",
            unique=True,
            postgresql_where=text("activated_at IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[str] = mapped_column(index=True, nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    compiled_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # NULL until deliberately activated via `api.activate_semantic_model` --
    # a freshly-saved version is a draft, not yet live for ingestion.
    activated_at: Mapped[datetime | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class TenantIdentityConfig(Base):
    """Which identity-verification provider a tenant uses, and that
    provider's own (non-secret) settings -- e.g. an Azure AD tenant/client
    ID pair, or a generic OIDC issuer/audience/claim-name mapping (Phase 4
    of the configurable-platform build plan).

    `provider_settings` is real, verifiable-against configuration (issuer
    URLs, client/audience IDs), never a credential -- unlike
    `DataSource.connection_ref`'s opaque secret-scope POINTER, there is no
    secret material an OIDC/Azure AD verifier needs stored here at all
    (JWT verification only needs PUBLIC signing keys, fetched live from
    the provider's own JWKS endpoint).

    At most one row per tenant -- a tenant has exactly one identity
    provider configured at a time (or none, meaning the gateway falls
    back to its process-wide default verifier). No versioning/activation
    dance like `SemanticModelRecord`'s: replacing a tenant's provider is a
    real UPDATE, not a new row, since there's no "previous provider" a
    rollback would ever want to reactivate.
    """

    __tablename__ = "tenant_identity_configs"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_tenant_identity_configs_tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[str] = mapped_column(index=True, nullable=False)
    provider_type: Mapped[str] = mapped_column(nullable=False)
    provider_settings: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class TenantGuardrailConfig(Base):
    """Per-tenant overrides for Guardrail agent thresholds -- Phase 5 of
    the configurable-platform build plan. Starts with
    `query_cost_estimator`'s row limits only
    (`ROLE_ROW_LIMITS`/`DEFAULT_ROLE_ROW_LIMIT`/`MAX_ROWS_CAP`) --
    deliberately NOT a generic "any agent, any threshold" blob; extending
    this to another agent's thresholds is a real, separate, later
    decision, not something this table's shape should have to
    anticipate speculatively.

    Every column is NULLABLE and deliberately additive-only: a tenant
    with no row here, or a row with some/all fields `NULL`, gets EXACTLY
    `QueryCostEstimatorAgent`'s hardcoded default behavior -- never an
    error, never a fabricated value. `role_row_limits` is a PARTIAL
    override, merged over the hardcoded `ROLE_ROW_LIMITS` dict by the
    agent (a tenant overriding just `"analyst"` doesn't need to also
    repeat `"admin"`'s default). At most one row per tenant -- a real
    `UniqueConstraint`, mirroring `TenantIdentityConfig`'s exact
    convention -- no versioning, since there's no "previous threshold
    set" a rollback would ever want to reactivate.
    """

    __tablename__ = "tenant_guardrail_configs"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_tenant_guardrail_configs_tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[str] = mapped_column(index=True, nullable=False)
    role_row_limits: Mapped[dict | None] = mapped_column(JSONB, default=None)
    default_role_row_limit: Mapped[int | None] = mapped_column(default=None)
    max_rows_cap: Mapped[int | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


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
