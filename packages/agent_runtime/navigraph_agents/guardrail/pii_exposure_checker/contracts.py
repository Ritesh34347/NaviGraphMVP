"""Contracts for the PII Exposure Checker agent.

Follows the same pattern as
`navigraph_agents.understanding.intent_understanding.contracts`: a small
payload model, an `AgentInput` subclass wrapping it plus a
`request_context`, a result model, and an `AgentOutput` subclass wrapping
that result. Unlike the Query Cost Estimator, this agent is NOT a pure
function -- it has a real catalog dependency (`navigraph_catalog.api.
find_column`) to resolve a referenced column back to its `is_pii` flag,
matching `navigraph_agents.query.data_source_discovery`'s
`session_factory: sessionmaker[Session]` constructor pattern.
"""

from __future__ import annotations

from typing import Any

from navigraph_shared.contracts import AgentError, AgentInput, AgentOutput
from pydantic import BaseModel, ConfigDict, Field


class GeneratedSql(BaseModel):
    """Mirrors sql_generation.contracts.GeneratedSql field-for-field --
    duplicated rather than cross-imported (same sibling-package
    convention as schema_constraint_validator's identical local mirror,
    built concurrently in a sibling package -- do not import from it)."""

    model_config = ConfigDict(extra="forbid")

    data_source_id: str
    sql: str
    params: dict[str, Any] = Field(default_factory=dict)
    referenced_tables: list[str]
    referenced_columns: list[str]


class PiiExposureCheckerPayload(BaseModel):
    """The statements SQL Generation produced, whose referenced columns
    this agent checks against the catalog's `is_pii` flag."""

    model_config = ConfigDict(extra="forbid")

    statements: list[GeneratedSql]


class PiiExposureCheckerInput(AgentInput):
    """Input contract for the PII Exposure Checker agent.

    Inherits the mandatory, non-optional `request_context: RequestContext`
    field from `AgentInput` -- `request_context.roles` is load-bearing
    here since it drives whether the caller is authorized for PII columns.
    """

    payload: PiiExposureCheckerPayload


class PiiExposureCheckerResult(BaseModel):
    """The statements cleared for execution, and any statement rejected
    for referencing a PII column the caller's roles are not authorized to
    see -- a rejected statement never appears in `cleared`, only in
    `rejected`."""

    model_config = ConfigDict(extra="forbid")

    cleared: list[GeneratedSql]
    rejected: list[AgentError] = Field(default_factory=list)


class PiiExposureCheckerOutput(AgentOutput):
    """Output contract for the PII Exposure Checker agent."""

    result: PiiExposureCheckerResult
