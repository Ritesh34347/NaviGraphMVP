# Glossary

Business and platform terms, for non-technical and technical reference
alike. Business terms below are pulled directly from the real
`SCHEMA_ENRICHMENT` column glossary (crawled into Postgres via
`schema_enrichment_crawler.py`), not invented.

## Business terms (real, from the live catalog)

| Business name | Real column | Synonyms |
|---|---|---|
| Customer ID | `CUSTOMERID` | client id, customer number, client number, customer key |
| Customer Type | `CUSTOMERTYPE` | client type, customer segment, account type |
| Risk Level | `RISKLEVEL` | risk profile, investor risk profile, risk category *(PII-tagged — see `security-compliance.md`)* |
| Investment Capacity | `INVESTMENTCAPACITY` | investable assets, investment potential, wealth capacity |
| Transaction ID | `TRANSACTIONID` | trade id, order id, transaction reference |
| Transaction Type | `TRANSACTIONTYPE` | trade type, order type, buy/sell indicator |
| Total Transaction Value | `TOTALVALUE` | trade value, order value, transaction amount, gross value |
| Units Traded | `UNITS` | quantity, shares traded, volume |
| Transaction Channel | `CHANNEL` | order channel, trading channel, execution channel |
| ISIN | `ISIN` | International Securities Identification Number, security id, asset id |
| Asset Name | `ASSETNAME` | security name, instrument name |
| Asset Short Name | `ASSETSHORTNAME` | ticker, short name, symbol |
| Asset Category | `ASSETCATEGORY` | asset class, instrument type, security type |
| Sector / Industry | `SECTOR` / `INDUSTRY` | industry sector, business sector / sub-sector, business line |
| Market ID | `MARKETID` | exchange id, market code, trading venue |
| Market Name | `NAME` (on `STAGING_MARKETS`) | market name, market title |
| Closing Price | `CLOSEPRICE` | close price, end-of-day price, settlement price |

Not every real column has a glossary entry — only ~30 of them do. A
column with none (e.g. a bare `TIMESTAMP`) legitimately has "no business
concept mapped yet"; this is a real, honest gap, not a display bug.

## The 4 real question types (`IntentLabel`)

| Term | Meaning | Example |
|---|---|---|
| **Metric lookup** | A single aggregate or breakdown | "How many transactions has each customer made?" |
| **Comparison** | A metric compared across a dimension | "Which markets have the highest transaction volume?" |
| **Trend analysis** | A metric's change over time | "Show me the trend of transaction volume over time." |
| **Anomaly investigation** | Detecting/explaining unusual values | "Are there any unusual spikes in units traded by market?" |

## Platform concepts

| Term | Meaning in this codebase |
|---|---|
| **Tenant** | The real unit of isolation — every request, cache key, catalog row, and lineage record carries a `tenant_id` |
| **RBAC / ABAC** | Role-based ("is this role allowed?") vs. attribute-based ("does the claimed tenant match?") access control — both real, enforced by `authz.rego` |
| **Guardrail** | The domain of 4 agents (Schema Constraint Validator, Policy Authorization, Query Cost Estimator, PII Exposure Checker) that sit between SQL generation and execution |
| **Lineage** | The real, queryable audit trail of every agent's input/output for a given request (`trace_id`) |
| **Canary** | A weighted-traffic-split deployment strategy — a new version serves a small % of real traffic before full promotion, automatically gated on real error-rate/latency metrics |
| **Golden set** | The 10 real, schema-grounded questions used to evaluate whether the whole pipeline actually works (`eval/golden_set/`) |
| **Grounded narrative** | A generated explanation whose factual claims are checked against the real result set — a fabricated number is dropped, not silently trusted |
| **Semantic retrieval** | The LLM-based step that matches a business term to a real catalog column, constrained to a closed candidate list (never free-form) |
| **Data source** | A registered, catalog-crawled connection to a real backing store (today: one real Snowflake account, `FIDELITY_POC`) |
