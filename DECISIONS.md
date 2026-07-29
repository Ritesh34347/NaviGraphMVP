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

## 2026-07-30 — Understanding-domain pipeline order: Conversation before Intent Understanding, deterministic before LLM-backed

We chose a fixed Understanding-domain pipeline order — Conversation →
Intent Understanding → Metadata Discovery → Ontology → Semantic Retrieval
→ Schema Mapping — rather than treating the six agents as independently
orderable. Conversation must precede Intent Understanding: it rewrites a
follow-up ("what about last quarter?") into a standalone question, and
Intent Understanding has to classify the *resolved* question, not a
fragment. We also chose two-tier term resolution over one fuzzy pass:
Ontology Agent resolves business terms via the knowledge graph's curated,
zero-hallucination `BusinessConcept`/synonym match first; only terms it
can't resolve go to Semantic Retrieval's LLM call, which is hard-
constrained to a closed candidate list built from Metadata Discovery's
real catalog inventory (the LLM can select an existing column or say "no
match," never invent one — every returned ID is validated against the
candidate set before being trusted). We considered a single LLM-backed
resolution pass for every term and rejected it: it would call an LLM (cost,
latency, hallucination risk) even for the common case where a term already
has an exact glossary match, and Phase 4's real integration test confirmed
the split works as intended — "units traded" resolved for free via
Ontology, "market" correctly fell through to Semantic Retrieval.

## 2026-07-29 — Execute real SQL against live Snowflake now, ahead of Guardrail, with compensating controls

We chose to build Data Federation to actually execute generated SQL against
the real Snowflake account this phase, rather than stubbing execution until
the Guardrail domain (real OPA RBAC/ABAC/row-column policy) exists —
confirmed explicitly with the user before building, alongside a live,
read-only `SHOW GRANTS TO ROLE FIDELITY_ANALYST_ROLE` check (also
user-approved) that verified the account has zero write privileges. We
considered stubbing execution entirely until Guardrail lands and rejected
it: it would leave the Query domain's core value (agents that actually
answer questions) unproven for an entire extra phase, and the real risk
this phase (executing unsafe or injected SQL) is fully addressed by
structural compensating controls that don't depend on Guardrail existing at
all — Execution Planning Agent's real string-masking SQL parser (single
read-only `SELECT`/`WITH` statement only, no exceptions), bind-parameterized
literal values only (never string-interpolated), and a re-verified hard
row-cap/timeout. This is explicitly framed as compensating controls for a
real, temporary gap (see `LIMITATIONS.md` item 18), not a substitute for
Guardrail — the adversarial test proving the safety gate rejects a
malicious `; DROP TABLE` statement (`tests/integration/query_pipeline/`) is
required evidence for this decision, not optional polish.

## 2026-07-29 — Execution defaults to the direct Snowflake connector, not Trino

We chose `route="direct_connector"` as Execution Planning Agent's only
assigned route this phase, even though the real Snowflake catalog is
already registered in Trino and `route="trino"` is fully built and unit
tested on `ExecutionPlan`. We considered defaulting to Trino now (it's
already wired, and federation-shaped routing is the eventual goal) and
rejected it: routing real execution through a general-purpose distributed
SQL engine's own access-control surface — which has not been independently
reviewed — during the exact window there is no policy gate (Guardrail,
still Phase 6) to catch a mistake trades a known-narrow risk (a single,
audited direct connector) for a broader, unaudited one. `route="trino"`
stays real, tested code precisely so switching the default later is a
one-line change in Execution Planning Agent, not a rebuild — it becomes the
default once either a second real data source creates genuine federation
need, or Trino's access-control configuration gets its own independent
review.

## 2026-07-29 — Trino gets its own `navigraph_federation` package, not a `TrinoConnector` inside `connector_sdk`

