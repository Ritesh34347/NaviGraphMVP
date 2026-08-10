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

from navigraph_catalog.api import (
    get_default_data_source,
    list_data_sources,
    set_default_data_source,
)
from navigraph_catalog.db import get_engine as get_catalog_engine
from navigraph_catalog.db import get_session_factory as get_catalog_session_factory
from navigraph_catalog.db import session_scope as catalog_session_scope
from navigraph_catalog.settings import MetadataCatalogSettings
from navigraph_lineage.api import get_trace, list_traces
from navigraph_lineage.db import get_engine as get_lineage_engine
from navigraph_lineage.db import get_session_factory as get_lineage_session_factory
from navigraph_lineage.db import session_scope as lineage_session_scope
from navigraph_lineage.settings import LineageSettings
from navigraph_semantic_model.activation import activate_semantic_model
from navigraph_semantic_model.loader import SemanticModelValidationError
from navigraph_semantic_model.onboarding import compile_draft_to_semantic_model
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

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
