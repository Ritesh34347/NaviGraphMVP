"""Read-only query functions over the knowledge graph.

Every function here takes an already-constructed `Neo4jClient` (dependency
injection, same convention as `navigraph_catalog.api`'s functions taking an
already-open `Session`) and a `tenant_id` -- every Cypher query below filters
by `tenant_id` explicitly, matching the "multi-tenancy is property-based"
design decision. None of these functions ever writes to the graph; see
`navigraph_kg.ingestion.pipeline` for the write path.
"""

from __future__ import annotations

from typing import Any

from navigraph_kg.client import Neo4jClient
from navigraph_kg.ontology import (
    NODE_ASSET,
    NODE_BUSINESS_CONCEPT,
    NODE_COLUMN,
    NODE_EXCHANGE,
    NODE_MARKET,
    NODE_RELATIONSHIP_CONCEPT,
    NODE_SECTOR,
    NODE_TABLE,
    REL_IN_SECTOR,
    REL_MAPS_TO,
    REL_OBJECT_KEY,
    REL_PART_OF_EXCHANGE,
    REL_REALIZES,
    REL_SUBJECT_KEY,
)


def resolve_business_term(
    client: Neo4jClient, *, tenant_id: str, term: str
) -> list[dict[str, Any]]:
    """Resolve `term` to the `Column`(s) it maps to via `BusinessConcept`.

    Matches case-insensitively against `BusinessConcept.name` OR any entry
    in `BusinessConcept.synonyms` -- a caller has no way of knowing in
    advance whether a user's phrasing matches the canonical business name or
    one of its synonyms, so both are checked in one query. `preferred` is
    included in every result so a caller can disambiguate when a term
    resolves to more than one `Column` (e.g. via more than one glossary
    source).
    """

    return client.run(
        f"""
        MATCH (bc:{NODE_BUSINESS_CONCEPT} {{tenant_id: $tenant_id}})
        WHERE toLower(bc.name) = toLower($term)
           OR any(synonym IN bc.synonyms WHERE toLower(synonym) = toLower($term))
        MATCH (bc)-[r:{REL_MAPS_TO}]->(c:{NODE_COLUMN})
        RETURN bc.name AS business_concept,
               c.catalog_column_id AS catalog_column_id,
               c.name AS column_name,
               r.preferred AS preferred,
               r.source AS source
        """,
        tenant_id=tenant_id,
        term=term,
    )


def get_column_for_concept(
    client: Neo4jClient, *, tenant_id: str, concept_name: str
) -> dict[str, Any] | None:
    """Look up the (preferred) `Column` a `BusinessConcept` maps to by exact name."""

    records = client.run(
        f"""
        MATCH (bc:{NODE_BUSINESS_CONCEPT} {{tenant_id: $tenant_id, name: $concept_name}})
              -[r:{REL_MAPS_TO}]->(c:{NODE_COLUMN})
        RETURN c.catalog_column_id AS catalog_column_id,
               c.name AS column_name,
               r.preferred AS preferred
        ORDER BY r.preferred DESC
        LIMIT 1
        """,
        tenant_id=tenant_id,
        concept_name=concept_name,
    )
    return records[0] if records else None


# Property names checked by `entity_matches_reference_node` when a category's
# node doesn't use the default `name` property -- e.g. `Asset` (see
# `ingestion.pipeline._sync_reference_data`) uses `asset_name`/
# `asset_short_name` instead.
_REFERENCE_NAME_PROPERTIES: dict[str, tuple[str, ...]] = {
    NODE_ASSET: ("asset_name", "asset_short_name", "isin"),
}
_DEFAULT_REFERENCE_NAME_PROPERTY = "name"


def entity_matches_reference_node(
    client: Neo4jClient,
    *,
    tenant_id: str,
    label: str,
    entity: str,
) -> bool:
    """Real bug, found live: a relationship concept's category label (e.g.
    `"Market"`) only ever matched an extracted entity that literally
    contained the word "market" -- a question naming a SPECIFIC market
    ("Athens Exchange") instead of the generic category word never
    matched, so e.g. "Transaction happens in Market" never fired for any
    question naming a real market by name. This checks the free-text
    `entity` string against REAL reference-data node values under `label`
    (Market/Asset/Channel/RiskLevel/CustomerType/etc. -- all real,
    crawled Tier-1 nodes, see `ingestion.pipeline._sync_reference_data`),
    so a real instance name (or a real instance name that's a substring/
    superstring of the extracted entity, e.g. Intent Understanding
    extracting "Athens Exchange" for the real market name "Athens
    Exchange S.A. Cash Market") counts as a match too, not just the
    literal category word.

    `label` always comes from this codebase's own curated
    `RELATIONSHIP_CONCEPTS` seed data (never user input), so
    string-interpolating it into the Cypher label position here is safe.
    """

    if not entity.strip():
        return False

    properties = _REFERENCE_NAME_PROPERTIES.get(label, (_DEFAULT_REFERENCE_NAME_PROPERTY,))
    where_clause = " OR ".join(
        f"(n.{prop} IS NOT NULL AND "
        f"(toLower(n.{prop}) CONTAINS toLower($entity) OR toLower($entity) CONTAINS toLower(n.{prop})))"
        for prop in properties
    )

    records = client.run(
        f"""
        MATCH (n:{label} {{tenant_id: $tenant_id}})
        WHERE {where_clause}
        RETURN n
        LIMIT 1
        """,
        tenant_id=tenant_id,
        entity=entity,
    )
    return bool(records)


