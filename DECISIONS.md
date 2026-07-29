# Decisions

Architecture-decision-record style log of the significant calls made while
scaffolding NaviGraph. Each entry is written in the first person plural, as of the
date it was made, with the alternative we considered noted briefly. See also
`docs/adr/` for selected decisions written up as formal ADRs.

---

## 2026-07-28 — Python 3.12 + FastAPI + Pydantic v2 + LangGraph for the agent runtime

We chose Python 3.12 with FastAPI, Pydantic v2, and LangGraph as the stack for
`packages/agent_runtime`, rather than a TypeScript-only stack across the whole
backend. LangGraph's graph-of-agents model maps directly onto our ~25-agent,
multi-domain architecture (Understanding, Query, Insight, Guardrail, Ops,
Orchestrator), and Python has the deepest ecosystem for LLM tooling, data/schema
introspection libraries, and the eventual Snowflake/Trino client libraries our
agents depend on. Pydantic v2 gives us fast, strict validation for the
`AgentInput`/`AgentOutput` contract every agent must honor. We considered a
TypeScript-only stack (Next.js API routes or a Node backend end-to-end) for
language uniformity with the web UI, but rejected it because the LLM-orchestration
and data-engineering ecosystems we depend on most heavily are materially more
mature in Python, and we're not willing to trade that maturity for one-language
convenience this early.

## 2026-07-28 — Modular monolith for the 25-agent runtime

We chose to run all ~25 agents inside a single `agent_runtime` FastAPI service (a
modular monolith, with each agent as an isolated module exposing both an
in-process LangGraph node and a thin HTTP wrapper) rather than deploying one
microservice per agent. At our current scale, one-microservice-per-agent would
multiply deployment, networking, and observability overhead by 25x before we have
evidence any individual agent needs independent scaling or an independent release
cadence. The dual invocation pattern (in-process call for the orchestrator's hot
path, HTTP wrapper for isolated testing and the eval harness) gives us the ability
to peel any agent out into its own service later without a rewrite, since the
contract boundary is already service-shaped. We considered full microservices from
day one and rejected it as premature operational complexity for a system whose
agent boundaries are still being tuned.

## 2026-07-28 — Local-first, Azure-targeted Terraform that is never applied

We chose to make docker-compose the everyday inner loop for local development,
while writing Terraform for Azure now as a validated skeleton that is deliberately
never applied during this phase. This lets every engineer iterate fast on a laptop
without cloud credentials or cost, while still forcing us to think through the real
target topology (AKS, ACR, Key Vault, managed Postgres, networking, Entra app
registration) early enough that the eventual cloud migration is a deployment
exercise rather than a design exercise. CI runs `terraform fmt`, `validate`, and
`plan` (gated behind credential presence) to keep the skeleton honest, but `apply`
must never appear in CI — only a human, later, with real sign-off. We considered
deferring Terraform entirely until a cloud deployment was imminent, and rejected
that because retrofitting infra-as-code onto an already-running system tends to
produce Terraform that doesn't match reality; writing it alongside the local stack
keeps the two honest with each other.

## 2026-07-28 — Trino stood up for real federation despite one registered source

We chose to stand up a real Trino coordinator/worker cluster in the local compose
stack now, even though zero real catalogs are registered yet (Snowflake catalog
wiring is a later phase). Federation is a core product promise — "multi-source" —
and proving the cluster topology, health-checking, and worker-joins-coordinator
behavior early means the only remaining work later is catalog configuration, not
debugging a distributed system we've never run. We considered deferring Trino
entirely until Snowflake credentials were available and querying Snowflake
directly in the interim, and rejected that because it would let single-source
assumptions leak into the Query agents' generated SQL in ways that would be
expensive to unwind later.

## 2026-07-28 — Anthropic Claude as default LLM provider behind a provider-agnostic client

We chose Anthropic's Claude, configured via an `ANTHROPIC_API_KEY` environment
variable, as the default LLM provider for all agents, accessed exclusively through
a provider-agnostic client abstraction (being built as shared infrastructure
elsewhere in `packages/`). Every agent codes against that abstraction, never
against the Anthropic SDK directly, so swapping providers or running comparative
evals later is a configuration change, not a rewrite. We considered hard-coding
directly against the Anthropic SDK for simplicity, and rejected it because the
agent contract (`docs/architecture/agent-contract.md`) commits to
provider-swappable model metadata (`model_version`, `prompt_version`) as first-class
output fields, which only makes sense if the provider boundary is real from the
start.

## 2026-07-28 — Next.js for the web UI

We chose Next.js for `web/`. It gives us server-rendered pages for fast initial
load of conversational BI sessions, a mature React ecosystem for the chart and
chat-style components the product needs, and a deployment story (static + server
functions) that maps cleanly onto both local docker-compose and an eventual Azure
target. We considered a plain client-side React SPA (e.g. Vite + React Router) and
rejected it because we want server-side rendering and API-route colocation
available without adopting a second framework later if/when we need them.