We chose to build Trino integration as a standalone package
(`packages/federation`) rather than as another `Connector` implementation
registered in `navigraph_connectors`. We considered the latter (it would
reuse the existing `Connector` interface/registry machinery directly) and
rejected it: `navigraph_connectors`' registry models a tenant's actual
`DataSource` rows (Snowflake account, eventually Postgres, etc.) — but
Trino isn't itself a tenant's data source; there is no "Trino data"
independent of whatever real data source it federates. Registering it via
`source_type="trino"` would imply a `DataSource` row that doesn't
correspond to any real, independent system a tenant configured, which is
conceptually wrong. `navigraph_federation.TrinoClient` instead reuses
`navigraph_connectors.base.QueryResult`/`ConnectionTestResult`'s shapes
directly (so a Trino result is interchangeable with a direct-connector
result for any caller), without pretending to be one.

## 2026-07-29 — Caching agent's key is tenant-prefixed as a literal segment, not folded into the hash, with a reserved policy-version segment

We chose
`navigraph:v1:{tenant_id}:query_cache:policy={policy_version}:{sha256(sql,params,data_source_id)}`
as the real cache-key shape, with `tenant_id` as a literal, readable prefix
segment rather than folded into the hash alongside everything else. We
considered hashing `tenant_id` in with the rest of the fingerprint (simpler,
one hash covers everything) and rejected it: a literal prefix means two
tenants' cache entries can never collide even under a hash collision on an
otherwise-identical fingerprint (verified directly in
`packages/agent_runtime/navigraph_agents/query/caching/tests/`, not just
inferred from the format string), and it lets a future "flush every cached
entry for tenant X" operation use a `SCAN`-based prefix match without
reversing a hash first. `policy_version` (defaulted to `"none"`) is
reserved now, specifically so a future Guardrail-driven policy variation
(e.g. a masking policy that changes what a cached result is even allowed to
contain) becomes "populate an existing key segment," never a cache-key
migration. TTL is a flat, conservative 300-second default for v1 — a
deliberate simplification (`LIMITATIONS.md` item 22), not a per-intent
policy decision made unilaterally here.

## 2026-07-30 — Schema Mapping is the only assembly point; sibling agents don't cross-import each other's contracts

We chose to have Schema Mapping Agent merge Ontology's and Semantic
Retrieval's outputs into one deduplicated, role-assigned,
join-annotated structure, rather than having any upstream agent do partial
assembly. To keep agents independently buildable and testable without a
Coordinator existing yet, agents whose input shape mirrors a sibling
agent's output (e.g. Schema Mapping's `ConceptResolution`/
`RelationshipResolution`/`TermMatch`/`CatalogInventoryEntry`) declare that
shape locally rather than importing the sibling package's `contracts`
module directly. We considered direct cross-package imports (simpler,
guarantees the shapes never drift) and rejected it as the wrong long-term
pattern: a future Coordinator is what should pass one agent's output into
the next agent's input, so no agent package should depend on another
agent package's internals. The real cost of this choice showed up
immediately: `schema_mapping`'s locally-declared `TermMatch` was missing
the `rationale` field `semantic_retrieval`'s real `TermMatch` has, caught
only when Phase 4's real integration test wired the two together. Accepted
as the correct tradeoff (a Coordinator-mediated contract, verified by
integration tests, over a compile-time-enforced but architecturally wrong
direct dependency) rather than reversed.

## 2026-07-29 — Guardrail agents split around the `GeneratedSql`/`OptimizedSql` data-availability boundary

