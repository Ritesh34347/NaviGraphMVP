# Data Flow: One Request, End to End

This document walks a single request through the full NaviGraph pipeline in
narrative form, naming the specific real agent responsible for each stage.
See `docs/architecture/overview.md` for the full agent map and current build
status, `docs/architecture/single-stage-mvp.md` for the exhaustive, ordered
19-agent call sequence and outcome model, and
`docs/architecture/agent-contract.md` for the formal shape of
`lineage_events`.

**Reconciled 2026-08-09**: every agent named below is real and built (see
`LIMITATIONS.md` items 7, 32, 35 — all RESOLVED). This pass also corrected
how lineage events are described: there is no generic per-phase event name
(`intent_extracted`, `query_generated`, etc.) anywhere in the real code.
Every agent's `LineageEvent.agent_name` is that agent's own registry key
(e.g. `understanding.intent_understanding`), and the Request Orchestrator
forwards each stage's real `lineage_events` to the Lineage Recorder
immediately after that stage runs, then emits one final event of its own
under `orchestrator.request_orchestrator` recording the overall outcome.
There is also no gateway-level `request_received` event — the gateway
forwards the request to the agent-runtime and the first real lineage event
is Conversation's.

Example question used throughout: *"What was our churn rate by region last
quarter, and why did it spike in the Southwest?"*

## 1. Gateway receives the request

The `gateway` service (`packages/gateway`) receives `POST /ask` with the
question text and the caller's `tenant_id`/`user_id`/`roles`/`claims`
(caller-supplied today — no real Azure AD JWT verification exists yet, see
`LIMITATIONS.md`'s Azure AD token-verification item). It mints a `trace_id`,
builds a `RequestContext`, and forwards the request over HTTP to the
agent-runtime's real Request Orchestrator (`POST
/agents/orchestrator/request_orchestrator/invoke`) — a real network hop
between two separate containers, not an in-process call.

## 2. Understanding domain: conversation, intent, and schema mapping

Six real agents run in sequence. **Conversation**
(`understanding.conversation`) resolves the raw question against any prior
turns in this session (read from Redis via Session/Context Manager) into a
self-contained question. **Intent Understanding**
(`understanding.intent_understanding`) classifies the intent (e.g. "trend +
causal explanation request") and extracts entities ("churn rate", "region",
"last quarter", "Southwest"). **Metadata Discovery**
(`understanding.metadata_discovery`) lists this tenant's real catalog
columns for the resolved data source. **Ontology**
(`understanding.ontology`) resolves the extracted entities/relationships
against the knowledge graph (Neo4j) — e.g. which upstream attributes are
known to relate to churn in the Southwest region. **Semantic Retrieval**
(`understanding.semantic_retrieval`) ranks candidate catalog columns for any
terms Ontology left unresolved. **Schema Mapping**
(`understanding.schema_mapping`) is the single assembly point that merges
all of the above into the final tables/columns/joins the rest of the
pipeline uses.

If Schema Mapping resolves zero tables, the Orchestrator domain's
**Multi-turn Clarification Coordinator** (`orchestrator.clarification_coordinator`)
runs instead of the rest of the pipeline, and the request ends with
`outcome="needs_clarification"` and a real clarifying question.

## 3. Query domain: query generation

**Data Source Discovery** (`query.data_source_discovery`) confirms each
resolved table maps to a real, reachable data source. **SQL Generation**
(`query.sql_generation`) then produces schema-grounded SQL against the
resolved tables/columns/joins. There is no separate Cypher-generation step —
graph-native reasoning already happened inside Ontology in step 2, not as a
second, independent query language generated here.

## 4. Guardrail domain: validation gate

Before anything executes, four real agents check the generated SQL in
sequence: **Schema Constraint Validator** (`guardrail.schema_constraint_validator`),
**PII Exposure Checker** (`guardrail.pii_exposure_checker`), **Policy
Authorization** (`guardrail.policy_authorization`, backed by OPA), and —
after SQL Optimization rewrites the statement — **Query Cost/Row-Limit
Estimator** (`guardrail.query_cost_estimator`). Any rejection at any of
these four stops the pipeline with `outcome="failed"` and a `failure_stage`
naming exactly which check failed. OPA enforces a real, deny-by-default
RBAC + tenant-ABAC policy (`infra/opa/policies/authz.rego`, hardened via
`tests/security/`'s adversarial suite — `LIMITATIONS.md` item 4, RESOLVED),
not a placeholder. What it doesn't do: see row-/column-level detail (PII
specifically is the separate PII Exposure Checker agent's job), or verify
that the caller's claims are cryptographically genuine — no real Azure AD
JWT verification exists yet (item 23), so `claims.tenant_id` is trusted as
given.

## 5. Query domain: optimization, planning, and real execution

**SQL Optimization** (`query.sql_optimization`) rewrites the guardrail-
approved statement. **Execution Planning** (`query.execution_planning`)
builds the final `ExecutionPlan` — the hard safety gate that only accepts a
single, bind-parameterized, read-only `SELECT`/`WITH` statement, with a row
cap and timeout. **Data Federation** (`query.data_federation`) then executes
it for real, against live Snowflake via the direct connector (the default
route today; `route="trino"` is built and unit-tested but not yet the
default — see `LIMITATIONS.md` item 3). There is no separate caching step in
the live pipeline today: `query.caching` is a real, built, Redis-backed
agent, but it is not currently called by the Request Orchestrator (see
`LIMITATIONS.md` item 59).

## 6. Insight domain: chart selection, anomalies, narrative, follow-ups

**Chart Selection** (`insight.chart_selection`) picks an appropriate
visualization for the result shape (e.g. a time series by region).
**Anomaly/Outlier Highlighter** (`insight.anomaly_outlier_highlighter`)
deterministically flags the Southwest spike. **Grounded Narrative
Generation** (`insight.grounded_narrative_generation`) writes a
natural-language explanation, with a citation-validation layer that drops
any claim not backed by an actual returned value rather than shipping it.
**Follow-up Suggestion** (`insight.follow_up_suggestion`) proposes related
questions ("Did any single account drive the Southwest spike?").

## 7. Response returned, full lineage recorded

Every one of the agents above emits its own `lineage_events` (keyed by its
own registry name); the Request Orchestrator forwards each stage's events to
**Lineage Recorder** (`ops.lineage_recorder`) immediately after that stage
runs — one incremental Postgres append per real upstream output, not one
bulk write at the end — so the entire reasoning chain from question to final
chart and narrative can be retrieved later via `GET /lineage/{trace_id}` and
audited. A lineage-recording failure is logged and swallowed; it never
aborts the request. The gateway returns the chart, narrative, and follow-up
suggestions to the caller.
