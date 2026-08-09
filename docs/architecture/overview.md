# Architecture Overview

This document is the canonical map of the NaviGraph agent architecture: the
request lifecycle, the six agent domains, and the 25 real agents within them.

**Reconciled 2026-08-09** against the actual registry
(`packages/agent_runtime/navigraph_agents/registry.py`), the real startup
wiring (`packages/agent_runtime/navigraph_agents/main.py`), and the real
Request Orchestrator (`orchestrator/request_orchestrator/agent.py`) — the
domain tables below previously marked every agent `DESIGNED` long after
Phases 4-9 had actually shipped all 25 of them (see `LIMITATIONS.md` items
7, 32, and 35, all now marked RESOLVED). For the exact call order the live
Request Orchestrator uses today, see
[`single-stage-mvp.md`](./single-stage-mvp.md).

## What NaviGraph does

NaviGraph answers natural-language business questions by combining
schema-grounded SQL generation with knowledge-graph semantic reasoning, across
multiple tenants and (eventually) multiple underlying data sources.

## Request lifecycle

```
 NL question
     |
     v
 [Understanding domain]  conversation resolution, intent + entity extraction
     |
     v
 [Understanding domain]  catalog + knowledge-graph retrieval, schema mapping
     |
     v
 [Query domain]          schema-grounded SQL generation
     |
     v
 [Guardrail domain]      validation gate (schema constraints, PII, OPA policy, cost)
     |
     v
 [Query domain]          optimization, execution planning, and real execution
                          (Snowflake direct connector by default; Trino built,
                          not yet the default route)
     |
     v
 [Insight domain]        chart selection, anomaly highlighting
     |
     v
 [Insight domain]        grounded narrative generation, follow-up suggestions
     |
     v
 lineage recorded at every stage above
```

