# Architecture Overview

This document is the canonical map of the NaviGraph agent architecture: the
request lifecycle, the six agent domains, and the ~25 named agents within them.
It is kept up to date as agents move from "designed" to "built" — see the status
table at the bottom.

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
specific event emitted at each step). The **Orchestrator** domain owns the
LangGraph graph that sequences all of the above and is responsible for
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
| Request Orchestrator (LangGraph graph) | DESIGNED |
| Session/Context Manager | DESIGNED |
| Multi-turn Clarification Coordinator | DESIGNED |

## Current build status summary

As of 2026-07-28, **only Intent Understanding (Understanding domain) is a real,
implemented agent**; it lives at
`packages/agent_runtime/navigraph_agents/understanding/intent_understanding/`.
Every other agent listed above is designed (its role, inputs, and outputs are
defined by this document and by `docs/architecture/agent-contract.md`) but not
yet implemented. See `LIMITATIONS.md` item 7 for why, and `CONTRIBUTING.md` for
how to add a new agent when its turn comes.