## 2026-07-29 — Connector SDK decoupled from the metadata catalog's storage model

We chose to make `navigraph_connectors` (the data-source plugin interface)
return plain Pydantic descriptors with zero dependency on SQLAlchemy or the
catalog's own tables, rather than having connectors write directly into
catalog rows. `navigraph_catalog`'s crawler is the only thing that
translates a connector's `introspect_schema()` output into catalog storage.
We considered letting connectors own persistence directly (simpler for a
single connector) and rejected it because it would make the "source-agnostic
SDK" claim untestable — the whole point of building the abstraction now,
with only Snowflake implemented, is to be able to pressure-test it with a
second, differently-shaped connector (e.g. Postgres) later without touching
the catalog schema at all. This paid off immediately: running the real
crawler against a live Snowflake account this same phase surfaced a bug
(the `INFORMATION_SCHEMA` metadata schema wasn't excluded from
`introspect_schema()`, polluting real business tables with ~60 Snowflake
system views) that was fixed entirely inside the connector, with no changes
needed to the catalog's models, API, or crawler logic.

## 2026-07-29 — Two-tier knowledge graph: reference data + business-concept mapping, no fact-level data

We chose to model `packages/knowledge_graph` as two tiers only: bounded
reference/dimension nodes grounded in the real schema (`Asset`, `Market`,
`Exchange`, `Sector`, `Industry`, `Channel`, `CustomerType`, `RiskLevel`,
`InvestmentCapacityBand`) and a business-concept-to-schema mapping layer
(`BusinessConcept`, `Table`, `Column`, `RelationshipConcept`). We explicitly
excluded individual customers and individual transactions from the graph —
confirmed with you rather than decided unilaterally, since it shapes what
Phase 4's agents can ask the graph for later. Every one of the 50 approved
business questions gets answered by generated SQL against Snowflake; the
graph's job is reference-data validation (e.g. confirming `"Technology"` is
a real sector value before it's injected into a SQL filter) and
business-term resolution, never a duplicate copy of high-cardinality data.
We considered materializing customer-holds-asset edges (from the
pre-aggregated `CUSTOMER_ASSET_AGG`/`CUSTOMER_MARKET_AGG` tables) directly
and rejected it: still customer-cardinality, wrong shape for a
single-instance Neo4j Community graph, and it would duplicate the warehouse
rather than add traversal value the SQL layer can't already provide.

## 2026-07-29 — Business glossary lives in Postgres (`metadata_catalog`), not crawled separately by the graph

We chose to ingest the real `SCHEMA_ENRICHMENT` glossary (business names,
synonyms, descriptions — ~41 real rows) into a new `ColumnGlossary` table in
the already-shipped `metadata_catalog` package, rather than having
`knowledge_graph` crawl it from Snowflake directly. `navigraph_catalog`
remains the single source of truth for everything schema-related (structure
*and* glossary); `knowledge_graph` becomes a fully rebuildable derived
semantic layer that needs zero Snowflake credentials for the parts of
ingestion that only touch structure/glossary — it only talks to Snowflake
directly for the one thing the catalog genuinely can't supply: live
reference-data values (distinct assets, sectors, markets, etc.), which are
data, not structure. We considered keeping `knowledge_graph` fully
self-contained with its own crawl-and-match logic and rejected it as
unnecessary duplication of the case-insensitive table/column matching logic
already proven in Phase 2's crawlers, for a real, if closed, Phase 2 package
we confirmed was safe to extend.

## 2026-07-29 — Exchange is a real graph node, decided from live data rather than assumed

We chose to model `Exchange` as its own node type with
`(:Market)-[:PART_OF_EXCHANGE]->(:Exchange)`, rather than treating
`EXCHANGEID` as a bare property on `Market`. This was decided from a live,
read-only query against the real account during planning, not assumed: real
exchanges group multiple real markets (e.g. `ATHEX` groups `EBB`, `XATH`,
`ENAX`; verified again post-ingestion by querying the live graph and getting
exactly those three markets back). Modeling it as a property would have
silently lost this real 1-to-many structure.

## 2026-07-29 — Sector/Industry are independent siblings off Asset, not a hierarchy

We chose `(:Asset)-[:IN_SECTOR]->(:Sector)` and
`(:Asset)-[:IN_INDUSTRY]->(:Industry)` as independent edges rather than a
`(:Sector)-[:HAS_INDUSTRY]->(:Industry)` hierarchy. A live query during
planning found the real data is ~90% clean as a hierarchy but not perfectly
— `"Building Materials"` genuinely appears under both `"Corporate"` and
`"Basic Materials"` — and only about half of real assets have any
sector/industry at all (bonds/MTF funds legitimately have neither). A
strict hierarchy edge would have forced a false single-parent choice for
the one real violation; siblings-off-`Asset` makes no such claim and costs
nothing in query-ability for the questions we actually need to answer.
Edges are only created when the source value is non-null — no placeholder
"Unclassified" nodes.
