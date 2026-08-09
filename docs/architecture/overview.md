# Architecture Overview

This document is the canonical map of the NaviGraph agent architecture: the
request lifecycle, the six agent domains, and the ~25 named agents within them.
It is kept up to date as agents move from "designed" to "built" — see the status
table at the bottom.

**For the real, current request-lifecycle sequence** (which agents actually
run today, in what order, against what real infrastructure), see
[`single-stage-mvp.md`](./single-stage-mvp.md). The domain tables below are
known-stale (see `LIMITATIONS.md` items 32 and 35) and a full reconciliation
is deferred as its own dedicated phase — `single-stage-mvp.md` does not
replace that reconciliation, it only documents the orchestrator's real
19-agent call sequence accurately in the meantime.

## What NaviGraph does

NaviGraph answers natural-language business questions by combining
schema-grounded SQL generation with knowledge-graph semantic reasoning, across
multiple tenants and (eventually) multiple underlying data sources.

## Request lifecycle

```
 NL question
     |
     v
 [Understanding domain]  intent + entity extraction
     |
     v
 [Query domain]          semantic retrieval (catalog + knowledge graph)
     |
     v
 [Query domain]          schema-grounded SQL and/or graph query generation
     |
     v
 [Guardrail domain]      validation gate (policy + schema constraints, OPA)
     |
     v
 [Ops domain]            federated execution (Trino)
     |
     v
 [Insight domain]        chart selection
     |
     v
 [Insight domain]        grounded narrative generation
     |
     v
 [Insight domain]        follow-up question suggestion
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

## Agent domains and agents

Status legend: **BUILT** (real, tested implementation exists) · **DESIGNED**
(architecture and contract defined, not yet implemented — see `LIMITATIONS.md`
item 7).

### Understanding domain

Turns a raw natural-language question into structured intent, entities, and
disambiguated references.

| Agent | Status |
|---|---|
| Intent Understanding | **BUILT** |
| Entity Resolution | DESIGNED |
| Ambiguity Detection | DESIGNED |
| Conversation Context Tracker | DESIGNED |

### Query domain

Retrieves relevant schema/catalog/graph context and generates the queries that
will actually run.

| Agent | Status |
|---|---|
| Semantic Catalog Retrieval | DESIGNED |
| Knowledge Graph Retrieval | DESIGNED |
| SQL Generation | DESIGNED |
| Cypher Generation | DESIGNED |
| Metric Definition Resolver | DESIGNED |
| Query Plan Composer | DESIGNED |

### Guardrail domain

Validates generated queries and enforces authorization before anything
executes against real data.

| Agent | Status |
|---|---|
| Schema Constraint Validator | DESIGNED |
| Policy Authorization (OPA) | DESIGNED |
| Query Cost/Row-Limit Estimator | DESIGNED |
| PII Exposure Checker | DESIGNED |

### Ops domain

Executes validated queries and manages the operational lifecycle of a request.

| Agent | Status |
|---|---|
| Federated Query Executor (Trino) | DESIGNED |
| Result Caching | DESIGNED |
| Lineage Recorder | DESIGNED |
| Error/Retry Handler | DESIGNED |

### Insight domain

Turns raw query results into a chart, a grounded narrative, and next-step
suggestions.

| Agent | Status |
|---|---|
| Chart Selection | DESIGNED |
| Grounded Narrative Generation | DESIGNED |
| Follow-up Suggestion | DESIGNED |
| Anomaly/Outlier Highlighter | DESIGNED |

### Orchestrator domain

Coordinates the domains above into a single request lifecycle.

| Agent | Status |
|---|---|
| Request Orchestrator | **BUILT** |
| Session/Context Manager | **BUILT** |
| Multi-turn Clarification Coordinator | **BUILT** |

## Current build status summary

**This section, and most of the per-domain agent names/tables above, are
stale** — they describe Phase 1.5's state (only Intent Understanding real)
and were never updated as Phases 4-9 shipped ~25 real agents across every
domain under different, more concrete names than the ones listed above
(e.g. "Semantic Catalog Retrieval" above was actually built as "Semantic
Retrieval"; "Federated Query Executor"/"Result Caching" above are actually
`query.data_federation`/`query.caching`). See `LIMITATIONS.md` item 32 for
the full finding and item 35 for the specific Ops-domain-table correction
already made. The Orchestrator domain table immediately above is the one
exception, corrected for real in Phase 9 since leaving it DESIGNED would
now be actively false. A full reconciliation pass across every other
domain's table remains a deferred, logged recommendation, not done here.
