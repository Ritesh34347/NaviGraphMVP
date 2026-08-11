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
    REL_COLUMN_OF,
    REL_IN_SECTOR,
    REL_MAPS_TO,
    REL_PART_OF_EXCHANGE,
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
    source). Also returns `table_name` (traversing `COLUMN_OF` to the
    resolved `Column`'s `Table`, OPTIONAL so a `Column` node still missing
    its `COLUMN_OF` edge doesn't drop the whole match) -- used by
    `OntologyAgent._resolve_relationships` to recognize that a
    `RelationshipConcept`'s `realizing_table` is already implied by a
    resolved business term, even when the question never literally names
    the concept's `subject_label` (see that method's docstring for the
    real gap this closes).
    """

    return client.run(
        f"""
        MATCH (bc:{NODE_BUSINESS_CONCEPT} {{tenant_id: $tenant_id}})
        WHERE toLower(bc.name) = toLower($term)
           OR any(synonym IN bc.synonyms WHERE toLower(synonym) = toLower($term))
        MATCH (bc)-[r:{REL_MAPS_TO}]->(c:{NODE_COLUMN})
        OPTIONAL MATCH (c)-[:{REL_COLUMN_OF}]->(t:{NODE_TABLE})
        RETURN bc.name AS business_concept,
               c.catalog_column_id AS catalog_column_id,
               c.name AS column_name,
               t.name AS table_name,
               r.preferred AS preferred,
               r.source AS source
        """,
        tenant_id=tenant_id,
        term=term,
    )


def list_business_concepts(client: Neo4jClient, *, tenant_id: str) -> list[dict[str, Any]]:
    """Return every real `BusinessConcept` -> `Column` mapping for a tenant,
    unconditionally (no term filter) -- the same shape `resolve_business_term`
    returns per exact match, just without the `WHERE` clause.

    Used by `OntologyAgent` as a fuzzy-matching fallback source when
    `resolve_business_term`'s exact (case-insensitive) equality match finds
    nothing for a compound extracted entity phrase (e.g. "total units
    traded" vs. the glossary's exact synonym "units traded") -- see that
    agent's `_resolve_concepts` docstring for the full fix. Kept here as a
    plain, unopinionated read (matching this module's own "no business
    logic in navigraph_kg.api" convention) rather than doing the fuzzy
    matching in Cypher; the token-sequence containment check itself lives
    in `OntologyAgent`, which already owns the equivalent normalization
    logic for relationship-concept label matching. A tenant's real
    glossary is small (on the order of dozens of concepts), so returning
    the whole set in one query is cheap and avoids N synonym-matching
    round-trips.
    """

    return client.run(
        f"""
        MATCH (bc:{NODE_BUSINESS_CONCEPT} {{tenant_id: $tenant_id}})
        MATCH (bc)-[r:{REL_MAPS_TO}]->(c:{NODE_COLUMN})
        OPTIONAL MATCH (c)-[:{REL_COLUMN_OF}]->(t:{NODE_TABLE})
        RETURN bc.name AS business_concept,
               bc.synonyms AS synonyms,
               c.catalog_column_id AS catalog_column_id,
               c.name AS column_name,
               t.name AS table_name,
               r.preferred AS preferred,
               r.source AS source
        """,
        tenant_id=tenant_id,
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

    Reads `realizing_table`/`subject_key_column`/`object_key_column`
    directly from properties on the `RelationshipConcept` node itself, NOT
    via the `REALIZES`/`SUBJECT_KEY`/`OBJECT_KEY` edge traversals -- see
    `ingestion.pipeline._sync_relationship_concepts`'s docstring for the
    real cartesian-fan-out bug this avoids (a `Column` node name like
    `CUSTOMER_ID` is never unique across a real schema, so `MERGE`-matching
    by bare name against those edges can bind to multiple nodes at once).
    """

    records = client.run(
        f"""
        MATCH (rc:{NODE_RELATIONSHIP_CONCEPT} {{
            tenant_id: $tenant_id,
            subject_label: $subject_label,
            predicate: $predicate,
            object_label: $object_label
        }})
        RETURN rc.name AS name,
               rc.realizing_table AS realizing_table,
               rc.subject_key_column AS subject_key_column,
               rc.object_key_column AS object_key_column
        LIMIT 1
        """,
        tenant_id=tenant_id,
        subject_label=subject_label,
        predicate=predicate,
        object_label=object_label,
    )
    return records[0] if records else None


def list_relationship_concepts(client: Neo4jClient, *, tenant_id: str) -> list[dict[str, Any]]:
    """Return every real `RelationshipConcept` synced for a tenant,
    unconditionally (no subject/predicate/object filter) -- the same shape
    `get_relationship_concept` returns per exact match, just without the
    `WHERE` clause, mirroring `list_business_concepts`'s identical
    "unfiltered sibling of the point-lookup" convention.

    REAL BUG this closes: `OntologyAgent._resolve_relationships` used to
    iterate the hardcoded `navigraph_kg.ontology.RELATIONSHIP_CONCEPTS`
    Python list and query the graph once per entry by its exact
    subject_label/predicate/object_label -- but a tenant's activated
    Semantic Model (see `ingestion.pipeline._load_relationship_concepts`)
    can name genuinely different predicates/directions for the same real
    join (e.g. "Order occurs on Date"/OCCURS_ON vs the hardcoded seed's
    "Order happens on Date"/HAPPENS_ON), which the static list's exact
    triples never match -- confirmed live: every relationship in a fresh
    e-commerce Semantic Model except two coincidentally-identical entries
    was invisible to query-time resolution even though ingestion had
    synced it correctly. Querying the graph directly for a tenant's whole
    relationship-concept set (which `_load_relationship_concepts` already
    keeps as the real source of truth, hardcoded-list fallback included)
    removes the static list as a second, driftable source entirely.

    Reads `realizing_table`/`subject_key_column`/`object_key_column`
    directly from properties on each `RelationshipConcept` node, NOT via
    edge traversals -- see `get_relationship_concept`'s docstring and
    `ingestion.pipeline._sync_relationship_concepts`'s for the real
    cartesian-fan-out bug this avoids (confirmed live: a single real
    relationship concept came back duplicated 6+ times from the
    edge-traversal version of this query).
    """

    return client.run(
        f"""
        MATCH (rc:{NODE_RELATIONSHIP_CONCEPT} {{tenant_id: $tenant_id}})
        RETURN rc.name AS name,
               rc.subject_label AS subject_label,
               rc.predicate AS predicate,
               rc.object_label AS object_label,
               rc.realizing_table AS realizing_table,
               rc.subject_key_column AS subject_key_column,
               rc.object_key_column AS object_key_column
        """,
        tenant_id=tenant_id,
    )


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
