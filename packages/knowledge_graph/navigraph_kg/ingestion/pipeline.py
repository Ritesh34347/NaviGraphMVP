"""Build/refresh the knowledge graph from the metadata catalog and Snowflake.

`run_ingestion` is the single entry point, running four ordered, idempotent
stages against an already-open catalog `Session`, a `Neo4jClient`, a
`Connector`, and a per-tenant `SemanticModel` (dependency-injected, same
pattern as `navigraph_catalog.ingestion.snowflake_crawler.crawl_and_store`):

  1. `_sync_schema_structure` -- crawl `Table`/`Column` proxy nodes from
     `navigraph_catalog.api.list_tables`/`list_columns`.
  2. `_sync_business_glossary` -- crawl `BusinessConcept` nodes and their
     `MAPS_TO` edges from `navigraph_catalog.api.list_glossary`.
  3. `_sync_reference_data` -- crawl real Tier-1 reference/dimension nodes.
     `Asset`/`Market`/`Exchange`/`Sector`/`Industry` still come from
     `reference_data_queries.py`'s dedicated, richer (edge-producing)
     crawl logic -- deliberately NOT generalized in this pass, see this
     module's "Deliberate scope note" below. `Channel`/`CustomerType`/
     `RiskLevel`/`InvestmentCapacityBand` (the four *simple* lookups, all
     sharing one exact SQL shape) now compile from `semantic_model
     .reference_lookups` instead of four hardcoded query constants.
  4. `_sync_relationship_concepts` -- compile `RelationshipConcept` nodes
     from `semantic_model.relationships`, replacing the hand-curated,
     hardcoded `navigraph_kg.ontology.RELATIONSHIP_CONCEPTS` list
     (LIMITATIONS.md item 38's structural half: a missing relationship
     concept is now a Semantic Model authoring/review gap, not a silent
     Python-list omission nobody thought to update).

Every stage uses Cypher `MERGE` (never bare `CREATE`), so re-running
`run_ingestion` for the same `data_source_id`/`tenant_id` is safe and
produces identical node/relationship counts -- see
`tests/integration/knowledge_graph/test_ingestion_integration.py` for the
real idempotency proof against a live Neo4j + Snowflake. Every node and
relationship touched by any stage gets `active = true` and
`last_synced_at = <this run's timestamp>` set on every upsert (the "soft
staleness" design decision) -- nothing is ever hard-deleted here. A future
"mark all inactive, then this run reactivates what it still sees" pass
would run as a first step before stage 1 in a real re-crawl scheduler; this
module only owns the reactivation half, which is genuinely all four stages
need to stay correct on every call.

CUSTOMERS AND TRANSACTIONS ARE DELIBERATELY OUT OF SCOPE. No stage here ever
creates a `Customer` or `Transaction` node, and no per-customer-cardinality
data (not even pre-aggregated rows) is ever materialized into Neo4j -- a
`RelationshipConcept` compiled from `semantic_model.relationships` still
only ever describes *how* to join Snowflake tables for SQL generation, not
a graph edge over real customer/transaction nodes.

DELIBERATE SCOPE NOTE (Phase 12.2): `Asset`/`Market`'s richer crawl (their
own dedicated queries, conditional `LISTED_ON`/`IN_SECTOR`/`IN_INDUSTRY`
edge derivation) is NOT generalized into the Semantic Model in this pass --
only the four simple lookups, which genuinely share one uniform query
shape, are. Forcing Asset/Market's real, non-uniform business logic into a
generic schema without a proper design pass would risk a rushed,
half-correct generalization of a real financial data pipeline; this is
named here rather than silently left half-done. See LIMITATIONS.md for the
tracked follow-up.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from navigraph_catalog.api import list_columns, list_glossary, list_tables
from navigraph_connectors.base import Connector
from navigraph_semantic_model import ReferenceLookup, Relationship, SemanticModel
from sqlalchemy.orm import Session

from navigraph_kg.client import Neo4jClient
from navigraph_kg.ingestion.reference_data_queries import (
    ASSET_INFORMATION_QUERY,
    MARKETS_QUERY,
)
from navigraph_kg.models import AssetRecord, IngestionSummary, MarketRecord
from navigraph_kg.ontology import (
    NODE_ASSET,
    NODE_BUSINESS_CONCEPT,
    NODE_CHANNEL,
    NODE_COLUMN,
    NODE_CUSTOMER_TYPE,
    NODE_EXCHANGE,
    NODE_INDUSTRY,
    NODE_INVESTMENT_CAPACITY_BAND,
    NODE_MARKET,
    NODE_RELATIONSHIP_CONCEPT,
    NODE_RISK_LEVEL,
    NODE_SECTOR,
    NODE_TABLE,
    REL_COLUMN_OF,
    REL_IN_INDUSTRY,
    REL_IN_SECTOR,
    REL_LISTED_ON,
    REL_MAPS_TO,
    REL_OBJECT_KEY,
    REL_PART_OF_EXCHANGE,
    REL_REALIZES,
    REL_SUBJECT_KEY,
)

# The fixed, schema-constrained set of "simple lookup" Tier-1 labels a
# Semantic Model's `reference_lookups` may populate -- see
# `navigraph_kg.ontology.SCHEMA_CONSTRAINTS` (every one of these has a real
# uniqueness constraint) and `ReferenceLookup`'s own docstring for why this
# is a closed set, not an arbitrary caller-chosen label.
_SIMPLE_LOOKUP_LABELS: dict[str, str] = {
    NODE_CHANNEL: "channels",
    NODE_CUSTOMER_TYPE: "customer_types",
    NODE_RISK_LEVEL: "risk_levels",
    NODE_INVESTMENT_CAPACITY_BAND: "investment_capacity_bands",
}


def run_ingestion(
    catalog_session: Session,
    neo4j_client: Neo4jClient,
    connector: Connector,
    semantic_model: SemanticModel,
    *,
    data_source_id: uuid.UUID,
    tenant_id: str,
) -> IngestionSummary:
    """Run all four ingestion stages and return per-stage counts.

    `semantic_model.tenant_id` must equal `tenant_id` -- ingesting one
    tenant's data using another tenant's Semantic Model is a real,
    catchable bug, not something to silently allow.
    """

    if semantic_model.tenant_id != tenant_id:
        raise ValueError(
            f"semantic_model.tenant_id={semantic_model.tenant_id!r} does not match "
            f"tenant_id={tenant_id!r} -- refusing to ingest one tenant's data using "
            "another tenant's Semantic Model"
        )

    synced_at = datetime.now(UTC)

    tables_synced, columns_synced = _sync_schema_structure(
        catalog_session,
        neo4j_client,
        data_source_id=data_source_id,
        tenant_id=tenant_id,
        synced_at=synced_at,
    )
    business_concepts_synced, concept_mappings_synced = _sync_business_glossary(
        catalog_session,
        neo4j_client,
        data_source_id=data_source_id,
        tenant_id=tenant_id,
        synced_at=synced_at,
    )
    reference_counts = _sync_reference_data(
        neo4j_client,
        connector,
        reference_lookups=semantic_model.reference_lookups,
        tenant_id=tenant_id,
        synced_at=synced_at,
    )
    relationship_concepts_synced = _sync_relationship_concepts(
        neo4j_client,
        relationships=semantic_model.relationships,
        tenant_id=tenant_id,
        synced_at=synced_at,
    )

    return IngestionSummary(
        tables_synced=tables_synced,
        columns_synced=columns_synced,
        business_concepts_synced=business_concepts_synced,
        concept_mappings_synced=concept_mappings_synced,
        assets_synced=reference_counts["assets"],
        markets_synced=reference_counts["markets"],
        exchanges_synced=reference_counts["exchanges"],
        sectors_synced=reference_counts["sectors"],
        industries_synced=reference_counts["industries"],
        channels_synced=reference_counts["channels"],
        customer_types_synced=reference_counts["customer_types"],
        risk_levels_synced=reference_counts["risk_levels"],
        investment_capacity_bands_synced=reference_counts["investment_capacity_bands"],
        relationship_concepts_synced=relationship_concepts_synced,
    )


def _sync_schema_structure(
    catalog_session: Session,
    neo4j_client: Neo4jClient,
    *,
    data_source_id: uuid.UUID,
    tenant_id: str,
    synced_at: datetime,
) -> tuple[int, int]:
    """Stage 1: `Table`/`Column` proxy nodes from the Postgres catalog."""

    synced_at_iso = synced_at.isoformat()
    tables_synced = 0
    columns_synced = 0

    for table in list_tables(catalog_session, data_source_id=data_source_id):
        neo4j_client.run(
            f"""
            MERGE (t:{NODE_TABLE} {{catalog_table_id: $catalog_table_id}})
            SET t.tenant_id = $tenant_id,
                t.name = $name,
                t.active = true,
                t.last_synced_at = $synced_at
            """,
            catalog_table_id=str(table.id),
            tenant_id=tenant_id,
            name=table.name,
            synced_at=synced_at_iso,
        )
        tables_synced += 1

        for column in list_columns(catalog_session, table_id=table.id):
            neo4j_client.run(
                f"""
                MERGE (c:{NODE_COLUMN} {{catalog_column_id: $catalog_column_id}})
                SET c.tenant_id = $tenant_id,
                    c.name = $name,
                    c.data_type = $data_type,
                    c.active = true,
                    c.last_synced_at = $synced_at
                WITH c
                MATCH (t:{NODE_TABLE} {{catalog_table_id: $catalog_table_id_for_table}})
                MERGE (c)-[r:{REL_COLUMN_OF}]->(t)
                SET r.active = true, r.last_synced_at = $synced_at
                """,
                catalog_column_id=str(column.id),
                tenant_id=tenant_id,
                name=column.name,
                data_type=column.data_type,
                catalog_table_id_for_table=str(table.id),
                synced_at=synced_at_iso,
            )
            columns_synced += 1

    return tables_synced, columns_synced


def _sync_business_glossary(
    catalog_session: Session,
    neo4j_client: Neo4jClient,
    *,
    data_source_id: uuid.UUID,
    tenant_id: str,
    synced_at: datetime,
) -> tuple[int, int]:
    """Stage 2: `BusinessConcept` nodes + `MAPS_TO` edges from the glossary.

    Only builds `BusinessConcept` nodes from real `ColumnGlossary` rows -- a
    column with no glossary entry simply has no `BusinessConcept`, which is a
    legitimate, surfaced state, not a gap this stage synthesizes around (per
    the approved "no auto-fabricated BusinessConcepts" design decision).
    """

    synced_at_iso = synced_at.isoformat()
    business_concepts_synced = 0
    concept_mappings_synced = 0

    for glossary_entry in list_glossary(catalog_session, data_source_id=data_source_id):
        neo4j_client.run(
            f"""
            MERGE (bc:{NODE_BUSINESS_CONCEPT} {{tenant_id: $tenant_id, name: $business_name}})
            SET bc.synonyms = $synonyms,
                bc.description = $description,
                bc.active = true,
                bc.last_synced_at = $synced_at
            WITH bc
            MATCH (c:{NODE_COLUMN} {{catalog_column_id: $catalog_column_id}})
            MERGE (bc)-[r:{REL_MAPS_TO}]->(c)
            SET r.source = $source,
                r.preferred = true,
                r.active = true,
                r.last_synced_at = $synced_at
            """,
            tenant_id=tenant_id,
            business_name=glossary_entry.business_name,
            synonyms=list(glossary_entry.synonyms),
            description=glossary_entry.description,
            catalog_column_id=str(glossary_entry.column_id),
            source=glossary_entry.source,
            synced_at=synced_at_iso,
        )
        business_concepts_synced += 1
        concept_mappings_synced += 1

    return business_concepts_synced, concept_mappings_synced


def _sync_reference_data(
    neo4j_client: Neo4jClient,
    connector: Connector,
    *,
    reference_lookups: list[ReferenceLookup],
    tenant_id: str,
    synced_at: datetime,
) -> dict[str, int]:
    """Stage 3: real Tier-1 reference/dimension nodes crawled from Snowflake.

    Exchanges + Markets are synced BEFORE Assets so the `LISTED_ON` edge
    below can `MATCH` an already-existing `Market` node; `IN_SECTOR`/
    `IN_INDUSTRY` edges are created only when the source value is non-null,
    per the approved design decision against fabricating placeholder
    `Sector`/`Industry` nodes for nulls (~half of real assets legitimately
    have neither -- see `models.AssetRecord`'s docstring).

    `Channel`/`CustomerType`/`RiskLevel`/`InvestmentCapacityBand` are
    driven entirely by `reference_lookups` -- a label this Semantic Model
    doesn't declare a lookup for gets NO nodes synced this run, which is
    the intended, real behavior change from the old unconditional
    "always sync all four" hardcoding.
    """

    synced_at_iso = synced_at.isoformat()
    counts = {
        "assets": 0,
        "markets": 0,
        "exchanges": 0,
        "sectors": 0,
        "industries": 0,
        "channels": 0,
        "customer_types": 0,
        "risk_levels": 0,
        "investment_capacity_bands": 0,
    }

    seen_exchanges: set[str] = set()
    for row in connector.execute_query(MARKETS_QUERY).rows:
        market = MarketRecord(
            exchange_id=row["EXCHANGEID"],
            market_id=row["MARKETID"],
            name=row.get("NAME"),
            description=row.get("DESCRIPTION"),
            country=row.get("COUNTRY"),
            trading_days=row.get("TRADINGDAYS"),
            trading_hours=row.get("TRADINGHOURS"),
            market_class=row.get("MARKETCLASS"),
        )
        neo4j_client.run(
            f"""
            MERGE (e:{NODE_EXCHANGE} {{tenant_id: $tenant_id, exchange_id: $exchange_id}})
            SET e.active = true, e.last_synced_at = $synced_at
            MERGE (m:{NODE_MARKET} {{tenant_id: $tenant_id, market_id: $market_id}})
            SET m.name = $name,
                m.description = $description,
                m.country = $country,
                m.trading_days = $trading_days,
                m.trading_hours = $trading_hours,
                m.market_class = $market_class,
                m.active = true,
                m.last_synced_at = $synced_at
            MERGE (m)-[r:{REL_PART_OF_EXCHANGE}]->(e)
            SET r.active = true, r.last_synced_at = $synced_at
            """,
            tenant_id=tenant_id,
            exchange_id=market.exchange_id,
            market_id=market.market_id,
            name=market.name,
            description=market.description,
            country=market.country,
            trading_days=market.trading_days,
            trading_hours=market.trading_hours,
            market_class=market.market_class,
            synced_at=synced_at_iso,
        )
        counts["markets"] += 1
        if market.exchange_id not in seen_exchanges:
            seen_exchanges.add(market.exchange_id)
            counts["exchanges"] += 1

    seen_sectors: set[str] = set()
    seen_industries: set[str] = set()
    for row in connector.execute_query(ASSET_INFORMATION_QUERY).rows:
        asset = AssetRecord(
            isin=row["ISIN"],
            asset_name=row["ASSETNAME"],
            asset_short_name=row.get("ASSETSHORTNAME"),
            asset_category=row.get("ASSETCATEGORY"),
            asset_sub_category=row.get("ASSETSUBCATEGORY"),
            market_id=row.get("MARKETID"),
            sector=row.get("SECTOR"),
            industry=row.get("INDUSTRY"),
        )
        neo4j_client.run(
            f"""
            MERGE (a:{NODE_ASSET} {{tenant_id: $tenant_id, isin: $isin}})
            SET a.asset_name = $asset_name,
                a.asset_short_name = $asset_short_name,
                a.asset_category = $asset_category,
                a.asset_sub_category = $asset_sub_category,
                a.active = true,
                a.last_synced_at = $synced_at
            """,
            tenant_id=tenant_id,
            isin=asset.isin,
            asset_name=asset.asset_name,
            asset_short_name=asset.asset_short_name,
            asset_category=asset.asset_category,
            asset_sub_category=asset.asset_sub_category,
            synced_at=synced_at_iso,
        )
        counts["assets"] += 1

        if asset.market_id:
            neo4j_client.run(
                f"""
                MATCH (a:{NODE_ASSET} {{tenant_id: $tenant_id, isin: $isin}})
                MATCH (m:{NODE_MARKET} {{tenant_id: $tenant_id, market_id: $market_id}})
                MERGE (a)-[r:{REL_LISTED_ON}]->(m)
                SET r.active = true, r.last_synced_at = $synced_at
                """,
                tenant_id=tenant_id,
                isin=asset.isin,
                market_id=asset.market_id,
                synced_at=synced_at_iso,
            )

        if asset.sector:
            neo4j_client.run(
                f"""
                MERGE (s:{NODE_SECTOR} {{tenant_id: $tenant_id, name: $sector}})
                SET s.active = true, s.last_synced_at = $synced_at
                WITH s
                MATCH (a:{NODE_ASSET} {{tenant_id: $tenant_id, isin: $isin}})
                MERGE (a)-[r:{REL_IN_SECTOR}]->(s)
                SET r.active = true, r.last_synced_at = $synced_at
                """,
                tenant_id=tenant_id,
                sector=asset.sector,
                isin=asset.isin,
                synced_at=synced_at_iso,
            )
            seen_sectors.add(asset.sector)

        if asset.industry:
            neo4j_client.run(
                f"""
                MERGE (i:{NODE_INDUSTRY} {{tenant_id: $tenant_id, name: $industry}})
                SET i.active = true, i.last_synced_at = $synced_at
                WITH i
                MATCH (a:{NODE_ASSET} {{tenant_id: $tenant_id, isin: $isin}})
                MERGE (a)-[r:{REL_IN_INDUSTRY}]->(i)
                SET r.active = true, r.last_synced_at = $synced_at
                """,
                tenant_id=tenant_id,
                industry=asset.industry,
                isin=asset.isin,
                synced_at=synced_at_iso,
            )
            seen_industries.add(asset.industry)

    counts["sectors"] = len(seen_sectors)
    counts["industries"] = len(seen_industries)

    # Channel / CustomerType / RiskLevel / InvestmentCapacityBand: independent
    # Tier-1 lookup nodes with no edges of their own -- customer-level
    # relationships are excluded from the graph entirely (see this module's
    # docstring), so these exist purely as resolvable reference values.
    # Compiled from `reference_lookups` (Semantic Model config), not four
    # hardcoded query constants -- a label this Semantic Model doesn't
    # declare a lookup for simply gets 0 synced this run.
    for lookup in reference_lookups:
        summary_key = _SIMPLE_LOOKUP_LABELS.get(lookup.node_label)
        if summary_key is None:
            raise ValueError(
                f"reference_lookups declares node_label={lookup.node_label!r}, which is "
                f"not one of the schema-constrained simple-lookup labels: "
                f"{sorted(_SIMPLE_LOOKUP_LABELS)}"
            )
        counts[summary_key] = _sync_simple_lookup(
            neo4j_client,
            connector,
            query=_distinct_values_query(lookup),
            column=lookup.column,
            label=lookup.node_label,
            tenant_id=tenant_id,
            synced_at=synced_at_iso,
        )

    return counts


def _distinct_values_query(lookup: ReferenceLookup) -> str:
    """Build the `SELECT DISTINCT ... WHERE ... IS NOT NULL` query a
    `ReferenceLookup` describes -- replaces one of
    `reference_data_queries.py`'s hardcoded query constants per label.

    `lookup.table` is validated (`navigraph_semantic_model.loader
    .validate_semantic_model_against_catalog`) to be `"SCHEMA.TABLE"`
    before this is ever called against a real connector -- this function
    itself does not re-validate that shape, matching every other stage's
    "validation happens once, at Semantic Model load time" division of
    responsibility.
    """

    return f"SELECT DISTINCT {lookup.column} FROM {lookup.table} WHERE {lookup.column} IS NOT NULL"


def _sync_simple_lookup(
    neo4j_client: Neo4jClient,
    connector: Connector,
    *,
    query: str,
    column: str,
    label: str,
    tenant_id: str,
    synced_at: str,
) -> int:
    """`MERGE` one node per distinct, non-null value of `column` in `query`'s results."""

    synced = 0
    for row in connector.execute_query(query).rows:
        value = row.get(column)
        if not value:
            continue
        neo4j_client.run(
            f"""
            MERGE (n:{label} {{tenant_id: $tenant_id, name: $name}})
            SET n.active = true, n.last_synced_at = $synced_at
            """,
            tenant_id=tenant_id,
            name=value,
            synced_at=synced_at,
        )
        synced += 1
    return synced


def _sync_relationship_concepts(
    neo4j_client: Neo4jClient,
    *,
    relationships: list[Relationship],
    tenant_id: str,
    synced_at: datetime,
) -> int:
    """Stage 4: `RelationshipConcept` nodes compiled from `semantic_model.relationships`.

    Each `Relationship.via.table` is `"SCHEMA.TABLE"` (validated against
    the live catalog at Semantic Model load time) -- only the bare table
    name is used for the `Table` node MATCH/MERGE below, matching stage 1's
    `Table.name` property (itself `CatalogTable.name`, never schema-
    qualified) and the pre-Semantic-Model hardcoded `RELATIONSHIP_CONCEPTS`'
    own `realizing_table` convention exactly.

    TRADEOFF, DELIBERATE AND NOTED: the realizing `Table`/subject-and-object
    `Column` nodes are matched by `name` alone, NOT by `catalog_table_id`/
    `catalog_column_id` (the properties `SCHEMA_CONSTRAINTS` actually
    constrains uniqueness on). In practice this usually still resolves to
    the exact same node stage 1 already created -- stage 1 also sets a
    `name` property on every `Table`/`Column` it upserts, and real table/
    column names in this dataset (e.g. `TRANSACTIONS`, `CUSTOMERID`) are
    unambiguous enough in the common case. But it is NOT guarded by a
    uniqueness constraint on `name` the way `catalog_table_id`/
    `catalog_column_id` are, so: (a) if the referenced table was never
    crawled into the Postgres catalog (e.g. `CUSTOMER_ASSET_AGG`, which is
    plausibly excluded from a crawl since it's customer-cardinality-adjacent
    data this graph otherwise avoids), this creates a new, minimal
    placeholder `Table`/`Column` node instead of failing outright; and
    (b) a column name that happens to repeat across multiple real tables
    (e.g. `CUSTOMERID` appears in several) could, in principle, `MATCH`
    whichever same-named `Column` node Neo4j finds first, rather than the
    one from the specific table this concept intends. Both are accepted at
    this hand-curated, small-cardinality layer: `RelationshipConcept` exists
    to describe *how* to join tables for SQL generation, not to be a
    perfectly-disambiguated foreign-key graph -- a real callback: it means
    when the metadata catalog crawl runs before this stage, the previously
    upserted Table/Column node from Stage 1 is reused; if the ordering
    happens the other way around (which the pipeline never allows in
    practice, since stage 1 always precedes stage 4), the placeholder is
    what's left behind.
    """

    synced = 0
    synced_at_iso = synced_at.isoformat()

    for relationship in relationships:
        realizing_table = relationship.via.table.split(".", 1)[-1]
        neo4j_client.run(
            f"""
            MERGE (rc:{NODE_RELATIONSHIP_CONCEPT} {{tenant_id: $tenant_id, name: $name}})
            SET rc.subject_label = $subject_label,
                rc.predicate = $predicate,
                rc.object_label = $object_label,
                rc.active = true,
                rc.last_synced_at = $synced_at
            MERGE (t:{NODE_TABLE} {{name: $realizing_table}})
            ON CREATE SET t.tenant_id = $tenant_id, t.active = true, t.last_synced_at = $synced_at
            MERGE (t)-[r1:{REL_REALIZES}]->(rc)
            SET r1.active = true, r1.last_synced_at = $synced_at
            MERGE (sc:{NODE_COLUMN} {{name: $subject_key_column}})
            ON CREATE SET sc.tenant_id = $tenant_id, sc.active = true, sc.last_synced_at = $synced_at
            MERGE (rc)-[r2:{REL_SUBJECT_KEY}]->(sc)
            SET r2.active = true, r2.last_synced_at = $synced_at
            MERGE (oc:{NODE_COLUMN} {{name: $object_key_column}})
            ON CREATE SET oc.tenant_id = $tenant_id, oc.active = true, oc.last_synced_at = $synced_at
            MERGE (rc)-[r3:{REL_OBJECT_KEY}]->(oc)
            SET r3.active = true, r3.last_synced_at = $synced_at
            """,
            tenant_id=tenant_id,
            name=relationship.name,
            subject_label=relationship.subject,
            predicate=relationship.predicate,
            object_label=relationship.object,
            realizing_table=realizing_table,
            subject_key_column=relationship.via.subject_key,
            object_key_column=relationship.via.object_key,
            synced_at=synced_at_iso,
        )
        synced += 1

    return synced
