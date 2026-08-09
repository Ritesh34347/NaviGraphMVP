"""Contracts for the Ontology Drafting agent.

Follows the exact pattern established by
`navigraph_agents.understanding.intent_understanding.contracts`: a small
payload model, an `AgentInput` subclass wrapping it plus a `request_context`,
a result model, and an `AgentOutput` subclass wrapping that result.

`CatalogInventoryEntry` mirrors `metadata_discovery.contracts
.CatalogColumnEntry` field-for-field -- duplicated rather than cross-
imported, see `schema_mapping.contracts.CatalogInventoryEntry`'s identical
docstring for the full rationale (no agent package should import another
agent package's contracts module directly).

The `Draft*` result types are DELIBERATELY NOT
`navigraph_semantic_model` types, for the same reason: this agent lives in
`navigraph_agents`, which has no dependency on (and should not gain one
just for this) the `navigraph_semantic_model` package. A draft is not
compiled into a real `SemanticModel` here -- that conversion, gated on a
human reviewing/editing/rejecting each proposal, is a separate step (the
onboarding tooling), never automatic. Every `Draft*` type carries a
`rationale: str` for exactly that reason: a human reviewing a proposal
needs to see WHY the agent made it, not just the proposal itself.
"""

from __future__ import annotations

from typing import Literal

from navigraph_shared.contracts import AgentInput, AgentOutput
from pydantic import BaseModel, ConfigDict, Field, model_validator

DraftAggregation = Literal["SUM", "COUNT", "AVG", "MIN", "MAX"]


class CatalogInventoryEntry(BaseModel):
    """One column's raw catalog structure, enriched with its business
    glossary entry when one exists -- the closed candidate list this agent's
    prompt is built from, and the list every LLM-returned table/column
    reference is validated against."""

    model_config = ConfigDict(extra="forbid")

    catalog_column_id: str
    table_name: str
    schema_name: str
    column_name: str
    data_type: str
    nullable: bool
    is_pii: bool = False
    business_name: str | None = None
    synonyms: list[str] = Field(default_factory=list)
    description: str | None = None


class OntologyDraftingPayload(BaseModel):
    """Which data source's crawled catalog to draft an ontology from."""

    model_config = ConfigDict(extra="forbid")

    data_source_id: str


class OntologyDraftingInput(AgentInput):
    """Input contract for the Ontology Drafting agent.

    Inherits the mandatory, non-optional `request_context: RequestContext`
    field from `AgentInput`.
    """

    payload: OntologyDraftingPayload


class DraftEntityBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table_name: str
    schema_name: str
    key_column: str


class DraftEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    bindings: list[DraftEntityBinding] = Field(min_length=1)
    synonyms: list[str] = Field(default_factory=list)
    description: str | None = None
    rationale: str


class DraftRelationship(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    subject: str
    predicate: str
    object: str
    realizing_table: str
    realizing_schema: str
    subject_key_column: str
    object_key_column: str
    rationale: str


class DraftSensitiveColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table_name: str
    column_name: str
    rationale: str


class DraftMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    entity: str
    aggregation: DraftAggregation
    column: str | None = None
    rationale: str

    @model_validator(mode="after")
    def _column_required_unless_counting_rows(self) -> DraftMetric:
        if self.aggregation != "COUNT" and self.column is None:
            raise ValueError(
                f"draft metric {self.name!r}: 'column' is required for aggregation "
                f"{self.aggregation!r} (only COUNT may omit it)"
            )
        return self


class OntologyDraftingResult(BaseModel):
    """Every candidate proposal drafted for `data_source_id`, plus which
    catalog columns the LLM proposed nothing at all for (not an error --
    most columns in a real schema are never part of any entity/relationship/
    metric, e.g. audit timestamps, internal surrogate keys)."""

    model_config = ConfigDict(extra="forbid")

    data_source_id: str
    entities: list[DraftEntity] = Field(default_factory=list)
    relationships: list[DraftRelationship] = Field(default_factory=list)
    sensitive_columns: list[DraftSensitiveColumn] = Field(default_factory=list)
    metrics: list[DraftMetric] = Field(default_factory=list)


class OntologyDraftingOutput(AgentOutput):
    """Output contract for the Ontology Drafting agent."""

    result: OntologyDraftingResult
