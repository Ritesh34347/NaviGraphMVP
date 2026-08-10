#!/usr/bin/env python3
"""Onboard a new data source end-to-end: register it, crawl its schema,
draft a candidate Semantic Model with the Ontology Drafting agent, compile
a human-reviewed draft into a real `SemanticModel`, and activate it
(catalog validation, PII compilation, persistence + activation in
`metadata_catalog`'s `semantic_models` table, OPA policy sync).

Five subcommands, meant to be run in this order -- see
`docs/runbooks/data-source-onboarding.md` for the full walkthrough,
including the REQUIRED human review step between `draft` and `compile`
that this script deliberately does not skip or automate:

    onboard_data_source.py register  --tenant-id ... --name ... --source-type ... --connection-ref-json '...'
    onboard_data_source.py crawl     --tenant-id ... --data-source-name ...
    onboard_data_source.py draft     --tenant-id ... --data-source-name ... --out draft.json
    # <-- a human reviews/edits draft.json here, per-proposal, before continuing -->
    onboard_data_source.py compile   --draft draft.json --tenant-id ... --data-source-name ... --out semantic_model.yaml
    onboard_data_source.py activate  --model semantic_model.yaml

`register`/`crawl`/`activate` need a real, reachable Postgres catalog
(and `crawl` additionally needs the real source system -- Snowflake,
another Postgres, etc. -- reachable via that `DataSource`'s own
credentials). `draft` additionally needs a real `ANTHROPIC_API_KEY` (falls
back to `FakeLLMClient` with a loud warning otherwise, exactly like the
agent-runtime service itself -- see `main.py._build_llm_client`).
`compile` is the one fully offline step: it only reads `--draft` and
writes `--out`, so a human can review/edit `draft.json` on a laptop with
no infra access at all before a `compile`+`activate` pair is run
somewhere that has it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

# REAL BUG, found live wiring Phase 2: `register`/`crawl` call
# `get_connector_class`, but a connector is only ever registered as an
# IMPORT SIDE EFFECT of importing its own submodule -- this script never
# did, so a fresh CLI invocation always failed with "No connector
# registered for source_type='...'". Same exact bug, same fix, as
# `navigraph_agents.main`'s own identical import block -- see that
# module's comment for the original live discovery.
import navigraph_connectors.databricks
import navigraph_connectors.postgres
import navigraph_connectors.snowflake  # noqa: F401
import yaml
from navigraph_catalog.api import list_data_sources, register_data_source
from navigraph_catalog.db import get_engine, get_session_factory, session_scope
from navigraph_catalog.ingestion.snowflake_crawler import crawl_and_store
from navigraph_catalog.settings import MetadataCatalogSettings
from navigraph_connectors.registry import get_connector_class
from navigraph_semantic_model.activation import activate_semantic_model
from navigraph_semantic_model.loader import (
    SemanticModelValidationError,
    load_semantic_model,
)
from navigraph_semantic_model.onboarding import compile_draft_to_semantic_model
from navigraph_shared.config import get_settings
from navigraph_shared.contracts import RequestContext
from navigraph_shared.llm import AnthropicLLMClient, FakeLLMClient, LLMClient
from navigraph_shared.opa import HttpOpaClient


def _build_llm_client() -> LLMClient:
    """Mirrors `navigraph_agents.main._build_llm_client` exactly -- this
    script is a standalone operational tool, not a caller of the
    agent-runtime service, so it builds its own client rather than
    importing a private helper out of a FastAPI app module."""

    settings = get_settings()
    if settings.anthropic_api_key:
        return AnthropicLLMClient(api_key=settings.anthropic_api_key, model=settings.anthropic_model)

    print(
        "WARNING: ANTHROPIC_API_KEY is not set -- falling back to FakeLLMClient. "
        "The drafted ontology will be empty/canned, not a real proposal. Set "
        "ANTHROPIC_API_KEY to draft for real.",
        file=sys.stderr,
    )
    return FakeLLMClient()


def _find_data_source(session, *, tenant_id: str, data_source_name: str):
    matching = [
        ds for ds in list_data_sources(session, tenant_id=tenant_id) if ds.name == data_source_name
    ]
    if not matching:
        print(
            f"No data source named {data_source_name!r} for tenant {tenant_id!r}.",
            file=sys.stderr,
        )
        return None
    return matching[0]


def cmd_register(args: argparse.Namespace) -> int:
    try:
        connection_ref = json.loads(args.connection_ref_json)
    except json.JSONDecodeError as exc:
        print(f"--connection-ref-json is not valid JSON: {exc}", file=sys.stderr)
        return 1

    settings = MetadataCatalogSettings()
    session_factory = get_session_factory(get_engine(settings))

    with session_scope(session_factory) as session:
        try:
            data_source = register_data_source(
                session,
                tenant_id=args.tenant_id,
                name=args.name,
                source_type=args.source_type,
                connection_ref=connection_ref,
                is_default=args.set_default,
            )
        except ValueError as exc:
            print(f"Registration failed: {exc}", file=sys.stderr)
            return 1
        data_source_id = data_source.id

    print(f"Registered data source {args.name!r} for tenant {args.tenant_id!r}: id={data_source_id}")
    return 0


def cmd_crawl(args: argparse.Namespace) -> int:
    settings = MetadataCatalogSettings()
    session_factory = get_session_factory(get_engine(settings))

    with session_scope(session_factory) as session:
        data_source = _find_data_source(
            session, tenant_id=args.tenant_id, data_source_name=args.data_source_name
        )
        if data_source is None:
            return 1

        # REAL BUG, found live wiring Phase 2: this called a
        # `navigraph_connectors.registry.build_connector` that never
        # existed in this SDK at all -- every real caller (Data Source
        # Discovery, Data Federation) constructs a connector the same,
        # documented way instead: the class reads its own global,
        # env-var-backed settings (e.g. `SnowflakeConnector.__init__`'s
        # `SnowflakeSettings()` default) rather than being handed
        # `DataSource.connection_ref`/secrets directly -- there is no
        # per-DataSource credential-routing layer anywhere in this
        # codebase yet (a real, separately-tracked limitation, not
        # something to invent here). This never had a test exercising it
        # and isn't in CI's mypy scope (`tools/scripts/` isn't checked),
        # so it was invisible until read closely.
        connector_cls = get_connector_class(data_source.source_type)
        connector = connector_cls()
        result = crawl_and_store(session, data_source_id=data_source.id, connector=connector)

    print(f"Crawled {args.data_source_name!r}: {result.tables_synced} table(s) synced.")
    if result.new_table_names:
        print(f"  New tables: {', '.join(result.new_table_names)}")
    if result.changed_table_names:
        print(f"  Changed tables (schema drift detected): {', '.join(result.changed_table_names)}")
    if not result.new_table_names and not result.changed_table_names:
        print("  No drift since the last crawl.")
    return 0


def cmd_draft(args: argparse.Namespace) -> int:
    # Imported lazily -- this is the one subcommand that depends on
    # `navigraph_agents` (the Ontology Drafting agent itself), which the
    # other four subcommands have no need to import.
    from navigraph_agents.understanding.ontology_drafting.agent import (
        OntologyDraftingAgent,
    )
    from navigraph_agents.understanding.ontology_drafting.contracts import (
        OntologyDraftingInput,
        OntologyDraftingPayload,
    )

    settings = MetadataCatalogSettings()
    session_factory = get_session_factory(get_engine(settings))

    with session_scope(session_factory) as session:
        data_source = _find_data_source(
            session, tenant_id=args.tenant_id, data_source_name=args.data_source_name
        )
        if data_source is None:
            return 1
        data_source_id = str(data_source.id)

    agent = OntologyDraftingAgent(llm_client=_build_llm_client(), session_factory=session_factory)
    output = asyncio.run(
        agent.run(
            OntologyDraftingInput(
                request_context=RequestContext(
                    tenant_id=args.tenant_id,
                    user_id="onboarding-cli",
                    trace_id=str(uuid.uuid4()),
                    roles=["admin"],
                ),
                payload=OntologyDraftingPayload(data_source_id=data_source_id),
            )
        )
    )

    for error in output.errors:
        marker = "FATAL" if not error.recoverable else "warning"
        print(f"  [{marker}] {error.code}: {error.message}", file=sys.stderr)

    result = output.result
    Path(args.out).write_text(json.dumps(result.model_dump(), indent=2), encoding="utf-8")
    print(
        f"Drafted {len(result.entities)} entit(y/ies), {len(result.relationships)} "
        f"relationship(s), {len(result.sensitive_columns)} sensitive column(s), and "
        f"{len(result.metrics)} metric(s) -> {args.out}"
    )
    print(
        "REVIEW THIS FILE BY HAND before running `compile` -- every proposal carries "
        "a rationale specifically so you can judge it; edit or delete any entry that "
        "doesn't look right. See docs/runbooks/data-source-onboarding.md."
    )
    return 0 if not any(not e.recoverable for e in output.errors) else 1


def cmd_compile(args: argparse.Namespace) -> int:
    draft = json.loads(Path(args.draft).read_text(encoding="utf-8"))

    model, warnings = compile_draft_to_semantic_model(
        draft,
        tenant_id=args.tenant_id,
        data_source_name=args.data_source_name,
        version=args.version,
    )

    for warning in warnings:
        print(f"  [dropped] {warning}", file=sys.stderr)

    Path(args.out).write_text(
        yaml.safe_dump(model.model_dump(mode="json"), sort_keys=False), encoding="utf-8"
    )
    print(
        f"Compiled {len(model.entities)} entit(y/ies), {len(model.relationships)} "
        f"relationship(s), {len(model.metrics)} metric(s) -> {args.out} "
        f"({len(warnings)} proposal(s) dropped, see above)"
    )
    print("Run `activate` against a real catalog to validate and apply this model.")
    return 0


def cmd_activate(args: argparse.Namespace) -> int:
    model = load_semantic_model(args.model)

    settings = MetadataCatalogSettings()
    session_factory = get_session_factory(get_engine(settings))
    opa_client = HttpOpaClient()

    with session_scope(session_factory) as session:
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

    print(f"Catalog validation passed. Tagged {result.tagged_pii_columns} column(s) is_pii=true.")
    print(
        f"Synced policy_bindings for tenant {model.tenant_id!r} "
        f"(allowed_roles={model.policy_bindings.allowed_roles}) to OPA."
    )
    print(f"Semantic Model v{model.version} for tenant {model.tenant_id!r} is now active.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    register_parser = subparsers.add_parser("register", help="Register a new DataSource")
    register_parser.add_argument("--tenant-id", required=True)
    register_parser.add_argument("--name", required=True)
    register_parser.add_argument("--source-type", required=True)
    register_parser.add_argument(
        "--connection-ref-json", required=True, help='e.g. \'{"secret_scope": "my-tenant-prod"}\''
    )
    register_parser.add_argument("--set-default", action="store_true")
    register_parser.set_defaults(func=cmd_register)

    crawl_parser = subparsers.add_parser("crawl", help="Crawl a registered DataSource's schema")
    crawl_parser.add_argument("--tenant-id", required=True)
    crawl_parser.add_argument("--data-source-name", required=True)
    crawl_parser.set_defaults(func=cmd_crawl)

    draft_parser = subparsers.add_parser(
        "draft", help="Draft candidate entities/relationships/metrics with the Ontology Drafting agent"
    )
    draft_parser.add_argument("--tenant-id", required=True)
    draft_parser.add_argument("--data-source-name", required=True)
    draft_parser.add_argument("--out", required=True, help="Path to write the draft JSON to")
    draft_parser.set_defaults(func=cmd_draft)

    compile_parser = subparsers.add_parser(
        "compile", help="Compile a human-reviewed draft JSON into a SemanticModel YAML"
    )
    compile_parser.add_argument("--draft", required=True, help="Path to a (reviewed) draft JSON file")
    compile_parser.add_argument("--tenant-id", required=True)
    compile_parser.add_argument("--data-source-name", required=True)
    compile_parser.add_argument("--out", required=True, help="Path to write the SemanticModel YAML to")
    compile_parser.add_argument("--version", type=int, default=1)
    compile_parser.set_defaults(func=cmd_compile)

    activate_parser = subparsers.add_parser(
        "activate", help="Validate a SemanticModel YAML against the live catalog and apply it"
    )
    activate_parser.add_argument("--model", required=True, help="Path to a SemanticModel YAML file")
    activate_parser.set_defaults(func=cmd_activate)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