We placed three of the four Guardrail agents (Schema Constraint Validator,
Policy Authorization, PII Exposure Checker) between SQL Generation and SQL
Optimization, and the fourth (Query Cost/Row-Limit Estimator) between SQL
Optimization and Execution Planning — rather than following
`docs/architecture/overview.md`'s table order, which is a status listing,
not a sequencing statement. We considered placing all four after SQL
Optimization (matching the doc's visual order) and rejected it: `GeneratedSql`
(SQL Generation's own output) is the only contract shape anywhere in the
Query-domain chain carrying `referenced_tables`/`referenced_columns` —
`OptimizedSql` and `ExecutionPlan` don't retain that structure. Three of
the four agents structurally need those fields; placing them downstream of
where SQL Optimization strips that data away would have made real
enforcement impossible without adding fields back onto sibling contracts
for no reason other than doc-order fidelity.

## 2026-07-29 — OPA and PII Exposure Checker are two separate enforcement layers, not one

We chose to keep column-level PII enforcement entirely in the PII Exposure
Checker agent's Python code (querying `CatalogColumn.is_pii` directly
against the live Postgres catalog), while `infra/opa/policies/authz.rego`
handles RBAC and tenant ABAC only. We considered pushing PII/column
sensitivity facts into OPA's own `data` document so one policy engine
decided everything, and rejected it: `infra/opa/conf/config.yaml` runs OPA
bundle-less (policies loaded only from mounted `.rego` files), and there is
no live-data-API integration anywhere in this stack to push dynamic
catalog facts (which change as new columns get tagged) into OPA without
building that integration from scratch this phase. `docs/architecture/overview.md`
already names these as two distinct agents — Policy Authorization and PII
Exposure Checker — so this boundary is load-bearing documentation, not
incidental phrasing. Keeping Rego itself simple and stateless also keeps it
independently adversarially-testable (see `tests/security/test_opa_policy_adversarial.py`)
without needing a live database in the loop.

## 2026-07-29 — Policy Authorization fails closed on OPA-unreachable, the deliberate opposite of Caching's fail-open

We chose to treat any exception from the real OPA call (connection
refused, timeout, non-2xx) as a single, non-recoverable
`AgentError(code="opa_unreachable")` for the whole batch, discarding
anything already authorized earlier in the same call. We considered
mirroring `CachingAgent`'s fail-OPEN convention (`cache_backend_unavailable`,
`recoverable=True`) for consistency with an existing precedent, and
rejected it: a cache miss costs nothing security-wise (the caller just
re-executes against the real source), but "the policy engine didn't
answer" silently becoming an implicit allow would disable tenant
isolation and RBAC entirely for that request. Verified live via
`tests/security/test_insufficient_roles_fail_closed.py::test_opa_unreachable_fails_closed_not_open`
against a real, deliberately unreachable address — not mocked.

## 2026-07-29 — `is_pii` lives on `CatalogColumn`, not `ColumnGlossary`

We added the new PII flag directly to `CatalogColumn` rather than to
`ColumnGlossary` (the existing business-enrichment table). We considered
`ColumnGlossary` (it already carries business-facing metadata) and
rejected it: `ColumnGlossary` is optional/nullable enrichment — most real
columns in `FIDELITY_POC` have no glossary entry at all — but PII
sensitivity must apply to every column unconditionally, glossaried or not.
`CatalogColumn` is the row that unconditionally exists for every crawled
column, so that's where a mandatory, defaulted-false flag belongs.

## 2026-07-29 — `OpaClient` lives in `navigraph_shared`, not a new package

We added `HttpOpaClient`/`FakeOpaClient` to `packages/shared/navigraph_shared/opa/`,
mirroring `navigraph_shared/llm/client.py`'s exact ABC/real/fake triad,
rather than creating a new standalone package (the way `navigraph_federation`
was split out for Trino in Phase 5). We considered a new package for
symmetry with that precedent and rejected it: `httpx` was already a real,
declared `navigraph-shared` dependency (unlike Trino, which needed a new
driver dependency), and OPA authorization is a cross-cutting concern
`gateway` will eventually need too, not just `agent_runtime` — the same
reasoning that already put `LLMClient` in `shared` rather than in
`agent_runtime` alone.

## 2026-07-29 — Real adversarial testing surfaced two live Rego bugs and one PII-tagging gap before shipping

We treated `tests/security/`'s real, live-OPA adversarial run as a
required gate before considering Phase 6 done, not a formality — and it
caught two real policy-correctness bugs (a `null` `claims` value silently
producing an empty `deny_reasons` despite correctly denying; an
empty-string `tenant_id` matching an empty-string claim being incorrectly
*allowed*) plus one real data-inconsistency bug (the initial PII backfill
tagged `fidelity_poc_snowflake_v2`, but `DataSourceDiscoveryAgent`
actually resolves `STAGING_TRANSACTIONS`/`CUSTOMER_INFORMATION` to the
older `fidelity_poc_snowflake` registration at runtime — caught by
`tests/integration/guardrail_pipeline/` returning a false "cleared" for a
real PII statement). All three were fixed before this phase was marked
done, not deferred to a follow-up — consistent with the user's standing
rule that a security-relevant component is never "done" without a real
adversarial test proving it.
