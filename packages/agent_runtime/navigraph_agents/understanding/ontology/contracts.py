"""Contracts for the Ontology agent.

Follows the same pattern as
`navigraph_agents.understanding.intent_understanding.contracts`: a small
payload model, an `AgentInput` subclass wrapping it plus a
`request_context`, a result model, and an `AgentOutput` subclass wrapping
that result. Unlike Intent Understanding, this agent is fully
deterministic -- no LLM call, no `prompts/` directory -- it is a thin
resolution layer over `navigraph_kg.api`.
"""

from __future__ import annotations

from navigraph_shared.contracts import AgentInput, AgentOutput
from pydantic import BaseModel, ConfigDict, Field

# Reused, not redefined -- see intent_understanding/contracts.py's module
# docstring for why `IntentLabel` is the single controlled vocabulary every
# agent that deals with intent must import rather than re-declare.
from navigraph_agents.understanding.intent_understanding.contracts import IntentLabel


class OntologyPayload(BaseModel):
    """The entities (from Intent Understanding) and the classified intent
    this agent uses to resolve business terms and scan for relationship
    concepts."""

    model_config = ConfigDict(extra="forbid")

    entities: list[str]
    intent: IntentLabel


class OntologyInput(AgentInput):
    """Input contract for the Ontology agent.

    Inherits the mandatory, non-optional `request_context: RequestContext`
    field from `AgentInput`.
    """

    payload: OntologyPayload


class ConceptResolution(BaseModel):
    """The outcome of resolving a single entity/term against
    `BusinessConcept` nodes in the knowledge graph."""

    model_config = ConfigDict(extra="forbid")

    term: str
    resolved: bool
    business_concept: str | None = None
    catalog_column_id: str | None = None
    column_name: str | None = None
    table_name: str | None = None
    preferred: bool | None = None


class RelationshipResolution(BaseModel):
    """A hand-curated `RelationshipConcept` match: which two input entities
    it connects, and what SQL join it describes."""

    model_config = ConfigDict(extra="forbid")

    subject_label: str
    predicate: str
    object_label: str
    realizing_table: str
    subject_key_column: str
    object_key_column: str


class OntologyResult(BaseModel):
    """Every concept resolution attempted, any relationship concepts found
    to span the input entities, and the subset of terms that failed to
    resolve at all."""

    model_config = ConfigDict(extra="forbid")

    concept_resolutions: list[ConceptResolution]
    relationship_resolutions: list[RelationshipResolution] = Field(default_factory=list)
    unresolved_terms: list[str] = Field(default_factory=list)


class OntologyOutput(AgentOutput):
    """Output contract for the Ontology agent."""

    result: OntologyResult
