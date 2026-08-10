"""Self-service data source onboarding: register a data source, test a
connection before saving, crawl it, and compile+activate a human-reviewed
ontology draft.

Plain FastAPI routes on a dedicated `APIRouter`, not `_invoke_agent`-wrapped
-- same "this isn't agent-shaped" precedent as `/lineage`, `/data_sources`,
and `/glossary` in `navigraph_agents.main`. Ontology DRAFTING itself is
deliberately NOT duplicated here: the gateway proxies straight to the
already-existing `POST /agents/understanding/ontology_drafting/invoke`
route, so there is exactly one place that agent is ever invoked from HTTP.

Mounted onto the main `app` via `app.include_router(onboarding_router)` in
`navigraph_agents.main`, which also constructs `app.state.secrets_provider`
once at startup -- every route below reads `request.app.state.secrets_provider`
and `request.app.state.catalog_session_factory` rather than constructing
either itself, matching this codebase's existing convention of building
shared clients once in `lifespan()`.

THE BUG FIX (LIMITATIONS.md item 21's last unclosed corner): `crawl_data_source_route`
resolves a real per-`DataSource` `Settings` via `get_settings_factory()` +
`SecretsProvider`, copying the exact pattern `data_source_discovery.agent
._check_connectivity` already gets right -- unlike
`tools/scripts/onboard_data_source.py`'s `cmd_crawl`, which still
constructs `connector_cls()` with zero arguments and would silently crawl
the wrong (global-env-var) source for a self-service-registered
`DataSource`. That CLI path is intentionally left unfixed here (out of
scope for this change).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from navigraph_catalog.api import (
    get_active_semantic_model,
    list_data_sources,
    register_data_source,
)
from navigraph_catalog.db import session_scope as catalog_session_scope
from navigraph_catalog.ingestion.snowflake_crawler import crawl_and_store
from navigraph_connectors.base import Connector, ConnectionTestResult
from navigraph_connectors.registry import (
    get_connector_class,
    get_settings_factory,
    list_registered_source_types,
)
from navigraph_semantic_model.activation import activate_semantic_model
from navigraph_semantic_model.loader import SemanticModelValidationError
from navigraph_semantic_model.onboarding import compile_draft_to_semantic_model
from navigraph_shared.secrets import build_secret_scope
from pydantic import ValidationError

from navigraph_agents.onboarding_contracts import (
    CompileAndActivateRequest,
    ConnectorTypeInfo,
    CrawlRequest,
    RegisterDataSourceRequest,
    TestConnectionRequest,
)

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


@router.get("/connector-types")
async def list_connector_types() -> dict:
    """Every registered connector type's onboarding manifest -- powers a
    self-service UI's dynamic connection form with zero per-source-type
    hardcoding in this route."""

    infos = []
    for source_type in list_registered_source_types():
        connector_cls = get_connector_class(source_type)
        infos.append(
            ConnectorTypeInfo(
                source_type=source_type,
                required_settings=connector_cls.required_settings(),
                # `capabilities()` is an instance method on the ABC, but
                # every real connector's `__init__` accepts zero required
                # args when constructed without `settings=` (falls back to
                # its own Settings() defaults) -- safe to instantiate here
                # purely to read a fixed, source-type-level fact, never to
                # actually connect to anything.
                capabilities=connector_cls().capabilities(),  # type: ignore[call-arg]
            )
        )
    return {"source_types": [info.model_dump(mode="json") for info in infos]}


def _build_settings(source_type: str, credential_fields: dict[str, str]) -> Any:
    """Build a connector-specific `Settings` instance directly from posted
    field values, with no `SecretsProvider` involved at all -- used only by
    `test_connection_route`, which must never persist anything regardless
    of outcome. Constructs the connector's own `Settings` class the way its
    `settings_factory` would, but reading straight from `credential_fields`
    via an in-memory `SecretsProvider` stand-in scoped to this one request.
    """

    from navigraph_shared.secrets import FakeSecretsProvider

    settings_factory = get_settings_factory(source_type)
    if settings_factory is None:
        raise HTTPException(
            status_code=400,
            detail=f"source_type={source_type!r} has no settings factory registered",
        )
    scope = "test-connection"
    fake_secrets = FakeSecretsProvider(
        {(scope, field): value for field, value in credential_fields.items()}
    )
    return settings_factory({"secret_scope": scope}, fake_secrets)


@router.post("/data-sources/test-connection")
async def test_connection_route(payload: TestConnectionRequest) -> dict:
    """Pre-save dry run. Never touches `SecretsProvider.set()` and never
    persists a `DataSource` row regardless of outcome."""

    try:
        connector_cls = get_connector_class(payload.source_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        settings = _build_settings(payload.source_type, payload.credential_fields)
        connector: Connector = connector_cls(settings=settings)  # type: ignore[call-arg]
        result = connector.test_connection()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - fold into the same "unreachable" shape
        result = ConnectionTestResult(
            success=False, message=f"failed to construct connector: {exc}"
        )

    return result.model_dump(mode="json")


@router.post("/data-sources")
async def register_data_source_route(payload: RegisterDataSourceRequest, request: Request) -> dict:
    """Write every credential field via `SecretsProvider.set()` BEFORE
    touching the catalog -- if any field write fails partway, abort without
    registering a `DataSource` that points at an incomplete secret scope.
    """

    try:
        get_connector_class(payload.source_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    secrets_provider = request.app.state.secrets_provider
    scope = build_secret_scope(tenant_id=payload.tenant_id, data_source_name=payload.name)

    written_fields: list[str] = []
    for field, value in payload.credential_fields.items():
        try:
            secrets_provider.set(scope=scope, field=field, value=value)
            written_fields.append(field)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=(
                    f"failed to write credential field {field!r} to the secrets backend "
                    f"after writing {written_fields} -- registration aborted, no DataSource "
                    f"was created: {exc}"
                ),
            ) from exc

    with catalog_session_scope(request.app.state.catalog_session_factory) as session:
        try:
            data_source = register_data_source(
                session,
                tenant_id=payload.tenant_id,
                name=payload.name,
                source_type=payload.source_type,
                connection_ref={"secret_scope": scope},
                is_default=payload.is_default,
            )
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        return {
            "id": str(data_source.id),
            "tenant_id": data_source.tenant_id,
            "name": data_source.name,
            "source_type": data_source.source_type,
            "is_default": data_source.is_default,
        }


@router.get("/data-sources")
async def list_data_sources_with_status_route(tenant_id: str, request: Request) -> dict:
    """Like `GET /data_sources`, enriched with `last_crawled_at` and
    whether `tenant_id` has an activated semantic model yet -- the two
    facts a self-service "your data sources" list screen needs that the
    plain MCP-facing route doesn't return."""

    with catalog_session_scope(request.app.state.catalog_session_factory) as session:
        data_sources = list_data_sources(session, tenant_id=tenant_id)
        active_model = get_active_semantic_model(session, tenant_id=tenant_id)

        return {
            "tenant_id": tenant_id,
            "semantic_model_active_version": (
                active_model.version if active_model is not None else None
            ),
            "data_sources": [
                {
                    "id": str(ds.id),
                    "name": ds.name,
                    "source_type": ds.source_type,
                    "is_default": ds.is_default,
                    "last_crawled_at": (
                        ds.last_crawled_at.isoformat() if ds.last_crawled_at is not None else None
                    ),
                }
                for ds in data_sources
            ],
        }


