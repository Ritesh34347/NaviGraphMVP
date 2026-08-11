"""Contracts for the Schema Mapping agent.

Follows the same pattern as
`navigraph_agents.understanding.intent_understanding.contracts`: a small
payload model, an `AgentInput` subclass wrapping it plus a
`request_context`, a result model, and an `AgentOutput` subclass wrapping
that result. Fully deterministic -- no LLM call, no `prompts/` directory:
this agent is a pure function of its input (no external client
dependency at all, unlike the Ontology agent).

A note on the local `ConceptResolution` / `RelationshipResolution` /
`TermMatch` / `CatalogInventoryEntry` types below: each mirrors the shape
of a type owned by a sibling agent package (`ConceptResolution` and
`RelationshipResolution` mirror
`navigraph_agents.understanding.ontology.contracts`'s types of the same
name; `TermMatch` mirrors what the Semantic Retrieval agent would produce;
`CatalogInventoryEntry` mirrors
`navigraph_agents.understanding.metadata_discovery.contracts.CatalogColumnEntry`).
These are duplicated here on purpose, not imported across the package
boundary -- see `CatalogInventoryEntry`'s docstring below for the full
rationale, which applies equally to all four types.
"""

from __future__ import annotations

from typing import Literal

from navigraph_shared.contracts import AgentInput, AgentOutput
from pydantic import BaseModel, ConfigDict, Field

# Reused, not redefined -- see intent_understanding/contracts.py's module
# docstring for why `IntentLabel` is the single controlled vocabulary every
# agent that deals with intent must import rather than re-declare.
from navigraph_agents.understanding.intent_understanding.contracts import IntentLabel


class ConceptResolution(BaseModel):
    """Mirrors `navigraph_agents.understanding.ontology.contracts.ConceptResolution`
    field-for-field -- duplicated rather than cross-imported since
    agent-to-agent contract dependencies should go through the Coordinator's
    data flow, not direct Python imports between sibling agent packages."""

    model_config = ConfigDict(extra="forbid")

    term: str
    resolved: bool
    business_concept: str | None = None
    catalog_column_id: str | None = None
    column_name: str | None = None
    table_name: str | None = None
    preferred: bool | None = None


class RelationshipResolution(BaseModel):
    """Mirrors `navigraph_agents.understanding.ontology.contracts.RelationshipResolution`
    field-for-field -- duplicated rather than cross-imported since
    agent-to-agent contract dependencies should go through the Coordinator's
    data flow, not direct Python imports between sibling agent packages."""

    model_config = ConfigDict(extra="forbid")

    subject_label: str
    predicate: str
    object_label: str
    realizing_table: str
    subject_key_column: str
    object_key_column: str


class TermMatch(BaseModel):
    """What the (sibling, not-yet-necessarily-existing) Semantic Retrieval
    agent would produce for one term: whether it found a matching catalog
    column via embedding/semantic search, distinct from the Ontology
    agent's graph-based `ConceptResolution` path. Duplicated here rather
    than cross-imported -- see this module's docstring."""

    model_config = ConfigDict(extra="forbid")

    term: str
    matched: bool
    catalog_column_id: str | None = None
    table_name: str | None = None
    column_name: str | None = None
    rationale: str | None = None


class CatalogInventoryEntry(BaseModel):
    """Mirrors `metadata_discovery.contracts.CatalogColumnEntry` -- duplicated
    rather than cross-imported since agent-to-agent contract dependencies
    should go through the Coordinator's data flow, not direct Python imports
    between sibling agent packages. That is not merely a stopgap for
    metadata_discovery not existing yet when this package was written: it is
    the correct long-term pattern -- a future Coordinator passes each
    agent's output into the next agent's input, so no agent package should
    ever need to import another agent package's contracts module directly.
    """

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


class SchemaMappingPayload(BaseModel):
    """Everything upstream understanding agents (Ontology, Semantic
    Retrieval) plus the catalog inventory (Metadata Discovery) contribute
    to this agent's schema-mapping decision.

    `original_question` (added for the real temporal-filter-column
    injection fix -- see `agent.py`'s `_find_temporal_filter_column`):
    entity extraction frequently never names "date" at all for a question
    like "orders in the last 30 days" (the entity is "orders"), so no date
    column ever reaches `concept_resolutions`/`semantic_matches` for this
    agent to resolve -- the date-range phrase silently produces no filter
    downstream. This agent needs the raw text to detect that case
    deterministically, the same way `sql_generation.agent`'s own temporal-
    trigger heuristic already does.
    """

    model_config = ConfigDict(extra="forbid")

    intent: IntentLabel
    original_question: str
    concept_resolutions: list[ConceptResolution]
    relationship_resolutions: list[RelationshipResolution] = Field(default_factory=list)
    semantic_matches: list[TermMatch] = Field(default_factory=list)
    catalog_inventory: list[CatalogInventoryEntry]


class SchemaMappingInput(AgentInput):
    """Input contract for the Schema Mapping agent.

    Inherits the mandatory, non-optional `request_context: RequestContext`
    field from `AgentInput`.
    """

    payload: SchemaMappingPayload


class ResolvedColumnRef(BaseModel):
    """One term resolved all the way through to a concrete catalog column,
    with its assigned query role."""

    model_config = ConfigDict(extra="forbid")

    term: str
    catalog_column_id: str
    table_name: str
    # Added in Phase 5: dialect-neutral SQL generation (SCHEMA.TABLE form)
    # needs the schema name, which was already available on
    # `CatalogInventoryEntry` but was being dropped here -- a real gap
    # surfaced when the Query domain's SQL Generation agent needed it and
    # had to work around its absence with a local guess.
    schema_name: str
    column_name: str
    data_type: str
    role: Literal["measure", "dimension", "filter"]


class JoinSpec(BaseModel):
    """A SQL join between two tables, derived from a resolved relationship
    concept that spans them.

    `left_schema`/`right_schema` are populated from `catalog_inventory` for
    EVERY join (not just bridge joins) directly at the source, rather than
    left for SQL Generation to re-derive from `columns` alone -- a table
    pulled in purely as a 2-hop bridge (see `_build_joins`'s "FIFTH REAL
    BUG") contributes no `ResolvedColumnRef`, so `columns`-derived schema
    lookups have nothing to find for it. Optional because a caller
    constructing this in a test fixture may not care about schema
    qualification.
    """

    model_config = ConfigDict(extra="forbid")

    left_table: str
    left_column: str
    right_table: str
    right_column: str
    left_schema: str | None = None
    right_schema: str | None = None
    relationship_concept: str | None = None


class SchemaMappingResult(BaseModel):
    """The concrete tables, columns, and joins this agent assembled, plus
    any terms it could not map to a catalog column at all."""

    model_config = ConfigDict(extra="forbid")

    tables: list[str]
    columns: list[ResolvedColumnRef]
    joins: list[JoinSpec] = Field(default_factory=list)
    unmapped_terms: list[str] = Field(default_factory=list)


class SchemaMappingOutput(AgentOutput):
    """Output contract for the Schema Mapping agent."""

    result: SchemaMappingResult
