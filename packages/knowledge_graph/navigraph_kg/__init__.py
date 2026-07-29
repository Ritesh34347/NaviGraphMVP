"""NaviGraph knowledge graph package.

A Neo4j-backed, two-tier ontology grounded in the real `FIDELITY_POC`
Snowflake dataset:

- Tier 1 (bounded-cardinality reference/dimension nodes, crawled from real
  Snowflake reference data): `Asset`, `Market`, `Exchange`, `Sector`,
  `Industry`, `Channel`, `CustomerType`, `RiskLevel`,
  `InvestmentCapacityBand`.
- Tier 2 (business-concept/schema-mapping layer, thin proxies over
  `navigraph-metadata-catalog`'s Postgres rows plus hand-curated
  relationship concepts): `BusinessConcept`, `Table`, `Column`,
  `RelationshipConcept`.

Customer- and transaction-cardinality data is deliberately EXCLUDED from
this graph -- see `navigraph_kg.ingestion.pipeline`'s module docstring.
Those questions are answered by generated SQL against Snowflake directly;
this graph exists purely for reference-data validation and business-term
resolution.
"""

__version__ = "0.1.0"
