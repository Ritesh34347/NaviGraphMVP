"""Contracts for the Policy Authorization agent.

Follows the same pattern as
`navigraph_agents.understanding.intent_understanding.contracts`: a small
payload model, an `AgentInput` subclass wrapping it plus a
`request_context`, a result model, and an `AgentOutput` subclass wrapping
that result.

A note on the local `GeneratedSql` type below: it mirrors the shape of
`navigraph_agents.query.sql_generation.contracts.GeneratedSql`, an
identical mirrored shape to
`guardrail.schema_constraint_validator.contracts.GeneratedSql` -- duplicated
here on purpose, not imported across the package boundary -- see
`navigraph_agents.understanding.schema_mapping.contracts.CatalogInventoryEntry`'s
docstring for the full rationale: agent-to-agent contract dependencies
should go through the Coordinator's data flow, not direct Python imports
between sibling agent packages.

`IntentLabel` is the one exception to "mirror, don't import": it is a
controlled vocabulary, not a data shape, and
`navigraph_agents.understanding.schema_mapping.contracts` (followed by
`navigraph_agents.query.sql_generation.contracts`) already establishes the
precedent of importing it directly from `intent_understanding.contracts`
rather than redefining it -- see that module's comment on the same import
for the rationale, which applies verbatim here.
"""

from __future__ import annotations

from typing import Any

from navigraph_shared.contracts import AgentError, AgentInput, AgentOutput
from pydantic import BaseModel, ConfigDict, Field

# Reused, not redefined -- see intent_understanding/contracts.py's module
# docstring for why `IntentLabel` is the single controlled vocabulary every
# agent that deals with intent must import rather than re-declare.
from navigraph_agents.understanding.intent_understanding.contracts import IntentLabel


class GeneratedSql(BaseModel):
    """Mirrors `sql_generation.contracts.GeneratedSql` field-for-field --
    an identical mirrored shape to
    `guardrail.schema_constraint_validator.contracts.GeneratedSql` -- see
    this module's docstring for the full rationale."""

    model_config = ConfigDict(extra="forbid")

    data_source_id: str
    sql: str
    params: dict[str, Any] = Field(default_factory=dict)
    referenced_tables: list[str]
    referenced_columns: list[str]


class PolicyAuthorizationPayload(BaseModel):
    """The statements to authorize, plus the classified intent behind the
    original question -- OPA's real policy considers both when deciding."""

    model_config = ConfigDict(extra="forbid")

    statements: list[GeneratedSql]
    intent: IntentLabel


class PolicyAuthorizationInput(AgentInput):
    """Input contract for the Policy Authorization agent.

    Inherits the mandatory, non-optional `request_context: RequestContext`
    field from `AgentInput` -- this agent cannot be invoked without a
    tenant-scoped request context (tenant_id/user_id/roles/claims are all
    part of every real OPA `input_document` -- see `agent.py`).
    """

    payload: PolicyAuthorizationPayload


class OpaDecision(BaseModel):
    """One statement's real OPA decision, for audit/lineage -- not just
    allow/deny, the specific `deny_reasons` list too."""

    model_config = ConfigDict(extra="forbid")

    data_source_id: str
    allow: bool
    deny_reasons: list[str] = Field(default_factory=list)


class PolicyAuthorizationResult(BaseModel):
    """The statements OPA authorized, every real decision made (allowed and
    denied alike, for audit), and any statement that failed authorization --
    a denied or unreachable-OPA statement never appears in `authorized`,
    only in `rejected`, mirroring `ExecutionPlanningResult`'s plans/rejected
    split exactly."""

    model_config = ConfigDict(extra="forbid")

    authorized: list[GeneratedSql]
    decisions: list[OpaDecision] = Field(default_factory=list)
    rejected: list[AgentError] = Field(default_factory=list)


class PolicyAuthorizationOutput(AgentOutput):
    """Output contract for the Policy Authorization agent."""

    result: PolicyAuthorizationResult