Every stage above emits a lineage event (see
[`data-flow.md`](./data-flow.md) for the stage-by-stage walkthrough with the
specific event emitted at each step). The **Orchestrator** domain's Request
Orchestrator agent directly calls each of the above in sequence (a plain
Python async function, not a LangGraph graph — see `DECISIONS.md`'s Phase 9
entry reversing Phase 1's original LangGraph decision) and is responsible for
tenant/session context propagation, retries, and error handling across stages.
See `single-stage-mvp.md` for the full 19-agent call order and outcome model.

## Agent domains and agents

Status legend: **BUILT** (real implementation, unit-tested, registered in
`main.py`) · **NOT BUILT** (no agent exists for this responsibility today).

### Understanding domain

Turns a raw natural-language question into structured intent, entities, and
a concrete table/column/join mapping.

| Agent | Status | Notes |
|---|---|---|
| Conversation (`understanding.conversation`) | **BUILT** | Resolves the raw question against prior turns (e.g. "what about last month?") into a self-contained question |
| Intent Understanding (`understanding.intent_understanding`) | **BUILT** | Classifies intent, extracts entities |
| Metadata Discovery (`understanding.metadata_discovery`) | **BUILT** | Lists the real catalog columns registered for a data source (Postgres) |
| Ontology (`understanding.ontology`) | **BUILT** | Resolves entities/relationships against the knowledge graph (Neo4j) |
| Semantic Retrieval (`understanding.semantic_retrieval`) | **BUILT** | Ranks candidate catalog columns for any terms Ontology left unresolved |
| Schema Mapping (`understanding.schema_mapping`) | **BUILT** | The single assembly point: merges the above into final tables/columns/joins |

Two originally-planned agents in this domain were never built as standalone
agents; their responsibilities were absorbed elsewhere:

- **Entity Resolution** and **Ambiguity Detection** (both originally
  `DESIGNED`) — no separate agent disambiguates entities against real
  dimension values or flags general ambiguity. Instead, when Schema Mapping
  resolves zero tables, the Orchestrator domain's Multi-turn Clarification
  Coordinator is invoked as the real (narrower) mechanism that plays this
  role today — see `single-stage-mvp.md`'s outcome model.
- **Conversation Context Tracker** shipped as two real agents together,
  not one: `understanding.conversation` (per-question resolution) plus
  `orchestrator.session_context_manager` (Redis-backed history persistence
  across turns).

### Query domain

Generates and executes the queries that answer the question.

| Agent | Status | Notes |
|---|---|---|
| SQL Generation (`query.sql_generation`) | **BUILT** | Schema-grounded SQL from Schema Mapping's output |
| SQL Optimization (`query.sql_optimization`) | **BUILT** | Rewrites/optimizes the Guardrail-approved statement |
| Execution Planning (`query.execution_planning`) | **BUILT** | Builds the final `ExecutionPlan`; the hard SELECT-only, bind-parameterized safety gate |
| Data Source Discovery (`query.data_source_discovery`) | **BUILT** | Confirms each resolved table maps to a real, reachable data source |
| Data Federation (`query.data_federation`) | **BUILT** | Executes for real (Snowflake direct connector today; `route="trino"` built and unit-tested but not the default) |
| Caching (`query.caching`) | **BUILT, not yet wired into the live pipeline** | A real Redis-backed query-result cache, reachable via `POST /agents/query/caching/invoke`, but not called by the Request Orchestrator's live 19-agent sequence today — see `LIMITATIONS.md` item 59 |

Three originally-planned agents in this domain were never built as
standalone agents:

- **Cypher Generation** — graph-side resolution is Understanding's Ontology
  agent's job; no separate agent independently generates and executes
  standalone Cypher.
- **Metric Definition Resolver** — metric definitions are resolved inline
  by Schema Mapping, not a separate agent.
- **Query Plan Composer** — its role is Execution Planning's job.

### Guardrail domain

Validates generated queries and enforces authorization before anything
executes against real data.

| Agent | Status | Notes |
|---|---|---|
| Schema Constraint Validator (`guardrail.schema_constraint_validator`) | **BUILT** | Deterministic reject on schema-constraint violations |
| PII Exposure Checker (`guardrail.pii_exposure_checker`) | **BUILT** | Rejects if the caller's role can't see a column flagged `is_pii` |
| Policy Authorization (`guardrail.policy_authorization`) | **BUILT** | Real OPA policy decision — OPA itself still runs an allow-all placeholder policy (`LIMITATIONS.md` item 4) |
| Query Cost/Row-Limit Estimator (`guardrail.query_cost_estimator`) | **BUILT** | Rejects if estimated cost/row volume exceeds configured limits |

All four originally-planned Guardrail agents are built. The gap is not the
agents — it's that Policy Authorization currently enforces a placeholder
allow-all Rego policy rather than real RBAC/ABAC rules.

### Query execution and Ops domains

The original design's Ops-domain table listed "Federated Query Executor
(Trino)" and "Result Caching" — both shipped for real, but as Query-domain
agents (`query.data_federation`, `query.caching`), not Ops. Ops itself
turned out to own two different agents than originally planned:

| Agent | Status | Notes |
|---|---|---|
| Lineage Recorder (`ops.lineage_recorder`) | **BUILT** | Persists each stage's lineage events incrementally, keyed by `trace_id` |
| Evaluation Judge (`ops.evaluation_judge`) | **BUILT** | Not in the original design at all — scores golden-set answers (correctness/groundedness/narrative quality) for the eval harness; not part of the live request path |

**Error/Retry Handler** was never built as a standalone agent — retry and
error-handling logic across stages is explicitly the Request Orchestrator's
own job (see the Orchestrator domain below and `single-stage-mvp.md`'s
outcome/failure-stage model), not a separate agent.

### Insight domain

Turns raw query results into a chart, a grounded narrative, and next-step
suggestions.

| Agent | Status | Notes |
|---|---|---|
| Chart Selection (`insight.chart_selection`) | **BUILT** | Picks a chart type/axes for the result shape |
| Anomaly/Outlier Highlighter (`insight.anomaly_outlier_highlighter`) | **BUILT** | Deterministically flags outliers in the result set |
| Grounded Narrative Generation (`insight.grounded_narrative_generation`) | **BUILT** | Citation-validated narrative — drops any claim not backed by a returned value |
| Follow-up Suggestion (`insight.follow_up_suggestion`) | **BUILT** | Proposes related next questions |

All four originally-planned Insight agents are built.

### Orchestrator domain

Coordinates the domains above into a single request lifecycle.

| Agent | Status | Notes |
|---|---|---|
| Request Orchestrator (`orchestrator.request_orchestrator`) | **BUILT** | Calls the other 19 real agents directly, in sequence — see `single-stage-mvp.md` |
| Session/Context Manager (`orchestrator.session_context_manager`) | **BUILT** | Redis-backed conversation history, read before and appended after every request regardless of outcome |
| Multi-turn Clarification Coordinator (`orchestrator.clarification_coordinator`) | **BUILT** | Invoked when Schema Mapping resolves zero tables; returns a clarifying question instead of a bare failure |

## Current build status summary

All 25 real agents across all six domains are **BUILT** and registered
(`packages/agent_runtime/navigraph_agents/main.py`'s `lifespan()` wires up
every one of them at startup). This reconciles `LIMITATIONS.md` items 7, 32,
and 35 (all marked RESOLVED as of this pass) — see those entries for the
history of how stale this document previously was.

What's genuinely still open is not "which agents exist" but specific,
already-logged functional gaps: OPA's allow-all placeholder policy (item 4),
Trino not yet the default execution route (item 3), `query.caching` not
wired into the live Request Orchestrator sequence (item 59), and no
mid-pipeline crash recovery given the LangGraph-to-plain-function reversal
(item 39). See `LIMITATIONS.md` for the complete, current list.
