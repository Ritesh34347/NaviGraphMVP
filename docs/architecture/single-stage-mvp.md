# Single-Stage MVP Architecture

`docs/architecture/overview.md` and `docs/architecture/data-flow.md` describe
NaviGraph's target agent map largely in terms of *domains* ("the Query
domain does X"). This document describes the thing that actually executes a
request today: **one linear, in-process pipeline — the "single stage" —
implemented by the real `RequestOrchestratorAgent`**
(`packages/agent_runtime/navigraph_agents/orchestrator/request_orchestrator/agent.py`),
which calls 20 real sub-agents directly, in a fixed order, with no graph,
no branching state machine, and no cross-agent negotiation: a 19-step linear
backbone, plus Caching wrapping Data Federation (a real lookup before it, a
real store after it on a miss — see "The real agent sequence" below).

This is not a simplified or hypothetical MVP sketch — it is a description of
real, tested, currently-running code (Phase 9, with the Caching integration
below added 2026-08-09 to resolve `LIMITATIONS.md` item 59).
`docs/architecture/overview.md`'s per-domain tables were, for a long time,
badly stale (see `LIMITATIONS.md` items 7, 32, and 35 — all RESOLVED as of
the 2026-08-09 docs-reconciliation pass); this document gives the
complementary, sequence-level answer to one question:
*what actually happens, in what order, when a request comes in today.*

## What "single-stage" means here

