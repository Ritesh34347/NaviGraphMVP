# NaviGraph

NaviGraph is a production-grade, multi-tenant conversational BI platform. It answers
natural-language questions over enterprise data by combining **schema-grounded SQL
generation** with **knowledge-graph semantic reasoning**, so answers are both
statistically fluent and provably grounded in the customer's real schema, metric
definitions, and business semantics.

NaviGraph is designed from day one to be:

- **Multi-tenant** — every request carries a `tenant_id` and every agent, cache key,
  and lineage record is scoped to it.
- **Multi-source** — the query federation layer (Trino) is architected to span
  multiple underlying data warehouses/databases, even though only one connector
  (Snowflake) is implemented today (see [`LIMITATIONS.md`](./LIMITATIONS.md)).
- **Explainable** — every answer carries a lineage trail from the natural-language
  question down to the SQL/Cypher that produced it, and a grounded narrative that
  cites the numbers it used.
- **Governed** — schema-grounded generation, a validation gate before execution, and
  a policy engine (OPA) for authorization are first-class parts of the request
  lifecycle, not bolted on later.

## How it works, in one paragraph

A user asks a question in plain English. An **Understanding** agent extracts intent
and entities. A **Query** agent retrieves the relevant parts of the semantic
catalog and knowledge graph, then generates schema-grounded SQL and/or graph
queries. A **Guardrail** agent validates the generated query against policy and
schema constraints before anything executes. Trino federates execution across
registered data sources. An **Insight** agent selects an appropriate chart and
writes a grounded narrative explanation, and suggests relevant follow-up questions.
Every stage emits a lineage event so the full chain of reasoning is auditable after
the fact. See [`docs/architecture/overview.md`](./docs/architecture/overview.md) for
the full agent map and [`docs/architecture/data-flow.md`](./docs/architecture/data-flow.md)
for the end-to-end sequence.

## Repository layout

```
infra/          Local-first docker-compose stack + Terraform skeleton for Azure
terraform/      Azure infrastructure-as-code (a real environment has been applied
                to Azure as of Phase 10b; CI itself still only ever plans, never applies)
packages/       Application services: gateway, agent_runtime (25 real agents),
                connector_sdk, federation, knowledge_graph, lineage, metadata_catalog, shared
web/            Next.js web UI (minimal scaffold today: landing page + NextAuth
                wiring; no chat/BI interface calling the gateway's /ask yet)
docs/           Architecture docs, ADRs, runbooks
tools/          Dev scripts and templates (e.g. smoke-test.sh, agent scaffolding)
.github/        CI workflows
```

## Quickstart (local dev)

```bash
# 1. Copy env template and fill in local values
cp infra/.env.example infra/.env

# 2. Bring up the full local stack (postgres, neo4j, redis, otel, prometheus,
#    grafana, opa, trino, agent-runtime, gateway, web)
docker compose -f infra/docker-compose.yml up -d

# 3. Wait for all services to report healthy, then run the smoke test
tools/scripts/smoke-test.sh
```

See [`docs/runbooks/local-dev-smoke-test.md`](./docs/runbooks/local-dev-smoke-test.md)
for a detailed walkthrough, including Azure AD app registration values and
troubleshooting for common first-boot issues (Neo4j slow start, Trino worker not
joining the coordinator, OPA bundle load failures).

## Project documents

- [`LIMITATIONS.md`](./LIMITATIONS.md) — what is deliberately not built yet, and why.
- [`DECISIONS.md`](./DECISIONS.md) — architecture decisions in ADR-style form.
- [`BUILD_LOG.md`](./BUILD_LOG.md) — a running log of what was built, by whom/what
  workstream, and when.
- [`docs/`](./docs/) — architecture, ADRs, and operational runbooks.
- [`CONTRIBUTING.md`](./CONTRIBUTING.md) — how to contribute, including how to add a
  new agent.
- [`SECURITY.md`](./SECURITY.md) — vulnerability disclosure process.

## Status

**Updated 2026-08-09** (this section previously described Phase 1's state
long after later phases had superseded it — see `LIMITATIONS.md` items 7,
32, and 35).

All 25 agents across the Understanding, Query, Guardrail, Insight, Ops, and
Orchestrator domains are real and built (Phases 2-9), called end-to-end by a
real Request Orchestrator against live Snowflake, Neo4j, Postgres, and
Redis — see [`docs/architecture/single-stage-mvp.md`](./docs/architecture/single-stage-mvp.md)
for the exact 19-agent call sequence. Real Azure infrastructure exists as of
Phase 10b (resource group, VNet, ACR, a 2-node AKS cluster, Key Vault,
Postgres Flexible Server, Entra app registration), created via a real,
human-approved `terraform apply` — Terraform itself still never applies from
CI.

What's still genuinely deferred, not designed-but-unbuilt: OPA already
enforces a real RBAC/ABAC policy (not a placeholder), but it has no real
Azure AD JWT verification behind it yet, so it trusts caller-supplied
claims, and it never sees row-/column-level detail beyond PII; only one
Snowflake connector exists; Trino federation is built but not yet the
default execution route; the built `query.caching` agent isn't yet wired
into the live request pipeline; and SOC 2 controls are scaffolded, not
audited. See `LIMITATIONS.md` for the complete, current list.
