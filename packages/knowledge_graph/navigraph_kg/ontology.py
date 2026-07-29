"""The knowledge graph's ontology: node labels, relationship types, schema
constraints, and hand-curated relationship-concept seed data.

Two tiers (see `navigraph_kg`'s package docstring for the full rationale):

- Tier 1 -- bounded-cardinality reference/dimension nodes, grounded in real
  `FIDELITY_POC` Snowflake data: `Asset`, `Market`, `Exchange`, `Sector`,
  `Industry`, `Channel`, `CustomerType`, `RiskLevel`,
  `InvestmentCapacityBand`.
- Tier 2 -- business-concept/schema-mapping layer: `BusinessConcept`,
  `Table`, `Column` (thin proxies referencing the Postgres catalog by UUID),
  `RelationshipConcept` (hand-curated, not crawled).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from navigraph_kg.client import Neo4jClient

# --- Node labels: Tier 1 (bounded-cardinality reference/dimension nodes) ---
NODE_ASSET = "Asset"
NODE_MARKET = "Market"
NODE_EXCHANGE = "Exchange"
NODE_SECTOR = "Sector"
NODE_INDUSTRY = "Industry"
NODE_CHANNEL = "Channel"
NODE_CUSTOMER_TYPE = "CustomerType"
NODE_RISK_LEVEL = "RiskLevel"
NODE_INVESTMENT_CAPACITY_BAND = "InvestmentCapacityBand"

# --- Node labels: Tier 2 (business-concept/schema-mapping layer) ---
NODE_BUSINESS_CONCEPT = "BusinessConcept"
NODE_TABLE = "Table"
NODE_COLUMN = "Column"
NODE_RELATIONSHIP_CONCEPT = "RelationshipConcept"

# --- Relationship types ---
REL_LISTED_ON = "LISTED_ON"
REL_PART_OF_EXCHANGE = "PART_OF_EXCHANGE"
REL_IN_SECTOR = "IN_SECTOR"
REL_IN_INDUSTRY = "IN_INDUSTRY"
REL_MAPS_TO = "MAPS_TO"
REL_COLUMN_OF = "COLUMN_OF"
REL_REALIZES = "REALIZES"
REL_SUBJECT_KEY = "SUBJECT_KEY"
REL_OBJECT_KEY = "OBJECT_KEY"


# Composite (tenant_id + natural key) constraints use `IS UNIQUE`, not
# `IS NODE KEY` -- `IS NODE KEY` also enforces property EXISTENCE and
# requires Neo4j Enterprise, whereas `infra/docker-compose.yml` runs the
# `neo4j:5-community` image. Multi-property `IS UNIQUE` uniqueness
# constraints (without the existence guarantee) ARE supported in Community
# edition since Neo4j 4.4+/5, which is what every composite constraint below
# relies on. `Table`/`Column` are the two exceptions: their natural key is
# just the Postgres-catalog UUID (`catalog_table_id`/`catalog_column_id`),
# already globally unique on its own with no need for a `tenant_id`
# component -- these two nodes are thin cross-database proxies, not
# independently-keyed domain entities.
def _tenant_scoped_constraint(label: str, key_property: str) -> str:
    """Build a `(tenant_id, key_property)` composite `IS UNIQUE` constraint statement."""

    return (
        f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) "
        f"REQUIRE (n.tenant_id, n.{key_property}) IS UNIQUE"
    )


def _global_constraint(label: str, key_property: str) -> str:
    """Build a single-property `IS UNIQUE` constraint statement, with no `tenant_id`."""

    return f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.{key_property} IS UNIQUE"


SCHEMA_CONSTRAINTS: list[str] = [
    _tenant_scoped_constraint(NODE_ASSET, "isin"),
    _tenant_scoped_constraint(NODE_MARKET, "market_id"),
    _tenant_scoped_constraint(NODE_EXCHANGE, "exchange_id"),
    _tenant_scoped_constraint(NODE_SECTOR, "name"),
    _tenant_scoped_constraint(NODE_INDUSTRY, "name"),
    _tenant_scoped_constraint(NODE_CHANNEL, "name"),
    _tenant_scoped_constraint(NODE_CUSTOMER_TYPE, "name"),
    _tenant_scoped_constraint(NODE_RISK_LEVEL, "name"),
    _tenant_scoped_constraint(NODE_INVESTMENT_CAPACITY_BAND, "name"),
    _tenant_scoped_constraint(NODE_BUSINESS_CONCEPT, "name"),
    _global_constraint(NODE_TABLE, "catalog_table_id"),
    _global_constraint(NODE_COLUMN, "catalog_column_id"),
    _tenant_scoped_constraint(NODE_RELATIONSHIP_CONCEPT, "name"),
]


def apply_constraints(client: Neo4jClient) -> None:
    """Apply every constraint in `SCHEMA_CONSTRAINTS`.

    Idempotent -- `CREATE CONSTRAINT IF NOT EXISTS` makes re-running this
    against an already-constrained database a no-op, so it is safe to call
    on every deploy/startup, not just once.
    """

    for statement in SCHEMA_CONSTRAINTS:
        client.run(statement)


# Hand-curated seed data for `RelationshipConcept` nodes (see
# `navigraph_kg.ingestion.pipeline._sync_relationship_concepts`) -- these are
# NEVER crawled or auto-derived; customer- and transaction-cardinality data
# is explicitly excluded from the graph itself (see the module docstring of
# `navigraph_kg.ingestion.pipeline`), so a `Customer` node never actually
# exists in Neo4j. What these seed entries capture instead is *how* a
# `Customer`-to-X relationship would be realized in Snowflake if a caller
# needed to generate SQL for it: which real table/columns to join.
RELATIONSHIP_CONCEPTS: list[dict[str, str]] = [
    {
        "name": "Customer holds Asset",
        "subject_label": "Customer",
        "predicate": "HOLDS",
        "object_label": "Asset",
        "realizing_table": "CUSTOMER_ASSET_AGG",
        "subject_key_column": "CUSTOMERID",
        "object_key_column": "ISIN",
    },
    {
        "name": "Customer uses Channel",
        "subject_label": "Customer",
        "predicate": "USES",
        "object_label": "Channel",
        "realizing_table": "TRANSACTIONS",
        "subject_key_column": "CUSTOMERID",
        "object_key_column": "CHANNEL",
    },
    {
        "name": "Customer has RiskLevel",
        "subject_label": "Customer",
        "predicate": "HAS",
        "object_label": "RiskLevel",
        "realizing_table": "CUSTOMER_INFORMATION",
        "subject_key_column": "CUSTOMERID",
        "object_key_column": "RISKLEVEL",
    },
    {
        # Real bug found live in Phase 9's real HTTP smoke test of the
        # Request Orchestrator: "What is the total transaction volume by
        # market?" resolved TRANSACTIONS.TOTALVALUE and MARKETS.NAME with
        # zero relationship concepts (Ontology's curated set had no entry
        # linking them), so Schema Mapping's `_build_joins` -- which ONLY
        # derives joins from `relationship_resolutions` -- emitted no join
        # at all. SQL Generation then had no way to connect the two tables,
        # producing a single ungrouped grand total cross-joined against
        # every distinct market name (same wrong value on all 38 rows).
        # Unlike the other three entries, MARKETID is the literal SAME
        # column name on both sides (TRANSACTIONS.MARKETID is a real
        # foreign key to MARKETS.MARKETID) rather than a Customer-style
        # subject/object key split, so `subject_key_column` and
        # `object_key_column` are identical here.
        "name": "Transaction happens in Market",
        "subject_label": "Transaction",
        "predicate": "HAPPENS_IN",
        "object_label": "Market",
        "realizing_table": "TRANSACTIONS",
        "subject_key_column": "MARKETID",
        "object_key_column": "MARKETID",
    },
]
