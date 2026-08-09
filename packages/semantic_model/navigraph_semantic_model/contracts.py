"""The Semantic Model contract.

A versioned, per-tenant, Pydantic-validated document that replaces three
things that were previously hardcoded Python/SQL/Rego, one per tenant:

- `navigraph_kg.ontology.RELATIONSHIP_CONCEPTS` (a hardcoded list of dicts)
  and `navigraph_kg.ingestion.reference_data_queries` (hardcoded Snowflake
  SQL) -- both replaced by `entities`/`relationships` below, which
  `navigraph_kg.ingestion.pipeline`'s reference-data and relationship-
  concept stages now compile from instead of importing directly.
- `sql_generation.agent._aggregation_function`'s data-type-plus-intent
  *heuristic* for choosing `SUM` vs `COUNT` -- replaced by `metrics`'
  explicit `aggregation` field, closing LIMITATIONS.md item 38 (the real
  Phase 8 `gq_002` bug: "how many transactions" produced a nonsensical
  `SUM` because nothing declared that transaction-counting should be a
  `COUNT`) structurally rather than refining the guess further.
- `infra/opa/policies/authz.rego`'s hardcoded `allowed_roles` set -- the
  one genuinely tenant-specific FACT baked into an otherwise tenant-
  agnostic policy file. `policy_bindings.allowed_roles` below is compiled
  into a per-tenant OPA data document instead (see
  `navigraph_semantic_model.opa_sync`), never a second Rego file per tenant.

Every model here follows this codebase's established contract discipline
(`AgentInput`/`AgentOutput`'s `ConfigDict(extra="forbid")`, Pydantic
validation on construction, no silently-accepted unknown fields) --
`load_semantic_model` (`loader.py`) is the only place a Semantic Model is
ever parsed from raw YAML/JSON, so malformed documents fail loudly there,
not deep inside whatever later consumes one.

This module intentionally has NO dependency on `navigraph_catalog` --
these are pure data shapes. Catalog-aware validation (does this binding
actually name a real, currently-crawled table/column) lives in
`loader.py`, which does depend on it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Aggregation = Literal["SUM", "COUNT", "AVG", "MIN", "MAX"]


class EntityBinding(BaseModel):
    """One physical location an `Entity` is realized at.

    An entity with more than one binding is realized across more than one
    `DataSource` (possibly a different `source_type` each) -- the
    mechanism that makes a single logical `Customer` entity resolvable
    against both a Postgres CRM table and a Snowflake transactions table,
    which is what Data Federation's existing (today only fake-backed)
    multi-source combine path exists to serve for real.
    """

    model_config = ConfigDict(extra="forbid")

    data_source: str
    # "SCHEMA.TABLE", matching the real crawled Snowflake identifier shape
    # this codebase already uses everywhere else (e.g. `far_trans.markets`)
    # -- split on the first "." to resolve `CatalogSchema.name`/
    # `CatalogTable.name` separately, see `loader.py`.
    table: str
    key: str


class ColumnSensitivity(BaseModel):
    """One column of an `Entity`'s bound table(s) that should be marked
    sensitive when this Semantic Model is compiled -- replaces manually
    running `tools/scripts/tag_pii_columns.py` per column list for every
    entity a Semantic Model already declares. A column not listed here is
    NOT touched either way (compiling a Semantic Model never clears an
    existing `is_pii=true` flag it doesn't mention -- see `loader.py`'s
    `compile_sensitivity` docstring for why that asymmetry is deliberate).
    """

    model_config = ConfigDict(extra="forbid")

    column: str
    classification: Literal["pii"] = "pii"


class Entity(BaseModel):
    """A business-meaningful concept (e.g. `Customer`, `Asset`) and where
    it physically lives."""

    model_config = ConfigDict(extra="forbid")

    name: str
    bindings: list[EntityBinding] = Field(min_length=1)
    synonyms: list[str] = Field(default_factory=list)
    description: str | None = None
    sensitive_columns: list[ColumnSensitivity] = Field(default_factory=list)


class RelationshipBinding(BaseModel):
    """The real table + join columns that realize a `Relationship` --
    mirrors `navigraph_kg.ontology.RELATIONSHIP_CONCEPTS`'
    `realizing_table`/`subject_key_column`/`object_key_column` fields
    exactly, now declared per-tenant data instead of a hardcoded Python
    dict shared (incorrectly) across every tenant."""

    model_config = ConfigDict(extra="forbid")

    data_source: str
    table: str
    subject_key: str
    object_key: str


class Relationship(BaseModel):
    """A named, directed relationship between two declared `Entity` names
    (e.g. "Customer holds Asset": subject=Customer, predicate=HOLDS,
    object=Asset), realized by one real join."""

    model_config = ConfigDict(extra="forbid")

    name: str
    subject: str
    predicate: str
    object: str
    via: RelationshipBinding


class Metric(BaseModel):
    """An explicitly-declared aggregation over one `Entity`'s bound
    column -- the field that makes `SUM` vs `COUNT` a declared fact
    instead of `sql_generation.agent._aggregation_function`'s inferred
    guess (LIMITATIONS.md item 38)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    entity: str
    aggregation: Aggregation
    # Required for every aggregation except COUNT, which may legitimately
    # mean "count matching rows" (`COUNT(*)`) rather than counting a
    # specific column's non-null values.
    column: str | None = None

    @model_validator(mode="after")
    def _column_required_unless_counting_rows(self) -> Metric:
        if self.aggregation != "COUNT" and self.column is None:
            raise ValueError(
                f"metric {self.name!r}: 'column' is required for aggregation "
                f"{self.aggregation!r} (only COUNT may omit it, meaning COUNT(*))"
            )
        return self


