"""Request/response contracts for self-service data source onboarding.

These are plain FastAPI route models, not `AgentInput`/`AgentOutput`
subclasses -- onboarding a data source (register credentials, crawl a
schema, compile+activate a reviewed ontology draft) isn't agent-shaped in
this codebase's existing convention, same rationale as `/lineage`,
`/data_sources`, and `/glossary` in `navigraph_agents.main`. Ontology
DRAFTING itself stays fully agent-shaped and unchanged -- see
`understanding.ontology_drafting.contracts.OntologyDraftingResult` -- this
module never redefines that shape; `CompileAndActivateRequest.draft` below
accepts exactly its `model_dump()`.
"""

from __future__ import annotations

from typing import Any

from navigraph_connectors.base import ConnectorCapabilities, RequiredSetting
from pydantic import BaseModel, ConfigDict, Field


class ConnectorTypeInfo(BaseModel):
    """One registered connector type's declarative onboarding manifest --
    powers a self-service UI's dynamic connection form without hardcoding
    per-source-type fields anywhere in this package."""

    model_config = ConfigDict(extra="forbid")

    source_type: str
    required_settings: list[RequiredSetting]
    capabilities: ConnectorCapabilities


class RegisterDataSourceRequest(BaseModel):
    """A client's own credentials, never a raw `connection_ref` -- the
    server computes `secret_scope` and writes `credential_fields` via
    `SecretsProvider.set()` itself (see `onboarding_routes.register_data_source_route`).
    A client-supplied `connection_ref` would let a caller point a new
    `DataSource` at an existing (possibly another tenant's) secret scope
    without ever proving they know its values."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    name: str
    source_type: str
    is_default: bool = False
    credential_fields: dict[str, str] = Field(default_factory=dict)


class TestConnectionRequest(BaseModel):
    """Pre-save dry run: field values live only in this request, never
    touch `SecretsProvider.set()`, and nothing is persisted regardless of
    the outcome."""

    model_config = ConfigDict(extra="forbid")

    source_type: str
    credential_fields: dict[str, str] = Field(default_factory=dict)


class CrawlRequest(BaseModel):
    """`tenant_id` is redundant with the path's `data_source_id` but
    required anyway -- defense in depth, confirming the caller's claimed
    tenant actually owns this data source before crawling it, matching
    every other route's tenant-scoping discipline in this codebase."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str


class CompileAndActivateRequest(BaseModel):
    """`draft` is the (possibly human-edited) `OntologyDraftingResult.model_dump()`
    the browser held in memory since drafting -- see this feature's plan
    for why the draft round-trips through the client instead of a new
    server-side drafts table."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    data_source_name: str
    version: int = 1
    draft: dict[str, Any]
