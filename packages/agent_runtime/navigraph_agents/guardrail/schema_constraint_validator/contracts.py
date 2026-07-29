"""Contracts for the Schema Constraint Validator agent.

Follows the same pattern as
`navigraph_agents.understanding.intent_understanding.contracts`: a small
payload model, an `AgentInput` subclass wrapping it plus a
`request_context`, a result model, and an `AgentOutput` subclass wrapping
that result.

A note on the local `GeneratedSql` type below: it mirrors the shape of
`navigraph_agents.query.sql_generation.contracts.GeneratedSql`, a type
owned by the sibling SQL Generation agent package. It is duplicated here on
purpose, not imported across the package boundary -- see
`navigraph_agents.understanding.schema_mapping.contracts.CatalogInventoryEntry`'s
docstring for the full rationale: agent-to-agent contract dependencies
should go through the Coordinator's data flow, not direct Python imports
between sibling agent packages.
"""

from __future__ import annotations

from typing import Any

from navigraph_shared.contracts import AgentError, AgentInput, AgentOutput
from pydantic import BaseModel, ConfigDict, Field


class GeneratedSql(BaseModel):
    """Mirrors `sql_generation.contracts.GeneratedSql` field-for-field --
    duplicated rather than cross-imported, per the established
    sibling-package convention (see
    `schema_mapping.contracts.CatalogInventoryEntry`'s docstring for the
    full rationale: agent-to-agent contract dependencies should go
    through the Coordinator's data flow, not direct Python imports
    between sibling agent packages)."""

    model_config = ConfigDict(extra="forbid")

    data_source_id: str
    sql: str
    params: dict[str, Any] = Field(default_factory=dict)
    referenced_tables: list[str]
    referenced_columns: list[str]


class SchemaConstraintValidatorPayload(BaseModel):
    """The generated statements to validate against the real catalog."""

    model_config = ConfigDict(extra="forbid")

    statements: list[GeneratedSql]


class SchemaConstraintValidatorInput(AgentInput):
    """Input contract for the Schema Constraint Validator agent.

    Inherits the mandatory, non-optional `request_context: RequestContext`
    field from `AgentInput` -- this agent cannot be invoked without a
    tenant-scoped request context.
    """

    payload: SchemaConstraintValidatorPayload


class SchemaConstraintValidatorResult(BaseModel):
    """A statement that fails the check can never appear in `validated`,
    only in `rejected` -- mirrors `ExecutionPlanningResult`'s plans/rejected
    split exactly: `_validate_statements` in `agent.py` returns a plain
    boolean branch per statement, and only the `True` branch appends to
    `validated`; there is no post-hoc filtering step that a future edit
    could accidentally skip."""

    model_config = ConfigDict(extra="forbid")

    validated: list[GeneratedSql]
    rejected: list[AgentError] = Field(default_factory=list)


class SchemaConstraintValidatorOutput(AgentOutput):
    """Output contract for the Schema Constraint Validator agent."""

    result: SchemaConstraintValidatorResult
