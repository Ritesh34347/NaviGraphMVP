"""Contracts for the SQL Generation agent.

Follows the same pattern as
`navigraph_agents.understanding.intent_understanding.contracts`: a small
payload model, an `AgentInput` subclass wrapping it plus a
`request_context`, a result model, and an `AgentOutput` subclass wrapping
that result.

A note on the local `ResolvedColumnRef` / `JoinSpec` / `SchemaMappingResult`
/ `ResolvedDataSource` types below: each mirrors the shape of a type owned
by a sibling agent package -- `ResolvedColumnRef`, `JoinSpec`, and
`SchemaMappingResult` mirror
`navigraph_agents.understanding.schema_mapping.contracts`'s types of the
same name; `ResolvedDataSource` mirrors (a minimal subset of)
`navigraph_agents.query.data_source_discovery.contracts.ResolvedDataSource`.
These are duplicated here on purpose, not imported across the package
boundary -- see
`navigraph_agents.understanding.schema_mapping.contracts.CatalogInventoryEntry`'s
docstring for the full rationale, which applies equally here: agent-to-agent
contract dependencies should go through a future Coordinator's data flow,
not direct Python imports between sibling agent packages, and each sibling
package is free to depend on only the fields it actually needs rather than
the full upstream shape.

`IntentLabel` is the one exception to "mirror, don't import": it is a
controlled vocabulary, not a data shape, and
`navigraph_agents.understanding.schema_mapping.contracts` already
establishes the precedent of importing it directly from
`intent_understanding.contracts` rather than redefining it -- see that
module's comment on the same import for the rationale, which applies
verbatim here.

KNOWN CONTRACT GAP -- flagged, not silently worked around: the real
`navigraph_agents.understanding.schema_mapping.contracts.ResolvedColumnRef`
carries no `schema_name` field, even though the `CatalogInventoryEntry` it
is built FROM (in `SchemaMappingAgent._resolve_columns`) does carry one --
that field is simply dropped when `CatalogInventoryEntry` is narrowed down
to `ResolvedColumnRef`. Dialect-neutral `SCHEMA.TABLE` SQL genuinely
requires knowing which schema a table lives in (see `agent.py`'s
`_build_from_clause`), so this local mirror adds `schema_name` as a real,
defensible field this package actually needs -- but that means it is not
byte-for-byte identical to the real upstream `ResolvedColumnRef`. This is a
real signal that `schema_mapping.contracts.ResolvedColumnRef` likely needs
the same `schema_name` field added in a later integration pass (threaded
through from `CatalogInventoryEntry.schema_name` at construction time),
not something this package should paper over by guessing a hardcoded
schema name. See the top-level report for this flagged explicitly.
"""

from __future__ import annotations

from typing import Any, Literal

from navigraph_shared.contracts import AgentInput, AgentOutput
from pydantic import BaseModel, ConfigDict, Field

# Reused, not redefined -- see schema_mapping/contracts.py's identical import
# and comment for why the controlled intent vocabulary is imported directly
# rather than mirrored like the data-shape types below.
from navigraph_agents.understanding.intent_understanding.contracts import IntentLabel


class ResolvedColumnRef(BaseModel):
    """Mirrors `schema_mapping.contracts.ResolvedColumnRef` field-for-field,
    PLUS a `schema_name` field the real type is currently missing -- see
    this module's docstring for the full "known contract gap" rationale."""

    model_config = ConfigDict(extra="forbid")

    term: str
    catalog_column_id: str
    table_name: str
    schema_name: str
    column_name: str
    data_type: str
    role: Literal["measure", "dimension", "filter"]


class JoinSpec(BaseModel):
    """Mirrors `schema_mapping.contracts.JoinSpec` field-for-field, including
    `left_schema`/`right_schema` -- see that module's docstring for why a
    bridge table (one contributing no `ResolvedColumnRef`) needs its schema
    threaded through the join itself rather than derived from `columns`."""

    model_config = ConfigDict(extra="forbid")

    left_table: str
    left_column: str
    right_table: str
    right_column: str
    left_schema: str | None = None
    right_schema: str | None = None
    relationship_concept: str | None = None


