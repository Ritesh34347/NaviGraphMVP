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
infra/          docker-compose stack (local dev) + real infra/k8s/ Kustomize manifests
terraform/      Azure infrastructure-as-code, applied for real to a live dev environment
packages/       gateway, agent_runtime (25 real agents), connector_sdk, metadata_catalog,
                knowledge_graph, federation, lineage, shared
web/            Next.js web UI, including a real demo chat interface (src/app/ChatDemo.tsx)
eval/           Golden-set questions + LLM-as-judge evaluation harness
tests/          Integration pipeline chains + adversarial security suites
docs/           Architecture docs, ADRs, runbooks, product/security/testing references
tools/          Dev scripts and templates (smoke-test, canary_gate, new-agent scaffolding)
.github/        CI + CD (build/push/canary-rollout/promote) workflows
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

**Living process logs** (updated continuously as the project is built):

- [`LIMITATIONS.md`](./LIMITATIONS.md) — every known gap, real bug found, and its
  resolution, numbered and cross-referenced (80 items as of this writing).
- [`DECISIONS.md`](./DECISIONS.md) — every real architecture/implementation decision,
  dated, with rationale and consequences (~53 entries).
- [`BUILD_LOG.md`](./BUILD_LOG.md) — a phase-by-phase narrative of what was built and
  verified (14 phases, Phase 1 through the real cloud deployment).

**[`NaviGraphSpec.md`](./NaviGraphSpec.md)** — the comprehensive build
specification: process discipline, every SDLC phase in instructional
detail, the agent contract pattern, and a real post-launch bug-class
catalog — everything needed to build a system like this the same way,
start to finish.

**Product & technical reference**:

- [`docs/product/prd.md`](./docs/product/prd.md) — product requirements.
- [`docs/architecture/overview.md`](./docs/architecture/overview.md) — the full agent
  map (25 real agents across 6 domains).
- [`docs/architecture/system-architecture.md`](./docs/architecture/system-architecture.md)
  — deployment topology, tech stack, canary rollout mechanics.
- [`docs/architecture/data-flow.md`](./docs/architecture/data-flow.md) — one real
  question traced end to end through every stage.
- [`docs/architecture/data-model.md`](./docs/architecture/data-model.md) — the real
  catalog/knowledge-graph/lineage schemas.
- [`docs/product/api-reference.md`](./docs/product/api-reference.md) — real endpoint
  reference for `gateway` and `agent-runtime`.
- [`docs/security/security-compliance.md`](./docs/security/security-compliance.md) —
  SOC 2-oriented controls mapping.
- [`docs/testing/test-strategy.md`](./docs/testing/test-strategy.md) — the real test
  pyramid (unit/integration/security/eval/CI).
- [`docs/runbooks/`](./docs/runbooks/) — local dev, `kind` validation, and production
  operations runbooks.
- [`docs/product/glossary.md`](./docs/product/glossary.md) — business and platform
  terms.
- [`ONBOARDING.md`](./ONBOARDING.md) — new-engineer onboarding guide.
- [`CHANGELOG.md`](./CHANGELOG.md) — release notes, one entry per phase.
- [`CONTRIBUTING.md`](./CONTRIBUTING.md) — how to contribute, including how to add a
  new agent.
- [`SECURITY.md`](./SECURITY.md) — vulnerability disclosure process.

## Status

All 10 build phases are complete. **25 real agents** across all 6 domains
(Understanding, Query, Guardrail, Insight, Ops, Orchestrator) are built, tested, and
deployed. The platform is **live on Azure Kubernetes Service**
(`https://app.navigraph.51-8-46-125.nip.io`), backed by real Snowflake data, a real
Neo4j knowledge graph, real OPA policy enforcement, and real Anthropic LLM calls —
with CI/CD, canary rollout, an eval harness, and an adversarial security test suite
all exercised against that live environment. Trino is registered but
`direct_connector` remains the default execution route (see `DECISIONS.md`'s Phase 5
entry). See `LIMITATIONS.md` for the current, honestly-scoped list of what's still
deliberately deferred (Azure AD JWT verification, a registered domain in place of
`nip.io`, and others) — none of them block real, live use of the platform today.
