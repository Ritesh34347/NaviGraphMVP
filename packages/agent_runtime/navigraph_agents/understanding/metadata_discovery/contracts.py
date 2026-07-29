"""Contracts for the Metadata Discovery agent.

Follows the exact pattern established by
`navigraph_agents.understanding.intent_understanding.contracts`: a small
payload model, an `AgentInput` subclass wrapping it plus a `request_context`,
a result model, and an `AgentOutput` subclass wrapping that result.

`data_source_id` is passed as a `str` (a UUID's string form), matching
`IntentUnderstandingPayload`'s convention of plain string fields on the
payload -- parsing/validating it as a real `uuid.UUID` is the agent's job
(see `agent.py`), not the contract's.
"""

from __future__ import annotations

from navigraph_shared.contracts import AgentInput, AgentOutput
from pydantic import BaseModel, ConfigDict, Field


class MetadataDiscoveryPayload(BaseModel):
    """Which data source's catalog metadata to discover."""

    model_config = ConfigDict(extra="forbid")

    data_source_id: str


class MetadataDiscoveryInput(AgentInput):
    """Input contract for the Metadata Discovery agent.

    Inherits the mandatory, non-optional `request_context: RequestContext`
    field from `AgentInput` -- this agent cannot be invoked without a
    tenant-scoped request context.
    """

    payload: MetadataDiscoveryPayload


class CatalogColumnEntry(BaseModel):
    """One column's raw catalog structure, enriched with its business
    glossary entry when one exists.

    `business_name`/`synonyms`/`description` are `None`/`[]` when the column
    has no matching `ColumnGlossary` row -- that is the expected, common case
    (most columns are never hand- or auto-enriched), not an error.
    """

    model_config = ConfigDict(extra="forbid")

    catalog_column_id: str
    table_name: str
    schema_name: str
    column_name: str
    data_type: str
    nullable: bool
    business_name: str | None = None
    synonyms: list[str] = Field(default_factory=list)
    description: str | None = None


class MetadataDiscoveryResult(BaseModel):
    """Every column discovered for `data_source_id`, glossary-enriched."""

    model_config = ConfigDict(extra="forbid")

    data_source_id: str
    columns: list[CatalogColumnEntry] = Field(default_factory=list)


class MetadataDiscoveryOutput(AgentOutput):
    """Output contract for the Metadata Discovery agent."""

    result: MetadataDiscoveryResult