class SchemaMappingResult(BaseModel):
    """Mirrors `schema_mapping.contracts.SchemaMappingResult` field-for-field
    (modulo `columns` using this module's `ResolvedColumnRef`, which adds
    `schema_name` -- see this module's docstring)."""

    model_config = ConfigDict(extra="forbid")

    tables: list[str]
    columns: list[ResolvedColumnRef]
    joins: list[JoinSpec] = Field(default_factory=list)
    unmapped_terms: list[str] = Field(default_factory=list)


class ResolvedDataSource(BaseModel):
    """Mirrors a minimal subset of
    `navigraph_agents.query.data_source_discovery.contracts.ResolvedDataSource`
    -- just the fields this agent actually needs to decide which physical
    data source a generated statement targets and whether it's safe to
    query at all. Deliberately omits that sibling's
    `connection_test_latency_ms`/`connection_test_message` fields, which
    are only useful for that agent's own diagnostics, not SQL generation."""

    model_config = ConfigDict(extra="forbid")

    table_name: str
    data_source_id: str
    source_type: str
    reachable: bool


class SqlGenerationPayload(BaseModel):
    """Everything the upstream Understanding-domain pipeline (via Schema
    Mapping) and the sibling Data Source Discovery agent contribute to this
    agent's SQL-generation decision."""

    model_config = ConfigDict(extra="forbid")

    original_question: str
    intent: IntentLabel
    schema_mapping: SchemaMappingResult
    resolved_data_sources: list[ResolvedDataSource]


class SqlGenerationInput(AgentInput):
    """Input contract for the SQL Generation agent.

    Inherits the mandatory, non-optional `request_context: RequestContext`
    field from `AgentInput`.
    """

    payload: SqlGenerationPayload


class PredicateResolution(BaseModel):
    """One relative-date or qualitative filter phrase from the original
    question, resolved to a literal, bindable value against one of
    `schema_mapping`'s real resolved columns."""

    model_config = ConfigDict(extra="forbid")

    raw_phrase: str
    column: str
    operator: Literal["=", "!=", ">", ">=", "<", "<=", "IN", "BETWEEN", "LIKE"]
    # The LITERAL value(s) -- never embedded in SQL text. Always bound via a
    # placeholder in `GeneratedSql.sql` and carried in `GeneratedSql.params`
    # instead. A plain `str` for single-value operators; a `list[str]` for
    # `IN` (one or more values) and `BETWEEN` (exactly two values) -- see
    # `agent.py`'s `_build_where_clause`.
    resolved_value: str | list[str]
    rationale: str | None = None


class GeneratedSql(BaseModel):
    """One dialect-neutral, bind-parameterized SQL statement targeting a
    single resolved data source."""

    model_config = ConfigDict(extra="forbid")

    data_source_id: str
    # Dialect-neutral `SCHEMA.TABLE` form, e.g.
    # "SELECT ... FROM STAGING.STAGING_TRANSACTIONS ...". Table/column
    # identifiers are embedded directly as SQL text (they are
    # catalog-validated identifiers from a prior agent, not user input --
    # safe to embed as identifiers, never as data values). Every literal
    # DATA value lives in `params` instead, bound via `%(name)s`
    # placeholders -- see `agent.py`'s module docstring for why that exact
    # placeholder style was chosen to match
    # `SnowflakeConnector.execute_query`'s real paramstyle.
    sql: str
    params: dict[str, Any] = Field(default_factory=dict)
    referenced_tables: list[str]
    referenced_columns: list[str]


class SqlGenerationResult(BaseModel):
    """The generated SQL statements (one per resolved data source), the
    predicate phrases successfully resolved to literal bindable values, and
    any predicate phrase that could not be resolved (malformed LLM output,
    or an LLM-returned column that wasn't in the closed candidate list)."""

    model_config = ConfigDict(extra="forbid")

    statements: list[GeneratedSql]
    predicate_resolutions: list[PredicateResolution] = Field(default_factory=list)
    unresolved_predicates: list[str] = Field(default_factory=list)


class SqlGenerationOutput(AgentOutput):
    """Output contract for the SQL Generation agent."""

    result: SqlGenerationResult
