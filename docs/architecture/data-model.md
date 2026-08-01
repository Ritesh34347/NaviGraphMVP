# Data Model

The real schemas behind the three storage layers: the Postgres metadata
catalog, the Neo4j knowledge graph, and the Postgres lineage store.
Pulled directly from the real SQLAlchemy models/migrations and Cypher
ontology constants — not re-derived or approximated.

## Metadata catalog (Postgres, `packages/metadata_catalog`)

Structural schema/glossary/PII-tag storage only — no business ontology,
no raw row data. `tenant_id` lives on `DataSource` so tenant scoping is
structural from the root of the tree.

```mermaid
erDiagram
    DataSource ||--o{ CatalogSchema : has
    CatalogSchema ||--o{ CatalogTable : has
    CatalogTable ||--o{ CatalogColumn : has
    CatalogColumn ||--o| ColumnGlossary : "may have"

    DataSource {
        uuid id PK
        string tenant_id "indexed, structural tenant scoping"
        string name
        string source_type "validated against connector registry"
        jsonb connection_ref "opaque pointer, e.g. env_prefix -- never raw credentials"
        datetime created_at
    }
    CatalogSchema {
        uuid id PK
        uuid data_source_id FK
        string name
    }
    CatalogTable {
        uuid id PK
        uuid schema_id FK
        string name
        string description
        int row_count_estimate
    }
    CatalogColumn {
        uuid id PK
        uuid table_id FK
        string name
        string data_type
        bool nullable
        int ordinal_position
        string description
        bool is_pii "server_default false; enforced by guardrail.pii_exposure_checker"
    }
    ColumnGlossary {
        uuid id PK
        uuid column_id FK "unique"
        string business_name
        jsonb synonyms "list[str], split from real SCHEMA_ENRICHMENT source"
        string description
        string source "e.g. schema_enrichment"
        datetime created_at
    }
```

Real, honest gaps: `connection_ref` is deliberately opaque (real secrets
come from Key Vault, never stored here); only ~30 of the real columns
have a `ColumnGlossary` entry — a column without one legitimately has "no
business concept exists yet," never a synthesized fallback.

## Knowledge graph (Neo4j, `packages/knowledge_graph`)

Two-tier by design (`DECISIONS.md`'s Phase 3 entry): bounded-cardinality
reference/dimension nodes, plus a thin business-concept mapping layer.
Customers and transactions are deliberately **excluded** — high-
cardinality, high-write facts stay in Snowflake/SQL; the graph only
supplies reference-data validation and business-term resolution.

```mermaid
erDiagram
    Asset }o--|| Market : "LISTED_ON"
    Market }o--|| Exchange : "PART_OF_EXCHANGE"
    Asset }o--o| Sector : "IN_SECTOR (only if non-null in source)"
    Asset }o--o| Industry : "IN_INDUSTRY (only if non-null in source)"
    Column ||--o| BusinessConcept : "MAPS_TO"
    Column }o--|| Table : "COLUMN_OF"
    RelationshipConcept }o--|| BusinessConcept : "SUBJECT_KEY / OBJECT_KEY"
    RelationshipConcept ||--|| BusinessConcept : "REALIZES"

    Asset {
        string isin PK
        string asset_name
        string asset_category
    }
    Market {
        string market_id PK
        string name
    }
    Exchange {
        string exchange_id PK
    }
    Sector { string name PK }
    Industry { string name PK }
    Channel { string name PK }
    CustomerType { string name PK }
    RiskLevel { string name PK }
    InvestmentCapacityBand { string name PK }

    BusinessConcept {
        string concept_id PK
        string name "from real SCHEMA_ENRICHMENT glossary"
    }
    Table {
        string table_id PK "proxy into Postgres catalog by UUID"
    }
    Column {
        string column_id PK "proxy into Postgres catalog by UUID"
    }
    RelationshipConcept {
        string relationship_id PK
        string predicate "e.g. Customer holds Asset"
    }
```

Real node/relationship label constants (`ontology.py`): 9 reference-data
node labels (`Asset`, `Market`, `Exchange`, `Sector`, `Industry`,
`Channel`, `CustomerType`, `RiskLevel`, `InvestmentCapacityBand`) + 4
business-concept-layer labels (`BusinessConcept`, `Table`, `Column`,
`RelationshipConcept`), and 9 relationship types (`LISTED_ON`,
`PART_OF_EXCHANGE`, `IN_SECTOR`, `IN_INDUSTRY`, `MAPS_TO`, `COLUMN_OF`,
`REALIZES`, `SUBJECT_KEY`, `OBJECT_KEY`).

Real, confirmed-live data-shape findings that drove this design
(`DECISIONS.md`'s Phase 3 entry): exchanges and markets are genuinely
1-to-many (e.g. `ATHEX` groups 3 real markets); Sector/Industry are
independent siblings, not a strict hierarchy (one real violation found —
`"Building Materials"` under two different parents); only ~50% of assets
have any sector/industry at all (bonds/funds legitimately have none).
Soft staleness (`active`/`last_synced_at` flags), not hard deletes, on
re-ingestion.

## Lineage store (Postgres, `packages/lineage`, own Alembic chain)

One table, storing exactly the fields the shared `LineageEvent` contract
already defines — no separate "event type" field; `agent_name` (e.g.
`understanding.intent_understanding`) is the real, stable identity of
each recorded step.

```mermaid
erDiagram
    LineageEventRecord {
        string event_id PK "agent's own lineage_{uuid4().hex}, not a surrogate"
        string agent_name "e.g. understanding.intent_understanding"
        datetime timestamp
        string input_summary
        string output_summary
        string tenant_id "part of the composite index, not a separate one"
        string trace_id
    }
```

`event_id` as the real primary key makes idempotent re-insertion a real,
DB-enforced property (`INSERT ... ON CONFLICT (event_id) DO NOTHING`),
not just an application convention — proven by a real re-recording test
in `tests/integration/lineage_pipeline/`. The composite index
`(tenant_id, trace_id, timestamp)` matches the one real read pattern:
"give me the whole ordered chain for this tenant+trace" (`GET
/lineage/{trace_id}?tenant_id=...`).

Reused the same physical Postgres instance as `metadata_catalog`, with
its own `alembic_version_lineage` tracking table to avoid colliding with
the catalog's own `alembic_version` — no new database service, since
tenant isolation in this codebase is already row-level everywhere, never
database-level.