Phase 1's original design routed every request through a **LangGraph**
graph — a stateful, checkpointable, potentially-branching execution model
built for the Orchestrator domain's "Coordinator/Supervisor/Planning"
nodes. Phase 9 formally reversed that decision (see `DECISIONS.md`,
"Request Orchestrator is a plain Python async function, not a LangGraph
graph"): across 8 phases and ~22 real agents, nothing ever needed
graph-checkpointing or mid-pipeline resumability, and a plain direct-call
sequence had already been proven correct end-to-end by
`eval/pipeline_chain.py::run_full_pipeline` before being formalized into the
real orchestrator.

So "single-stage" means:

- **One `async def run(...)`, not a graph.** `RequestOrchestratorAgent.run()`
  awaits each sub-agent directly, in source order. There are no nodes, no
  edges, no separate execution engine deciding what runs next.
- **No branching except early-exit and one clarification fork.** The only
  control flow beyond "call the next agent" is (a) returning immediately
  with `outcome="failed"` the moment any stage reports an unrecoverable
  problem, and (b) one conditional detour into the Multi-turn Clarification
  Coordinator if Schema Mapping resolves zero tables. There is no retry
  loop, no re-planning, no agent calling another agent's sibling.
  Retries/error-handling across stages are explicitly this orchestrator's
  job, not a graph runtime's (see `overview.md`).
- **No mid-pipeline crash recovery.** If the process dies mid-request,
  the request is simply lost and must be re-asked — a deliberate,
  logged trade-off (`LIMITATIONS.md` item 39), not an oversight. This is
  the one concrete capability given up by dropping LangGraph.
- **One process, one trace.** All 20 agents run in-process in the
  `agent_runtime` service under a single OpenTelemetry span
  (`agent.request_orchestrator.run`), tagged with `tenant_id`, `trace_id`,
  and `session_id`. There is no network hop between agents on the hot path
  (each agent is also reachable standalone over HTTP via
  `POST /agents/{domain}/{agent_name}/invoke` per `agent-contract.md`'s
  "dual invocation" rule, but the orchestrator never uses that path itself).

## The real agent sequence

Example question used below: *"What was our churn rate by region last
quarter?"*

```
 gateway: POST /ask  (tenant/user/roles from Azure AD, trace_id minted)
     |
     v
 [side channel] Session Context Manager.get  -- read conversation history (Redis)
     |
     v
 data_source_id resolution  -- caller-supplied, else exactly-one-match
     lookup via tenant_id (navigraph_catalog); zero/multiple -> outcome=failed
     |
     v
 ===================== UNDERSTANDING (agents 1-6) =====================
  1. understanding.conversation             resolve pronouns/follow-ups against history
  2. understanding.intent_understanding     classify intent, extract entities   [LLM]
  3. understanding.metadata_discovery       list this data source's real catalog columns
  4. understanding.ontology                 resolve entities against the KG (Neo4j)
  5. understanding.semantic_retrieval       rank candidate columns for unresolved terms [LLM]
  6. understanding.schema_mapping           assemble final tables/columns/joins
     |
     |   zero tables resolved? --> orchestrator.clarification_coordinator [LLM]
     |                              outcome="needs_clarification", pipeline ends here
     v
 ======================== QUERY (agents 7-9) ==========================
  7. query.data_source_discovery            confirm each resolved table's real backing source
  8. query.sql_generation                   schema-grounded SQL from the mapping   [LLM]
     |
     v
 ====================== GUARDRAIL (agents 9-12) ========================
  9. guardrail.schema_constraint_validator  reject if SQL violates schema constraints
 10. guardrail.pii_exposure_checker         reject if the caller's role can't see a PII column
 11. guardrail.policy_authorization         OPA decision (tenant/role policy)
 12. query.sql_optimization                 rewrite/optimize the approved statement
 13. guardrail.query_cost_estimator         reject if estimated cost/rows exceed limits
     |
     v
 ==================== QUERY EXECUTION (agents 14-15) ====================
 14. query.execution_planning              build the final ExecutionPlan (route, row cap, timeout)
     |
     v
     query.caching (lookup)  -- keyed on (sql, params, data_source_id); Redis
     |
     +-- cache HIT  ------------------------------> skip straight to INSIGHT,
     |                                               using the cached result
     |
     +-- cache MISS (or a recoverable cache-backend error, treated the same)
     v
 15. query.data_federation                 execute for real (Snowflake direct connector today)
     |
     v
     query.caching (store)  -- only reached on a miss; caches the real result
     |
     v
 ======================= INSIGHT (agents 16-19) =========================
 16. insight.chart_selection               pick a chart shape for the result set
 17. insight.anomaly_outlier_highlighter   flag outliers deterministically
 18. insight.grounded_narrative_generation write a narrative, citing only returned values [LLM]
 19. insight.follow_up_suggestion          propose related questions               [LLM]
     |
     v
 [side channel] Session Context Manager.append_turn  -- persist this turn (Redis)
     |
     v
 outcome="answered": chart + narrative + follow-ups + full lineage trail returned to caller
```

Every one of the steps below (plus the session-manager and clarification
calls) emits `lineage_events`; the orchestrator forwards each stage's
events to `ops.lineage_recorder` (Postgres) immediately after that stage
runs — one incremental append per real upstream output, not one bulk write
at the end. A lineage-recording failure is logged and swallowed; it never
aborts the request, because lineage is an audit side-channel, not a
correctness gate. Caching's own lineage events are recorded the same way,
whether the operation was a lookup or a store.

### Step-by-step reference

| # | Registry key | Domain | What it actually does | Backing store/service |
|---|---|---|---|---|
| 1 | `understanding.conversation` | Understanding | Resolves the raw question against prior turns (e.g. "what about last month?") into a self-contained `resolved_question` | Session history passed in-memory from Session Context Manager |
| 2 | `understanding.intent_understanding` | Understanding | Classifies intent (trend/comparison/lookup/causal, etc.) and extracts entities | LLM (Claude via `navigraph_shared.llm`) |
| 3 | `understanding.metadata_discovery` | Understanding | Lists the real catalog columns registered for this `data_source_id` | Postgres (`metadata_catalog`) |
| 4 | `understanding.ontology` | Understanding | Resolves extracted entities/relationships against the knowledge graph | Neo4j |
| 5 | `understanding.semantic_retrieval` | Understanding | Ranks candidate catalog columns against any terms the ontology left unresolved | LLM |
| 6 | `understanding.schema_mapping` | Understanding | The single assembly point: merges ontology + retrieval + catalog into final tables/columns/joins | Deterministic (no LLM, no I/O) |
| — | `orchestrator.clarification_coordinator` | Orchestrator | Only runs if step 6 resolves zero tables; produces a clarifying question and ends the request as `needs_clarification` | LLM |
| 7 | `query.data_source_discovery` | Query | Confirms each resolved table actually maps to a reachable, real data source | Postgres (`metadata_catalog`) |
| 8 | `query.sql_generation` | Query | Generates schema-grounded SQL from the resolved mapping and intent | LLM |
| 9 | `guardrail.schema_constraint_validator` | Guardrail | Deterministic reject if the SQL violates known schema constraints | Deterministic |
| 10 | `guardrail.pii_exposure_checker` | Guardrail | Rejects if the caller's role would see a column flagged `is_pii` | Postgres (`metadata_catalog`) |
| 11 | `guardrail.policy_authorization` | Guardrail | Real OPA decision: deny-by-default RBAC + tenant ABAC (not a placeholder — see below) | OPA |
| 12 | `query.sql_optimization` | Query | Rewrites/optimizes the guardrail-approved statement | Deterministic |
| 13 | `guardrail.query_cost_estimator` | Guardrail | Rejects if estimated cost/row volume exceeds configured limits | Deterministic |
| 14 | `query.execution_planning` | Query | Builds the final `ExecutionPlan`: route, bind-parameterized SQL, row cap, timeout | Deterministic (real SQL-shape validation: single read-only `SELECT`/`WITH` only) |
| — (lookup) | `query.caching` | Query | Real cache lookup keyed on `(sql, params, data_source_id)`, tenant-prefixed; **resolves `LIMITATIONS.md` item 59** (this agent existed, tested and registered, since Phase 5 but was never actually called until 2026-08-09) | Redis |
| 15 | `query.data_federation` | Query | Executes the plan for real — **skipped entirely on a cache hit** | Snowflake (direct connector, default route) |
| — (store) | `query.caching` | Query | Real cache store of step 15's result, same key as the lookup above — only reached on a miss | Redis |
| 16 | `insight.chart_selection` | Insight | Picks a chart type/axes for the returned result shape (the cached or freshly-executed one) | Deterministic |
| 17 | `insight.anomaly_outlier_highlighter` | Insight | Flags statistical outliers in the result set | Deterministic |
| 18 | `insight.grounded_narrative_generation` | Insight | Writes a narrative; a citation-validation layer drops any claim not backed by a returned value | LLM |
| 19 | `insight.follow_up_suggestion` | Insight | Proposes related next questions | LLM |

A cache-backend failure (Redis unreachable, timeout, serialization error) on
either the lookup or the store is always recoverable and never blocks the
pipeline — it is recorded in the response's `errors` list (code
`cache_backend_unavailable`) and otherwise behaves exactly like a real miss:
step 15 still runs, the request still answers.