def get_relationship_concept(
    client: Neo4jClient,
    *,
    tenant_id: str,
    subject_label: str,
    predicate: str,
    object_label: str,
) -> dict[str, Any] | None:
    """Look up a hand-curated `RelationshipConcept` by its (subject, predicate, object) shape.

    Returns the realizing table name and the subject/object key column
    names -- exactly what a caller needs to generate the SQL join this
    concept describes, since the graph itself never materializes
    customer-cardinality relationship data (see
    `navigraph_kg.ingestion.pipeline`'s module docstring).
    """

    records = client.run(
        f"""
        MATCH (rc:{NODE_RELATIONSHIP_CONCEPT} {{
            tenant_id: $tenant_id,
            subject_label: $subject_label,
            predicate: $predicate,
            object_label: $object_label
        }})
        OPTIONAL MATCH (t:{NODE_TABLE})-[:{REL_REALIZES}]->(rc)
        OPTIONAL MATCH (rc)-[:{REL_SUBJECT_KEY}]->(sc:{NODE_COLUMN})
        OPTIONAL MATCH (rc)-[:{REL_OBJECT_KEY}]->(oc:{NODE_COLUMN})
        RETURN rc.name AS name,
               t.name AS realizing_table,
               sc.name AS subject_key_column,
               oc.name AS object_key_column
        LIMIT 1
        """,
        tenant_id=tenant_id,
        subject_label=subject_label,
        predicate=predicate,
        object_label=object_label,
    )
    return records[0] if records else None


def list_assets_by_sector(
    client: Neo4jClient, *, tenant_id: str, sector_name: str
) -> list[dict[str, Any]]:
    """List every `Asset` in a given `Sector`, by exact sector name."""

    return client.run(
        f"""
        MATCH (a:{NODE_ASSET} {{tenant_id: $tenant_id}})
              -[:{REL_IN_SECTOR}]->(s:{NODE_SECTOR} {{tenant_id: $tenant_id, name: $sector_name}})
        RETURN a.isin AS isin,
               a.asset_name AS asset_name,
               a.asset_category AS asset_category,
               a.asset_sub_category AS asset_sub_category
        """,
        tenant_id=tenant_id,
        sector_name=sector_name,
    )


def get_asset(client: Neo4jClient, *, tenant_id: str, isin: str) -> dict[str, Any] | None:
    """Look up a single `Asset` by its ISIN."""

    records = client.run(
        f"""
        MATCH (a:{NODE_ASSET} {{tenant_id: $tenant_id, isin: $isin}})
        RETURN a.isin AS isin,
               a.asset_name AS asset_name,
               a.asset_short_name AS asset_short_name,
               a.asset_category AS asset_category,
               a.asset_sub_category AS asset_sub_category
        LIMIT 1
        """,
        tenant_id=tenant_id,
        isin=isin,
    )
    return records[0] if records else None


def list_markets_for_exchange(
    client: Neo4jClient, *, tenant_id: str, exchange_id: str
) -> list[dict[str, Any]]:
    """List every `Market` grouped under a given `Exchange` (a real, genuine 1-to-many).

    E.g. exchange `ATHEX` groups three real markets (`EBB`, `XATH`,
    `ENAX`) in the live `FIDELITY_POC` data -- this is exactly the query
    that proves that modeling decision is actually queryable, not just
    theoretically correct.
    """

    return client.run(
        f"""
        MATCH (m:{NODE_MARKET} {{tenant_id: $tenant_id}})
              -[:{REL_PART_OF_EXCHANGE}]->(e:{NODE_EXCHANGE} {{tenant_id: $tenant_id, exchange_id: $exchange_id}})
        RETURN m.market_id AS market_id,
               m.name AS name,
               m.country AS country
        """,
        tenant_id=tenant_id,
        exchange_id=exchange_id,
    )
