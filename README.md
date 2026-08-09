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
  multiple underlying data warehouses/databases; Snowflake and Postgres
  connectors are both real today, though only Snowflake has a registered
  tenant data source and a Trino catalog entry so far (see
  [`LIMITATIONS.md`](./LIMITATIONS.md) item 1).
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
packages/       Application services: gateway, agent_runtime (26 real agents),
                connector_sdk, federation, knowledge_graph, lineage, metadata_catalog,
                semantic_model, mcp_server, slack_bot, shared
web/            Next.js web UI: landing page + NextAuth wiring + a real chat UI
                (web/src/app/chat) calling the gateway's /ask
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
for the exact real agent call sequence (20 agents). Real Azure infrastructure exists as of
Phase 10b (resource group, VNet, ACR, a 2-node AKS cluster, Key Vault,
Postgres Flexible Server, Entra app registration), created via a real,
human-approved `terraform apply` — Terraform itself still never applies from
CI.

What's still genuinely deferred, not designed-but-unbuilt: OPA already
enforces a real RBAC/ABAC policy (not a placeholder), and the gateway now
real-verifies an Azure AD bearer token when configured, but this has never
run against a live Entra tenant, and it never sees row-/column-level
detail beyond PII; Trino federation is built but not yet the default
execution route; and SOC 2 controls are scaffolded, not audited.
`query.caching` is now wired into the live request pipeline (real lookup
before, and store after, Data Federation). A new `navigraph_semantic_model`
package (a versioned, per-tenant config artifact) now drives knowledge-graph
ingestion and OPA's per-tenant role vocabulary, replacing hardcoded
Python/SQL/Rego for those two consumers — but no live tenant has been
migrated to one yet, and it isn't wired into the live Request Orchestrator.
Real onboarding tooling now exists too (Phase 13): schema-hash drift
detection on the catalog, an onboarding-time-only Ontology Drafting agent
(26th real agent) that proposes a first-draft Semantic Model from a
crawled schema for a human to review, and a CLI
(`tools/scripts/onboard_data_source.py`) chaining registration → crawl →
drafting → compile → activation — but this pipeline has never been run
against a real tenant either.

Phase 14 added the three client-facing surfaces: a real chat UI
(`web/src/app/chat`), an agentic tool-surface API (`packages/mcp_server`,
a real MCP server wrapping `/ask` for external agents like Claude
Desktop), and a Slack bot (`packages/slack_bot`) that answers
`@NaviGraph` mentions. All three are real and tested, but neither has a
real per-caller tenant mapping (both use one fixed dev-mode tenant) nor
has been exercised against a live Slack workspace/Claude Desktop install
in this sandbox. See `LIMITATIONS.md` for the complete, current list.
