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
terraform/      Azure infrastructure-as-code (validated skeleton, never applied)
packages/       Application services: gateway, agent_runtime, shared libraries
                (built by a parallel workstream, not part of this scaffold)
web/            Next.js web UI (built by a parallel workstream, not part of this scaffold)
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

This repository is in **Phase 1 (infra scaffolding)**. Only the Intent Understanding
agent is real; the rest of the ~25-agent architecture is designed but not yet
implemented. Terraform is a validated skeleton only and has never been applied — no
real Azure resources exist. See `LIMITATIONS.md` for the full list of known gaps.
