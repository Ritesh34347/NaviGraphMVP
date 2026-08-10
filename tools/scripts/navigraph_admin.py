#!/usr/bin/env python3
"""NaviGraph admin CLI (Phase 15.2): tenant-scoped operator commands for
data sources, lineage search, and Semantic Model onboarding.

Mirrors `tools/scripts/onboard_data_source.py`'s established conventions
exactly: direct, real database access via each package's own
`session_factory`/`session_scope` (not an HTTP client against a running
service) -- the same pattern `tag_pii_columns.py` and
`onboard_data_source.py` already use for every other operator-run script
in this repo.

There is no real "list every tenant" command here, and cannot be one
yet -- this codebase has no tenant registry at all (`tenant_id` is just a
string every `DataSource`/lineage row carries); every command below is
scoped to one already-known `tenant_id`, matching that same real
limitation everywhere else in this codebase (see `navigraph_catalog.api
.list_data_sources`'s own required `tenant_id` parameter).

Usage:
    python tools/scripts/navigraph_admin.py datasource list --tenant-id acme-corp
    python tools/scripts/navigraph_admin.py datasource set-default --tenant-id acme-corp --name acme_prod_snowflake
    python tools/scripts/navigraph_admin.py lineage search --tenant-id acme-corp --agent-name query.sql_generation
    python tools/scripts/navigraph_admin.py lineage show --tenant-id acme-corp --trace-id lineage-abc123
    python tools/scripts/navigraph_admin.py semantic-model compile-and-activate \\
        --draft draft.json --tenant-id acme-corp --data-source-name acme_prod_snowflake
    python tools/scripts/navigraph_admin.py identity set-provider --tenant-id acme-corp \\
        --provider-type azure_ad --provider-settings-json '{"azure_ad_tenant_id": "...", "azure_ad_client_id": "..."}'
    python tools/scripts/navigraph_admin.py identity show --tenant-id acme-corp
    python tools/scripts/navigraph_admin.py guardrail set-thresholds --tenant-id acme-corp \\
        --role-row-limits-json '{"analyst": 8000}' --default-role-row-limit 2000
    python tools/scripts/navigraph_admin.py guardrail show --tenant-id acme-corp
    python tools/scripts/navigraph_admin.py connector list-types
    python tools/scripts/navigraph_admin.py connector describe --source-type postgres

`connector list-types`/`describe` are NOT tenant-scoped (Phase 6 of the
configurable-platform build plan) -- they describe a connector TYPE, not
any specific tenant's registration. `describe` prints
`Connector.required_settings()`'s real, declarative manifest for the
given `--source-type`, so `datasource register`'s `--connection-ref-json`
(`onboard_data_source.py`) can be filled in correctly without guessing or
reading connector source code -- see `navigraph_connectors.base
.Connector.required_settings`'s own docstring for why this defaults to an
empty list for any connector that hasn't declared one.

`guardrail set-thresholds`/`show` manage a tenant's `TenantGuardrailConfig`
row (Phase 5 of the configurable-platform build plan) -- overrides for
`QueryCostEstimatorAgent`'s hardcoded row-limit thresholds. Every
`--role-row-limits-json`/`--default-role-row-limit`/`--max-rows-cap` flag
is optional and additive-only: omit any of them (or the whole command,
for a tenant with no row at all) to keep that agent's exact hardcoded
default for that field -- `--role-row-limits-json` is a PARTIAL override,
merged over the hardcoded per-role table by the agent itself, not a full
replacement a caller has to reconstruct from scratch.

`identity set-provider`/`show` manage a tenant's `TenantIdentityConfig`
row (Phase 4 of the configurable-platform build plan) -- which identity
provider the gateway's `TenantVerifierResolver` selects for that tenant's
requests, instead of the process-wide default Azure AD verifier every
tenant used before this existed. `--provider-settings-json` is validated
against the registered provider's own `Settings` class
(`navigraph_shared.auth.registry.build_verifier`) before being persisted,
so a typo'd field name fails loudly here rather than silently at the next
real request.

`semantic-model compile-and-activate` is Phase 2's real connective step:
`onboard_data_source.py`'s `draft` command still owns drafting (and the
REQUIRED human review of its output, per that script's own docstring --
never skipped or automated here either), but once a reviewed `draft.json`
exists, this single command replaces `onboard_data_source.py`'s separate
`compile` (writes a YAML file) + `activate` (reads it back) pair with one
step that never touches an intermediate model file -- compiling straight
into `navigraph_semantic_model.activation.activate_semantic_model`'s real
validate -> tag PII -> persist -> mark active -> sync OPA sequence.
`onboard_data_source.py compile`/`activate` remain available for the
hand-edit-the-compiled-YAML-before-activating workflow; this command is
for the common case where the draft itself was the human's only edit
point.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

# Phase 6 of the configurable-platform build plan: `connector describe`/
# `list-types` need every real connector's import-side-effect
# registration to have already run -- the identical bug (and identical
# fix) Phase 2 found for `onboard_data_source.py`'s `register`/`crawl`.
import navigraph_connectors.databricks
import navigraph_connectors.postgres
import navigraph_connectors.snowflake  # noqa: F401
from navigraph_catalog.api import (
    get_default_data_source,
    get_tenant_guardrail_config,
    get_tenant_identity_config,
    list_data_sources,
    set_default_data_source,
    set_tenant_guardrail_config,
    set_tenant_identity_config,
)
from navigraph_catalog.db import get_engine as get_catalog_engine
from navigraph_catalog.db import get_session_factory as get_catalog_session_factory
from navigraph_catalog.db import session_scope as catalog_session_scope
from navigraph_catalog.settings import MetadataCatalogSettings
from navigraph_connectors.registry import (
    get_connector_class,
    list_registered_source_types,
)
from navigraph_lineage.api import get_trace, list_traces
from navigraph_lineage.db import get_engine as get_lineage_engine
from navigraph_lineage.db import get_session_factory as get_lineage_session_factory
from navigraph_lineage.db import session_scope as lineage_session_scope
from navigraph_lineage.settings import LineageSettings
from navigraph_semantic_model.activation import activate_semantic_model
from navigraph_semantic_model.loader import SemanticModelValidationError
from navigraph_semantic_model.onboarding import compile_draft_to_semantic_model
from navigraph_shared.auth.registry import build_verifier
from navigraph_shared.opa import HttpOpaClient


def cmd_datasource_list(args: argparse.Namespace) -> int:
    session_factory = get_catalog_session_factory(get_catalog_engine(MetadataCatalogSettings()))

    with catalog_session_scope(session_factory) as session:
        data_sources = list_data_sources(session, tenant_id=args.tenant_id)
        default = get_default_data_source(session, tenant_id=args.tenant_id)
        default_id = default.id if default is not None else None

        if not data_sources:
            print(f"No data sources registered for tenant {args.tenant_id!r}.")
            return 0

        for ds in data_sources:
            marker = " (default)" if ds.id == default_id else ""
            last_crawled = ds.last_crawled_at.isoformat() if ds.last_crawled_at else "never"
            print(
                f"{ds.name}{marker}\n"
                f"  id: {ds.id}\n"
                f"  source_type: {ds.source_type}\n"
                f"  last_crawled_at: {last_crawled}"
            )
    return 0


def cmd_datasource_set_default(args: argparse.Namespace) -> int:
    session_factory = get_catalog_session_factory(get_catalog_engine(MetadataCatalogSettings()))

    with catalog_session_scope(session_factory) as session:
        matching = [
            ds for ds in list_data_sources(session, tenant_id=args.tenant_id) if ds.name == args.name
        ]
        if not matching:
            print(
                f"No data source named {args.name!r} for tenant {args.tenant_id!r}.",
                file=sys.stderr,
            )
            return 1
        set_default_data_source(session, tenant_id=args.tenant_id, data_source_id=matching[0].id)

    print(f"{args.name!r} is now the default data source for tenant {args.tenant_id!r}.")
    return 0


def _parse_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def cmd_lineage_search(args: argparse.Namespace) -> int:
    session_factory = get_lineage_session_factory(get_lineage_engine(LineageSettings()))

    with lineage_session_scope(session_factory) as session:
        summaries = list_traces(
            session,
            tenant_id=args.tenant_id,
            agent_name=args.agent_name,
            since=_parse_datetime(args.since),
            until=_parse_datetime(args.until),
            search_text=args.search_text,
            limit=args.limit,
            offset=args.offset,
        )

    if not summaries:
        print(f"No matching traces for tenant {args.tenant_id!r}.")
        return 0

    for summary in summaries:
        print(
            f"{summary.trace_id}\n"
            f"  first_event_at: {summary.first_event_at.isoformat()}\n"
            f"  last_event_at: {summary.last_event_at.isoformat()}\n"
            f"  event_count: {summary.event_count}\n"
            f"  agent_names: {', '.join(summary.agent_names)}"
        )
    return 0


def cmd_lineage_show(args: argparse.Namespace) -> int:
    session_factory = get_lineage_session_factory(get_lineage_engine(LineageSettings()))

    with lineage_session_scope(session_factory) as session:
        records = get_trace(session, trace_id=args.trace_id, tenant_id=args.tenant_id)

    if not records:
        print(
            f"No events found for trace {args.trace_id!r} under tenant {args.tenant_id!r} "
            "(wrong trace_id, wrong tenant_id, or the trace hasn't been recorded yet).",
            file=sys.stderr,
        )
        return 1

    for record in records:
        print(
            f"[{record.timestamp.isoformat()}] {record.agent_name}\n"
            f"  input:  {record.input_summary}\n"
            f"  output: {record.output_summary}"
        )
    return 0


def cmd_identity_set_provider(args: argparse.Namespace) -> int:
    try:
        provider_settings = json.loads(args.provider_settings_json)
    except json.JSONDecodeError as exc:
        print(f"--provider-settings-json is not valid JSON: {exc}", file=sys.stderr)
        return 1

    try:
        build_verifier(args.provider_type, provider_settings)
    except Exception as exc:  # noqa: BLE001 -- surfaces build_verifier's real error (unregistered provider_type, or a pydantic ValidationError on provider_settings) as a clean CLI message, not a traceback
        print(f"Invalid --provider-type/--provider-settings-json: {exc}", file=sys.stderr)
        return 1

    session_factory = get_catalog_session_factory(get_catalog_engine(MetadataCatalogSettings()))
    with catalog_session_scope(session_factory) as session:
        set_tenant_identity_config(
            session,
            tenant_id=args.tenant_id,
            provider_type=args.provider_type,
            provider_settings=provider_settings,
        )

    print(
        f"Tenant {args.tenant_id!r} now uses identity provider {args.provider_type!r}: "
        f"{provider_settings}"
    )
    return 0


def cmd_identity_show(args: argparse.Namespace) -> int:
    session_factory = get_catalog_session_factory(get_catalog_engine(MetadataCatalogSettings()))

    with catalog_session_scope(session_factory) as session:
        config = get_tenant_identity_config(session, tenant_id=args.tenant_id)

    if config is None:
        print(
            f"Tenant {args.tenant_id!r} has no identity provider configured -- "
            "the gateway falls back to its process-wide default verifier."
        )
        return 0

    print(f"provider_type: {config.provider_type}\nprovider_settings: {config.provider_settings}")
    return 0


def cmd_guardrail_set_thresholds(args: argparse.Namespace) -> int:
    role_row_limits = None
    if args.role_row_limits_json is not None:
        try:
            role_row_limits = json.loads(args.role_row_limits_json)
        except json.JSONDecodeError as exc:
            print(f"--role-row-limits-json is not valid JSON: {exc}", file=sys.stderr)
            return 1
        if not isinstance(role_row_limits, dict) or not all(
            isinstance(v, int) for v in role_row_limits.values()
        ):
            print("--role-row-limits-json must be a JSON object of role -> integer.", file=sys.stderr)
            return 1

    session_factory = get_catalog_session_factory(get_catalog_engine(MetadataCatalogSettings()))
    with catalog_session_scope(session_factory) as session:
        set_tenant_guardrail_config(
            session,
            tenant_id=args.tenant_id,
            role_row_limits=role_row_limits,
            default_role_row_limit=args.default_role_row_limit,
            max_rows_cap=args.max_rows_cap,
        )

    print(
        f"Tenant {args.tenant_id!r} guardrail thresholds: "
        f"role_row_limits={role_row_limits}, "
        f"default_role_row_limit={args.default_role_row_limit}, "
        f"max_rows_cap={args.max_rows_cap} "
        "(any field left unset keeps QueryCostEstimatorAgent's hardcoded default)."
    )
    return 0


def cmd_guardrail_show(args: argparse.Namespace) -> int:
    session_factory = get_catalog_session_factory(get_catalog_engine(MetadataCatalogSettings()))

    with catalog_session_scope(session_factory) as session:
        config = get_tenant_guardrail_config(session, tenant_id=args.tenant_id)

    if config is None:
        print(
            f"Tenant {args.tenant_id!r} has no guardrail threshold overrides -- "
            "QueryCostEstimatorAgent's hardcoded defaults apply."
        )
        return 0

    print(
        f"role_row_limits: {config.role_row_limits}\n"
        f"default_role_row_limit: {config.default_role_row_limit}\n"
        f"max_rows_cap: {config.max_rows_cap}"
    )
    return 0


def cmd_connector_list_types(args: argparse.Namespace) -> int:
    for source_type in list_registered_source_types():
        print(source_type)
    return 0


def cmd_connector_describe(args: argparse.Namespace) -> int:
    try:
        connector_cls = get_connector_class(args.source_type)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    required_settings = connector_cls.required_settings()
    if not required_settings:
        print(f"{args.source_type!r} declares no required settings.")
        return 0

    print(f"Settings for --source-type {args.source_type!r} (see --connection-ref-json):")
    for setting in required_settings:
        marker = "required" if setting.required else "optional"
        line = f"  {setting.env_var} ({marker}): {setting.description}"
        if setting.condition:
            line += f" -- {setting.condition}"
        print(line)
    return 0


def cmd_semantic_model_compile_and_activate(args: argparse.Namespace) -> int:
    draft = json.loads(Path(args.draft).read_text(encoding="utf-8"))

    model, warnings = compile_draft_to_semantic_model(
        draft,
        tenant_id=args.tenant_id,
        data_source_name=args.data_source_name,
        version=args.version,
    )
    for warning in warnings:
        print(f"  [dropped] {warning}", file=sys.stderr)

    session_factory = get_catalog_session_factory(get_catalog_engine(MetadataCatalogSettings()))
    opa_client = HttpOpaClient()

    with catalog_session_scope(session_factory) as session:
        try:
            result = asyncio.run(activate_semantic_model(model, session, opa_client))
        except SemanticModelValidationError as exc:
            print(
                f"Semantic Model for tenant {model.tenant_id!r} failed catalog validation "
                f"with {len(exc.issues)} issue(s) -- NOT activated:",
                file=sys.stderr,
            )
            for issue in exc.issues:
                print(f"  - {issue}", file=sys.stderr)
            return 1

    print(
        f"Compiled {len(model.entities)} entit(y/ies), {len(model.relationships)} "
        f"relationship(s), {len(model.metrics)} metric(s) "
        f"({len(warnings)} proposal(s) dropped, see above)."
    )
    print(f"Catalog validation passed. Tagged {result.tagged_pii_columns} column(s) is_pii=true.")
    print(
        f"Synced policy_bindings for tenant {model.tenant_id!r} "
        f"(allowed_roles={model.policy_bindings.allowed_roles}) to OPA."
    )
    print(f"Semantic Model v{model.version} for tenant {model.tenant_id!r} is now active.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="resource", required=True)

    datasource_parser = subparsers.add_parser("datasource", help="Manage DataSource registrations")
    datasource_subparsers = datasource_parser.add_subparsers(dest="action", required=True)

    ds_list = datasource_subparsers.add_parser("list", help="List a tenant's registered data sources")
    ds_list.add_argument("--tenant-id", required=True)
    ds_list.set_defaults(func=cmd_datasource_list)

    ds_set_default = datasource_subparsers.add_parser(
        "set-default", help="Mark a data source as a tenant's default"
    )
    ds_set_default.add_argument("--tenant-id", required=True)
    ds_set_default.add_argument("--name", required=True)
    ds_set_default.set_defaults(func=cmd_datasource_set_default)

    lineage_parser = subparsers.add_parser("lineage", help="Search and inspect real lineage traces")
    lineage_subparsers = lineage_parser.add_subparsers(dest="action", required=True)

    lineage_search = lineage_subparsers.add_parser("search", help="Search a tenant's traces")
    lineage_search.add_argument("--tenant-id", required=True)
    lineage_search.add_argument("--agent-name", default=None)
    lineage_search.add_argument("--since", default=None, help="ISO 8601 timestamp")
    lineage_search.add_argument("--until", default=None, help="ISO 8601 timestamp")
    lineage_search.add_argument("--search-text", default=None)
    lineage_search.add_argument("--limit", type=int, default=50)
    lineage_search.add_argument("--offset", type=int, default=0)
    lineage_search.set_defaults(func=cmd_lineage_search)

    lineage_show = lineage_subparsers.add_parser("show", help="Show one trace's full event chain")
    lineage_show.add_argument("--tenant-id", required=True)
    lineage_show.add_argument("--trace-id", required=True)
    lineage_show.set_defaults(func=cmd_lineage_show)

    semantic_model_parser = subparsers.add_parser(
        "semantic-model", help="Compile and activate a reviewed ontology draft"
    )
    semantic_model_subparsers = semantic_model_parser.add_subparsers(
        dest="action", required=True
    )

    sm_compile_and_activate = semantic_model_subparsers.add_parser(
        "compile-and-activate",
        help="Compile a human-reviewed draft and activate it in one step (no intermediate model file)",
    )
    sm_compile_and_activate.add_argument(
        "--draft", required=True, help="Path to a (reviewed) draft JSON file"
    )
    sm_compile_and_activate.add_argument("--tenant-id", required=True)
    sm_compile_and_activate.add_argument("--data-source-name", required=True)
    sm_compile_and_activate.add_argument("--version", type=int, default=1)
    sm_compile_and_activate.set_defaults(func=cmd_semantic_model_compile_and_activate)

    identity_parser = subparsers.add_parser(
        "identity", help="Manage a tenant's identity-verification provider"
    )
    identity_subparsers = identity_parser.add_subparsers(dest="action", required=True)

    identity_set_provider = identity_subparsers.add_parser(
        "set-provider", help="Set (or replace) a tenant's identity provider"
    )
    identity_set_provider.add_argument("--tenant-id", required=True)
    identity_set_provider.add_argument(
        "--provider-type", required=True, help='e.g. "azure_ad" or "oidc"'
    )
    identity_set_provider.add_argument(
        "--provider-settings-json",
        required=True,
        help='e.g. \'{"azure_ad_tenant_id": "...", "azure_ad_client_id": "..."}\'',
    )
    identity_set_provider.set_defaults(func=cmd_identity_set_provider)

    identity_show = identity_subparsers.add_parser(
        "show", help="Show a tenant's configured identity provider, if any"
    )
    identity_show.add_argument("--tenant-id", required=True)
    identity_show.set_defaults(func=cmd_identity_show)

    guardrail_parser = subparsers.add_parser(
        "guardrail", help="Manage a tenant's Guardrail threshold overrides"
    )
    guardrail_subparsers = guardrail_parser.add_subparsers(dest="action", required=True)

    guardrail_set_thresholds = guardrail_subparsers.add_parser(
        "set-thresholds", help="Set (or replace) a tenant's Guardrail threshold overrides"
    )
    guardrail_set_thresholds.add_argument("--tenant-id", required=True)
    guardrail_set_thresholds.add_argument(
        "--role-row-limits-json",
        default=None,
        help='Partial override, merged over the defaults, e.g. \'{"analyst": 8000}\'',
    )
    guardrail_set_thresholds.add_argument("--default-role-row-limit", type=int, default=None)
    guardrail_set_thresholds.add_argument("--max-rows-cap", type=int, default=None)
    guardrail_set_thresholds.set_defaults(func=cmd_guardrail_set_thresholds)

    guardrail_show = guardrail_subparsers.add_parser(
        "show", help="Show a tenant's configured Guardrail threshold overrides, if any"
    )
    guardrail_show.add_argument("--tenant-id", required=True)
    guardrail_show.set_defaults(func=cmd_guardrail_show)

    connector_parser = subparsers.add_parser(
        "connector", help="Describe a connector type's real settings manifest (not tenant-scoped)"
    )
    connector_subparsers = connector_parser.add_subparsers(dest="action", required=True)

    connector_list_types = connector_subparsers.add_parser(
        "list-types", help="List every registered connector source_type"
    )
    connector_list_types.set_defaults(func=cmd_connector_list_types)

    connector_describe = connector_subparsers.add_parser(
        "describe", help="Show a connector type's required/optional settings"
    )
    connector_describe.add_argument("--source-type", required=True)
    connector_describe.set_defaults(func=cmd_connector_describe)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
