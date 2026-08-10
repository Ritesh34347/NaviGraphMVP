#!/usr/bin/env python3
"""One-time migration: seed a persisted, activated SemanticModel from
`navigraph_kg.ontology.RELATIONSHIP_CONCEPTS` for a real tenant -- Phase 1
of the configurable-platform build plan, extended in Phase 3 to also sync
that tenant's OPA policy bindings.

WHY THIS EXISTS: `navigraph_kg.ingestion.pipeline._sync_relationship_concepts`
now reads a tenant's ACTIVATED SemanticModel first (via
`navigraph_catalog.api.get_active_semantic_model`), falling back to the
hardcoded `RELATIONSHIP_CONCEPTS` list only for a tenant that has never
onboarded one. Run this once per real tenant to migrate today's exact
ingestion output into persisted, tenant-owned config, so later removing
`ontology.py`'s hardcoded list doesn't silently change what an existing
tenant's ingestion produces.

PHASE 3 ADDITION, NOT OPTIONAL BEFORE DEPLOYING THAT CHANGE:
`infra/opa/policies/authz.rego`'s `allowed_roles` rule now reads a
per-tenant OPA data document instead of a static literal, and resolves to
an EMPTY set (fail-closed) for any tenant with no synced document. This
script's model uses `PolicyBindings`' own default
(`["analyst", "pii_viewer", "admin"]`) -- identical to the OLD static
literal -- and syncs it via `sync_policy_bindings`, so running this for
every real tenant BEFORE that Rego change deploys is what keeps them from
locking themselves out on deploy day.

DELIBERATELY PRESERVES TODAY'S (ARGUABLY ACCIDENTAL) BEHAVIOR: the real
`_sync_relationship_concepts` has always synced ALL 18 `RELATIONSHIP_CONCEPTS`
entries regardless of tenant (brokerage AND e-commerce entries together, for
both `navikenz-poc` and `ecommerce-poc`). This script reproduces that
exactly, rather than partitioning per tenant -- a zero-regression migration's
job is to match current output bit-for-bit, not to fix a separate,
pre-existing scoping question. Narrowing this later is real, in-scope future
work, not this script's.

ENTITY BINDINGS ARE HEURISTIC, NOT CATALOG-VALIDATED: each entity's single
binding is derived mechanically from its first `RELATIONSHIP_CONCEPTS`
appearance (as subject or object) -- that relationship's realizing_table/key
column, not an independently-confirmed "true" identity table.
`_sync_relationship_concepts` never reads `.entities`, so this doesn't affect
ingestion output either way; real entity-binding curation is Phase 2's
onboarding-flow job, not this migration's.

Usage (needs a real, reachable Postgres catalog, with `tenant_id` already
having a default DataSource registered -- see `onboard_data_source.py
register --set-default`):

    python tools/scripts/seed_semantic_model_from_ontology.py --tenant-id navikenz-poc
    python tools/scripts/seed_semantic_model_from_ontology.py --tenant-id ecommerce-poc
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from navigraph_catalog.api import (
    activate_semantic_model,
    get_default_data_source,
    save_semantic_model,
)
from navigraph_catalog.db import get_engine, get_session_factory, session_scope
from navigraph_catalog.settings import MetadataCatalogSettings
from navigraph_kg.ontology import RELATIONSHIP_CONCEPTS
from navigraph_semantic_model.contracts import (
    Entity,
    EntityBinding,
    Relationship,
    RelationshipBinding,
    SemanticModel,
)
from navigraph_semantic_model.opa_sync import sync_policy_bindings
from navigraph_shared.opa import HttpOpaClient


def _derive_entities(
    concepts: list[dict[str, str]], *, data_source_name: str
) -> list[Entity]:
    """One `Entity` per unique subject/object label, bound to the
    realizing_table/key column of its first appearance in `concepts` -- see
    the module docstring's "ENTITY BINDINGS ARE HEURISTIC" note."""

    bindings_by_label: dict[str, tuple[str, str]] = {}
    for concept in concepts:
        bindings_by_label.setdefault(
            concept["subject_label"],
            (concept["realizing_table"], concept["subject_key_column"]),
        )
        bindings_by_label.setdefault(
            concept["object_label"],
            (concept["realizing_table"], concept["object_key_column"]),
        )

    return [
        Entity(
            name=label,
            bindings=[EntityBinding(data_source=data_source_name, table=table, key=key)],
        )
        for label, (table, key) in bindings_by_label.items()
    ]


def _derive_relationships(
    concepts: list[dict[str, str]], *, data_source_name: str
) -> list[Relationship]:
    return [
        Relationship(
            name=concept["name"],
            subject=concept["subject_label"],
            predicate=concept["predicate"],
            object=concept["object_label"],
            via=RelationshipBinding(
                data_source=data_source_name,
                table=concept["realizing_table"],
                subject_key=concept["subject_key_column"],
                object_key=concept["object_key_column"],
            ),
        )
        for concept in concepts
    ]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--version", type=int, default=1)
    args = parser.parse_args()

    settings = MetadataCatalogSettings()
    session_factory = get_session_factory(get_engine(settings))

    with session_scope(session_factory) as session:
        default_source = get_default_data_source(session, tenant_id=args.tenant_id)
        if default_source is None:
            print(
                f"Tenant {args.tenant_id!r} has no default DataSource registered -- "
                "run `onboard_data_source.py register --set-default` first.",
                file=sys.stderr,
            )
            return 1

        model = SemanticModel(
            tenant_id=args.tenant_id,
            version=args.version,
            entities=_derive_entities(
                RELATIONSHIP_CONCEPTS, data_source_name=default_source.name
            ),
            relationships=_derive_relationships(
                RELATIONSHIP_CONCEPTS, data_source_name=default_source.name
            ),
        )

        save_semantic_model(
            session,
            tenant_id=model.tenant_id,
            version=model.version,
            compiled_json=model.model_dump(mode="json"),
        )
        activate_semantic_model(session, tenant_id=model.tenant_id, version=model.version)

    asyncio.run(sync_policy_bindings(HttpOpaClient(), model))

    print(
        f"Seeded and activated SemanticModel v{model.version} for tenant {args.tenant_id!r}: "
        f"{len(model.entities)} entit(y/ies), {len(model.relationships)} relationship(s) "
        f"(matches ontology.RELATIONSHIP_CONCEPTS exactly, {len(RELATIONSHIP_CONCEPTS)} total). "
        f"Synced policy_bindings (allowed_roles={model.policy_bindings.allowed_roles}) to OPA."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