## Outcome model

Every request ends in exactly one of three outcomes, each with a fixed
confidence used for downstream evaluation scoring:

| Outcome | Confidence | When |
|---|---|---|
| `answered` | 1.0 | All steps completed (via a fresh execution or a cache hit); chart, narrative, and follow-ups are populated |
| `needs_clarification` | 0.5 | Schema Mapping (step 6) resolved zero tables; a clarifying question is returned instead of an answer |
| `failed` | 0.0 | Any other stage reported an unrecoverable error; `failure_stage` names exactly which registry key failed and `failure_reason` carries the detail |

`failure_stage` short-circuit points, in pipeline order:
`orchestrator.data_source_resolution` (zero/multiple data sources for the
tenant) → `understanding.intent_understanding` →
`understanding.metadata_discovery` → `query.data_source_discovery` →
`query.sql_generation` → `guardrail.schema_constraint_validator` →
`guardrail.pii_exposure_checker` → `guardrail.policy_authorization` →
`guardrail.query_cost_estimator` → `query.execution_planning` →
`query.data_federation`. A session turn is recorded (success, failure, or
clarification alike) so the next request in the same session has full
context, regardless of how this one ended.

## Real vs. stubbed infrastructure in this MVP

| Component | Status today | Detail |
|---|---|---|
| Snowflake execution | **Real** | `route="direct_connector"` is the only assigned route; a live, read-only account with zero write privileges (`LIMITATIONS.md` item 3, `DECISIONS.md` "Execution defaults to the direct Snowflake connector, not Trino") |
| Trino federation | Built, unused | `route="trino"` is fully implemented and unit-tested but not the default — promoting it is a one-line change in Execution Planning, gated on either a second real data source or an independent security review of Trino's own access control |
| Guardrail SQL-injection defense | **Real, structural** | Execution Planning's parser accepts only a single read-only `SELECT`/`WITH` statement, bind-parameterized values only, plus a hard row-cap/timeout — proven against an adversarial `; DROP TABLE` test, independent of OPA |
| OPA policy | **Real** (not a placeholder) | Deny-by-default RBAC + tenant ABAC (`infra/opa/policies/authz.rego`), hardened via adversarial tests (`LIMITATIONS.md` item 4, RESOLVED). Still narrow: no row-/column-level detail beyond PII (a separate agent's job) |
| Azure AD JWT verification | **Real, gated on configuration** | Gateway's `/ask` verifies a real bearer token via `navigraph_shared.auth.AzureAdTokenVerifier` (real RS256/JWKS checks, real crypto tests) when `AZURE_AD_TENANT_ID`/`AZURE_AD_AUDIENCE` are set, falling back to the caller-supplied trust model otherwise (`LIMITATIONS.md` item 23, RESOLVED). Never exercised against a live Entra tenant |
| Query-result caching | **Real, wired in** | `query.caching` now runs in the live sequence (lookup before Data Federation, store after — `LIMITATIONS.md` item 59, RESOLVED); Redis, tenant-prefixed key, flat 300s TTL |
| Session state | **Real** | Redis, same key/TTL pattern as the query-result cache |
| Lineage | **Real** | Postgres, one incremental append per stage, keyed by `trace_id` |
| Knowledge graph | **Real**, single instance | Neo4j; no HA/clustering yet (`LIMITATIONS.md` item 2) |
| Multi-tenant / multi-source | Structurally real, narrow in practice | Every call carries `tenant_id`; Snowflake and Postgres connectors both real, but only Snowflake has a registered tenant data source and a Trino catalog entry so far (`LIMITATIONS.md` item 1, partially resolved) |
| Semantic Model (`navigraph_semantic_model`) | **Real, not yet activated for any live tenant** | Knowledge-graph ingestion and OPA's per-tenant role vocabulary compile from it (`LIMITATIONS.md` item 61); SQL Generation's explicit `metric.aggregation` field exists and is tested but not yet wired into the live Request Orchestrator |
| Onboarding tooling (drift detection, Ontology Drafting agent, `onboard_data_source.py`) | **Real, never run against a live tenant** | Schema-hash drift detection is verified against a real Postgres catalog; the drafting agent and the CLI's offline `compile` step are unit-tested/smoke-verified in this sandbox, but `register`/`crawl`/`draft`/`activate` each need real infra this sandbox doesn't have (`LIMITATIONS.md` item 62) |

## Deliberately out of scope for this stage

- A second connector (Postgres/REST) to pressure-test the source-agnostic
  connector interface (item 1, resolved for Postgres).
- Row-/column-level ABAC beyond PII. Real Azure AD JWT verification itself
  is now built and gateway-wired (item 23, resolved), but has never run
  against a live Entra tenant, and OPA's tenant-claim check isn't
  meaningfully trustworthy until a real app registration is configured to
  emit one.
- Promoting the Trino route to default (item 3).
- Mid-pipeline crash recovery / resumability — the one capability traded
  away by dropping LangGraph (item 39).
- A structural (contract-level) fix for the column-role/alias threading
  the orchestrator currently does by hand in `_alias_for` — only partially
  resolved (item 28).
- The full docs-reconciliation pass across every `overview.md`/`data-flow.md`
  domain table (item 32) — this document intentionally covers only the
  request-lifecycle sequence, not every table in those files.

## Evidence this actually works

Phase 8's first full-real-model run of the 10-question golden set
(`eval/results/`) exercised this exact sequence end-to-end against live
Snowflake and a real Anthropic model: 60% full-pipeline success, with two
correct PII rejections, one correctly-caught narrative hallucination
(dropped by the citation-validation layer, not silently shipped), and one
real, logged SQL-aggregation gap (`SUM` vs `COUNT` for "how many X"
questions) left as a scoped follow-up rather than papered over
(`LIMITATIONS.md` item 38).

## Resolved since this document was first written

- **`query.caching` wired into the live sequence** (item 59, 2026-08-09) —
  see the step-by-step reference above.
- **OPA's policy was already real, not a placeholder** (item 4) — this
  document briefly and incorrectly said otherwise; corrected the same day.

## Sources

- `packages/agent_runtime/navigraph_agents/orchestrator/request_orchestrator/agent.py`
  and `contracts.py` — the real implementation this document describes.
- `DECISIONS.md` — "Request Orchestrator is a plain Python async function,
  not a LangGraph graph"; "Execute real SQL against live Snowflake now,
  ahead of Guardrail"; "Execution defaults to the direct Snowflake
  connector, not Trino".
- `LIMITATIONS.md` items 1, 2, 3, 4, 18, 23, 28, 32, 35, 38, 39, 59.
- `docs/architecture/agent-contract.md` — the common `AgentInput`/
  `AgentOutput` shape every one of the 20 agents implements.
- `packages/agent_runtime/navigraph_agents/query/caching/agent.py` and
  `contracts.py` — the real Caching agent this document's Caching rows
  describe.