class PolicyBindings(BaseModel):
    """Tenant-specific policy FACTS -- compiled into a per-tenant OPA data
    document (`navigraph_semantic_model.opa_sync`), never a second Rego
    file. `infra/opa/policies/authz.rego`'s own policy LOGIC (deny-by-
    default, the RBAC/ABAC composition rule) stays generic and shared
    across every tenant regardless of what a given tenant's
    `allowed_roles` are."""

    model_config = ConfigDict(extra="forbid")

    allowed_roles: list[str] = Field(default_factory=lambda: ["analyst", "pii_viewer", "admin"])


class SemanticModel(BaseModel):
    """The complete, versioned, per-tenant Semantic Model document."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    version: int = Field(ge=1)
    entities: list[Entity] = Field(min_length=1)
    relationships: list[Relationship] = Field(default_factory=list)
    metrics: list[Metric] = Field(default_factory=list)
    policy_bindings: PolicyBindings = Field(default_factory=PolicyBindings)

    @model_validator(mode="after")
    def _internal_references_resolve(self) -> SemanticModel:
        """Structural validation only -- does every `Relationship`/`Metric`
        reference a declared `Entity` name, and are entity/relationship
        names unique. Whether a binding's `(data_source, table, key)`
        actually exists in the live catalog is `loader.py`'s job, not
        this model's -- this model must be constructible offline, with no
        database, so it can be unit-tested and hand-authored/reviewed
        before ever touching a real catalog.
        """

        entity_names = [e.name for e in self.entities]
        if len(entity_names) != len(set(entity_names)):
            raise ValueError("entity names must be unique within a Semantic Model")

        relationship_names = [r.name for r in self.relationships]
        if len(relationship_names) != len(set(relationship_names)):
            raise ValueError("relationship names must be unique within a Semantic Model")

        entity_name_set = set(entity_names)
        for relationship in self.relationships:
            if relationship.subject not in entity_name_set:
                raise ValueError(
                    f"relationship {relationship.name!r}: subject "
                    f"{relationship.subject!r} is not a declared entity"
                )
            if relationship.object not in entity_name_set:
                raise ValueError(
                    f"relationship {relationship.name!r}: object "
                    f"{relationship.object!r} is not a declared entity"
                )

        for metric in self.metrics:
            if metric.entity not in entity_name_set:
                raise ValueError(
                    f"metric {metric.name!r}: entity {metric.entity!r} is not a declared entity"
                )

        return self

    def get_entity(self, name: str) -> Entity:
        for entity in self.entities:
            if entity.name == name:
                return entity
        raise KeyError(f"no entity named {name!r} in this Semantic Model")
