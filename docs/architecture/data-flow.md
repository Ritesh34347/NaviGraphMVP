# Data Flow: One Request, End to End

This document walks a single request through the full NaviGraph pipeline,
naming the specific agent responsible for each stage and the lineage event it
emits. See `docs/architecture/overview.md` for the full agent map and current
build status, and `docs/architecture/agent-contract.md` for the formal shape of
`lineage_events`.

Example question used throughout: *"What was our churn rate by region last
quarter, and why did it spike in the Southwest?"*

## 1. Gateway receives the request

The `gateway` service (`packages/gateway`) receives `POST /ask` with the
question text and the caller's authenticated session (tenant, user, roles from
Azure AD/Entra ID). It attaches a `RequestContext` (`tenant_id`, `user_id`,
`trace_id`, `roles`/`claims`) and hands off to the agent runtime's
Orchestrator.

**Lineage event**: `request_received` — records the raw question, tenant, user,
and trace_id.

## 2. Understanding domain: intent + entity extraction

The **Intent Understanding** agent (the one agent that is real today) parses
the question into a structured intent (e.g. "trend + causal explanation
request") and extracts entities ("churn rate", "region", "last quarter",
"Southwest"). Downstream **Entity Resolution** and **Ambiguity Detection**
agents (designed, not yet built) would resolve "Southwest" against the tenant's
actual region dimension and flag any ambiguous references back to the user.

**Lineage event**: `intent_extracted` — records the structured intent payload,
extracted entities, and the agent's confidence score.

## 3. Query domain: semantic retrieval

The **Semantic Catalog Retrieval** and **Knowledge Graph Retrieval** agents
(designed) look up which tables/columns define "churn rate" for this tenant
and traverse the knowledge graph (Neo4j) for relevant relationships — e.g.
which upstream events or attributes are known to correlate with churn in the
"Southwest" region.

**Lineage event**: `context_retrieved` — records which catalog entries and
graph nodes/edges were retrieved and used.

## 4. Query domain: query generation

The **SQL Generation** agent (designed) produces schema-grounded SQL against
the resolved tables/columns; if the question requires graph-native reasoning
(e.g. "why," which may involve relationship traversal beyond a single fact
table), the **Cypher Generation** agent (designed) produces a complementary
graph query. The **Query Plan Composer** agent (designed) merges these into a
single execution plan.

**Lineage event**: `query_generated` — records the generated SQL/Cypher text,
the schema elements it references, and the metric definitions used (via the
**Metric Definition Resolver**, designed).

## 5. Guardrail domain: validation gate

Before anything executes, the **Schema Constraint Validator**, **Policy
Authorization** (backed by OPA), **Query Cost/Row-Limit Estimator**, and **PII
Exposure Checker** agents (all designed) check the generated query against
schema constraints, the tenant's authorization policy, expected cost/row
volume, and PII exposure rules. Today, OPA enforces a placeholder allow-all
policy (see `LIMITATIONS.md` item 4) — the gate exists structurally, but its
real policy logic is not yet written.

**Lineage event**: `query_validated` — records the validation outcome (pass/
fail per check) and the specific policy decision returned by OPA.

## 6. Ops domain: federated execution

The **Federated Query Executor** agent (designed) submits the validated query
to Trino, which federates execution across registered catalogs. Today, zero
real catalogs are registered (see `LIMITATIONS.md` item 3), so this stage is
architecturally proven but not yet connected to real data. The **Result
Caching** agent (designed) would cache results in Redis keyed by
tenant+query-hash.

**Lineage event**: `query_executed` — records execution duration, row count
returned, and whether the result was served from cache.

## 7. Insight domain: chart selection, narrative, follow-ups

The **Chart Selection** agent (designed) picks an appropriate visualization for
the result shape (e.g. a time series by region). The **Grounded Narrative
Generation** agent (designed) writes a natural-language explanation that cites
the actual returned numbers (never inventing figures not present in the
result set). The **Anomaly/Outlier Highlighter** agent (designed) flags the
Southwest spike specifically. The **Follow-up Suggestion** agent (designed)
proposes related questions ("Did any single account drive the Southwest
spike?").

**Lineage event**: `insight_generated` — records the chart type chosen, the
narrative text, and which result values it grounded each claim in.

## 8. Response returned, full lineage recorded

The **Lineage Recorder** agent (designed) persists the full chain of lineage
events from `request_received` through `insight_generated` against the
request's `trace_id`, so the entire reasoning chain — from raw question to
final chart and narrative — can be audited after the fact. The gateway returns
the chart, narrative, and follow-up suggestions to the caller.