@router.post("/data-sources/{data_source_id}/crawl")
async def crawl_data_source_route(
    data_source_id: str, payload: CrawlRequest, request: Request
) -> dict:
    """Crawl a real, previously-registered `DataSource` and upsert its
    schema into the catalog.

    THE BUG FIX: resolves `get_settings_factory()` + the shared
    `SecretsProvider` when `connection_ref` carries a `secret_scope`,
    exactly like `data_source_discovery.agent._check_connectivity` already
    does correctly -- see this module's docstring.
    """

    try:
        data_source_uuid = uuid.UUID(data_source_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid data_source_id: {exc}") from exc

    with catalog_session_scope(request.app.state.catalog_session_factory) as session:
        data_sources = {
            str(ds.id): ds for ds in list_data_sources(session, tenant_id=payload.tenant_id)
        }
        data_source = data_sources.get(data_source_id)
        if data_source is None:
            raise HTTPException(
                status_code=404,
                detail=f"no DataSource {data_source_id!r} for tenant {payload.tenant_id!r}",
            )

        try:
            connector_cls = get_connector_class(data_source.source_type)
            connection_ref = data_source.connection_ref or {}
            secret_scope = connection_ref.get("secret_scope")
            settings_factory = (
                get_settings_factory(data_source.source_type) if secret_scope else None
            )
            if settings_factory is not None:
                connector: Connector = connector_cls(  # type: ignore[call-arg]
                    settings=settings_factory(connection_ref, request.app.state.secrets_provider)
                )
            else:
                connector = connector_cls()
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"failed to construct connector for crawl: {exc}",
            ) from exc

        try:
            result = crawl_and_store(session, data_source_id=data_source_uuid, connector=connector)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"crawl failed: {exc}") from exc

        return {
            "data_source_id": data_source_id,
            "tables_synced": result.tables_synced,
            "new_table_names": result.new_table_names,
        }


@router.post("/semantic-models/compile-and-activate")
async def compile_and_activate_route(payload: CompileAndActivateRequest, request: Request) -> dict:
    """Fuse `compile_draft_to_semantic_model` + `activate_semantic_model`,
    mirroring `tools/scripts/navigraph_admin.py`'s `semantic-model
    compile-and-activate` command. Returns 422 with structured `issues` on
    `SemanticModelValidationError` instead of a bare 500 -- the review UI
    needs to render exactly what failed, not just that something did.
    """

    try:
        model, warnings = compile_draft_to_semantic_model(
            payload.draft,
            tenant_id=payload.tenant_id,
            data_source_name=payload.data_source_name,
            version=payload.version,
        )
    except (ValidationError, KeyError) as exc:
        raise HTTPException(
            status_code=422,
            detail={"issues": [f"draft is malformed: {exc}"]},
        ) from exc

    with catalog_session_scope(request.app.state.catalog_session_factory) as session:
        try:
            result = await activate_semantic_model(model, session, request.app.state.opa_client)
        except SemanticModelValidationError as exc:
            raise HTTPException(status_code=422, detail={"issues": exc.issues}) from exc

    return {
        "tenant_id": payload.tenant_id,
        "version": payload.version,
        "tagged_pii_columns": result.tagged_pii_columns,
        "compile_warnings": warnings,
    }
