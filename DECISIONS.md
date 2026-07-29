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

## 2026-07-29 — Chart Selection consumes a mirrored, role-bearing column list, not raw-value inference

We chose `ChartColumnRef` (mirroring `schema_mapping.contracts.ResolvedColumnRef`'s
role-bearing fields, plus a new `result_alias` field) as Chart Selection's
input, rather than having it infer measure/dimension/temporal signal from
`DataFederationResult.final_rows`' raw cell values. We considered
raw-value inference (it would need no new field, no caller-side threading)
and rejected it: `final_rows`' cells are untyped `Any` — a Snowflake
`NUMBER` can arrive as `int`/`float`/`Decimal`/`str` depending on the
connector — so "is this numeric" from raw values alone would silently
re-derive a classification Schema Mapping already computed correctly,
violating this codebase's consistent "thread the already-computed signal
forward, don't re-derive" discipline (see
`sql_generation._aggregation_function` trusting `role` rather than
re-inspecting values). The real cost of this choice is `result_alias`
itself: no existing contract carries SQL Generation's real aliasing
(`UNITS` → `UNITS_TOTAL`) forward, so today's caller must populate it by
hand — accepted as a real, logged gap (`LIMITATIONS.md` item 28) rather
than reaching back into an already-shipped upstream contract for this
phase's convenience.

## 2026-07-29 — Anomaly/Outlier Highlighter is fully deterministic, placed between Chart Selection and Grounded Narrative Generation, and its output is both cited and standalone

We chose z-score detection via stdlib `statistics.mean`/`statistics.pstdev`
(population, not sample, stdev — these grouped-by-dimension result sets
are small and treated as the entire population under comparison, not a
sample) with no LLM and no new dependency (confirmed no numpy/scipy/pandas
declared in `packages/agent_runtime/pyproject.toml`) — mirroring
`query_cost_estimator`/`sql_optimization`'s existing deterministic-agent
precedent exactly. We placed it between Chart Selection and Grounded
Narrative Generation (reusing Chart Selection's already-resolved
measure/dimension columns rather than re-implementing that resolution) and
made its `AnomalyDetectionResult` both independently returned AND citable
grounding material for the narrative agent — because the `insight_generated`
lineage event must record "which result values it grounded each claim in,"
and a narrative's anomaly citation must trace back to a real,
independently-auditable finding, only possible if the Highlighter's result
survives as first-class output rather than being merged away into
narrative text only.

## 2026-07-29 — Grounded Narrative Generation's two-layer citation validation mirrors Semantic Retrieval's closed-candidate-list discipline

We required the LLM to return structured `{"narrative": str, "citations":
[{citation_id, row_index, column, cited_value}]}` JSON, then validated
every citation against a closed candidate set built directly from the
real `final_rows`/anomaly data — a citation naming a `(row_index, column)`
that doesn't exist, or a value that doesn't match the real one, is dropped
and recorded as `llm_cited_fabricated_value`, never partially trusted. A
second, independent whole-narrative numeric scan catches any number the
LLM stated without even attempting to cite. We considered trusting the
LLM's narrative at face value with only a prompt instruction ("never
invent a number") and rejected it outright: this project's standing rule
requires a real, verifiable mechanism for any hallucination-risk
LLM output, not a trusted instruction — exactly the discipline
`SemanticRetrievalAgent`'s "closed candidate list, reject anything not in
it" already established for catalog column IDs, applied here to real
result-set cells instead. A real, honestly-scoped blind spot remains and
is logged (`LIMITATIONS.md` item 30): this can only catch wholesale
fabrication, not a real value misattributed to the wrong row/group.

## 2026-07-29 — Follow-up Suggestion is deliberately exempt from the closed-candidate-list discipline

We chose to apply only shape validation (1-3 non-empty suggestions) to
Follow-up Suggestion's output, explicitly NOT the grounding check Grounded
Narrative Generation requires. We considered applying the same discipline
uniformly across both LLM-backed Insight agents for consistency, and
rejected it: a suggested question is a proposal, not a factual claim, and
`data-flow.md`'s own worked example ("Did any single account drive the
Southwest spike?") deliberately introduces "account," a concept outside
the closed candidate list — rejecting that on principle would reject
exactly the useful, exploratory suggestions this agent exists to produce.
Verified live in `tests/integration/insight_pipeline/`: a suggestion
referencing a concept absent from `final_columns` is accepted, not
rejected.

## 2026-07-29 — Documentation staleness is logged, not fixed, this phase

We chose to record the accumulated drift in `docs/architecture/overview.md`,
`data-flow.md`, and two module docstrings (`LIMITATIONS.md` item 32) as a
finding rather than reconciling it as part of Phase 7. We considered
fixing at least Insight's own rows/sections while we were already touching
this domain, and rejected doing even that much: partial reconciliation
(Insight's rows current, every other domain's rows still stale) reads as
more complete than it is, arguably worse than leaving the whole thing
consistently stale. Recommend a dedicated, later phase whose only job is
this reconciliation, covering all ~20 real agents across 4 domains at
once, not addressed piecemeal per feature phase.

## 2026-07-29 — Lineage is a new standalone package, sharing the physical Postgres instance and using the event's own ID as primary key

We chose `packages/lineage` (`navigraph_lineage`) as a new package with its
own Alembic revision chain (`version_table="alembic_version_lineage"`,
distinct from `metadata_catalog`'s own `alembic_version` in the same
database — a real collision we would have hit otherwise, caught and
avoided during design, not live) rather than adding a table to
`metadata_catalog`. Lineage is a high-write, append-only, per-request
audit log with a completely different lifecycle than crawled schema
structure and no FK relationship to it — mirrors the `navigraph_federation`
precedent (split from `connector_sdk` when Trino-routed execution became a
genuinely distinct concern). We used `LineageEvent.event_id` (the real,
already-`uuid4`-derived string every agent generates) directly as the
table's primary key rather than a synthetic surrogate, making idempotent
re-insertion (`INSERT ... ON CONFLICT (event_id) DO NOTHING ...
RETURNING`) a real, DB-enforced property, not an application convention.

**A real bug found while proving this idempotency for real**: the first
version of `record_events` used `result.rowcount` to count newly-inserted
rows, assuming Postgres reports it accurately for a bulk `ON CONFLICT DO
NOTHING` insert. It does not for SQLAlchemy 2.0's "insertmanyvalues"
batching strategy — `tests/integration/lineage_pipeline/` caught a real
`rowcount=-1` for a genuine single-event insert. Fixed by switching to a
`RETURNING event_id` clause and counting the returned rows instead, which
is unconditionally accurate.

## 2026-07-29 — Lineage Recorder records incrementally, one call per upstream agent's real output

We chose to have `LineageRecorderAgent` accept one upstream agent's own
`lineage_events` list per invocation, called immediately after that
agent's real output is produced, rather than a single end-of-request batch
flush. We considered batching (fewer total calls) and rejected it: the
pipeline diagram's own phrasing is "lineage recorded at every stage," and
no real Orchestrator exists yet to accumulate a full request's events
before a single flush — incremental recording is both what the spec
literally describes and the only design that doesn't require inventing a
new accumulation mechanism this phase wasn't scoped to build.

## 2026-07-29 — Evaluation Judge is a real agent, not a bare script function; the harness uses one real LLM client throughout, not per-step canned responses

We built `ops.evaluation_judge` as a real agent (contract, lineage,
confidence, HTTP route) rather than a bare scoring function inside
`run_harness.py` — this codebase already builds even purely deterministic
steps (Chart Selection, Schema Mapping) as real agents specifically so
every meaningful step gets a lineage event and a uniform, independently
testable HTTP surface; scoring a real answer is at least as consequential.
`intent_match` is computed in Python, never asked of the judge model — a
closed-vocabulary equality check has no business being an LLM judgment
call, mirroring this codebase's standing rule.

We chose ONE real, caller-supplied `LLMClient` used uniformly across every
LLM-backed step in `eval/pipeline_chain.py`'s `run_full_pipeline`, rather
than refactoring `tests/integration/insight_pipeline/test_pipeline_chain.py`
to share this same helper (a deviation from the original Phase 8 plan,
which proposed that refactor). We considered it and rejected it: that
test needs fully deterministic, per-step CANNED LLM responses (a fixed
intent, a fixed semantic-retrieval match, a hand-crafted fabricated
citation) to reliably exercise specific mechanics — citation-validation
rejection, z-score correctness — against a schema resolution known in
advance. Routing it through one real `LLMClient` would make it flaky
(real model variability changing which entities/columns resolve) for no
real benefit over the ~150 lines of duplication avoiding it costs. The
harness's own first real run (see `LIMITATIONS.md` item 38) confirms this
was the right call: a real model's real behavior (differing entity
extraction, occasional malformed JSON, genuine resolution misses) is
exactly what the harness exists to observe, and exactly what the
deterministic test must NOT be subject to.

**A real, foundational bug found by this same first real run** (see
`LIMITATIONS.md` item 37): every LLM-backed agent's JSON parsing broke on
the real model's actual output shape (wrapped in a markdown code fence).
Fixed once, centrally, via `navigraph_shared.llm.strip_json_code_fence`,
applied to all 7 affected agents rather than patched ad hoc per call site.

## 2026-07-29 — Golden set: 10 real questions, one YAML file each, CI wiring deferred

We chose 10 real, schema-grounded golden questions (confirmed with the
user) over the README's originally-stated "50+," given each question's
real cost (the full real pipeline plus two real LLM calls per question) —
see `LIMITATIONS.md` item 33. One YAML file per question (not one shared
file) keeps future additions as atomic diffs, matching this repo's
existing per-unit-of-work file granularity. We deferred wiring
`eval/run_harness.py` into CI: `.github/workflows/ci.yml` runs only
`pytest packages/` and has no Anthropic/Snowflake secrets configured
today, and `tests/integration/*` (which this harness's own dependencies
mirror) has never been wired into CI either, for the identical reason.

## 2026-07-29 — Request Orchestrator is a plain Python async function, not a LangGraph graph (reverses Phase 1's decision)

Phase 1's original architecture decision committed to LangGraph for the
Orchestrator domain's Coordinator/Supervisor/Planning graph execution.
We formally reverse that decision here, confirmed with the user via
`AskUserQuestion`: 8 phases and ~22 real agents were built and proven
correct with zero real need for graph-checkpointing or resumability ever
emerging, and `eval/pipeline_chain.py`'s `run_full_pipeline` (built Phase
8) already proved the direct-async-call pattern end-to-end against real
infrastructure. Formalizing that already-proven pattern into the real
Request Orchestrator agent is strictly less risk than introducing
LangGraph for the first time, 8 phases into the project, for a capability
(checkpointed resumability) nothing has yet needed — see `LIMITATIONS.md`
item 39 for the one concrete capability given up by this choice (no
mid-pipeline crash recovery) and the condition under which it would be
worth revisiting. `docs/architecture/agent-contract.md`'s "Dual invocation"
section and `docs/architecture/overview.md`'s Orchestrator-domain prose are
both corrected to describe a direct, in-process call rather than a
LangGraph node.

## 2026-07-29 — Session state lives in Redis, keyed and TTL'd like the query-result cache, not a new Postgres table

We chose Redis over a new Postgres table for session/conversation-history
storage, reusing `query.caching`'s exact `CacheClientProtocol` DI pattern
and `navigraph:v1:{tenant_id}:...` key-scheme convention (just a
`:session:{session_id}` namespace instead of `:query_cache:...`). Session
state is short-lived and naturally TTL-bounded (a stale, abandoned
conversation should simply expire) — the opposite lifecycle from
`navigraph_catalog`/`navigraph_lineage`'s permanent stores, which would
need a real eviction job Redis gives for free. We considered a new
Postgres table (consistent with this project's other durable stores) and
rejected it: it would need its own migration, its own eviction job, and
solves a problem Redis already solves for free, for data that was never
meant to be permanent. `session_id` is deliberately NOT added to
`RequestContext` — that shared, `extra="forbid"` contract is depended on
by all ~25 agents; session_id only concerns the Orchestrator domain, so it
travels in `RequestOrchestratorPayload`/`SessionContextManagerPayload`
instead, minted fresh (`f"sess_{uuid.uuid4().hex}"`) when the caller omits
one.

## 2026-07-29 — `eval/pipeline_chain.py::run_full_pipeline` is retired; the eval harness now calls the real Request Orchestrator directly

`run_full_pipeline` (built Phase 8) hand-threaded the same ~19-agent
sequence the real Request Orchestrator agent now implements for real,
production use. Keeping both alive would have meant every future contract
change to any of those 19 agents needing to be applied twice, a real,
already-demonstrated drift risk in this codebase (see `LIMITATIONS.md`
item 32's documentation-staleness finding, and item 35's specific
already-shipped-agents-still-marked-`DESIGNED` case). We deleted
`eval/pipeline_chain.py` outright rather than keeping it as a deprecated
re-export — confirmed safe via `grep` (only `eval/run_harness.py` imported
it) before deleting; this is irreversible, a real, deliberate tradeoff
named explicitly rather than glossed over. `eval/run_harness.py` was
rewritten to construct one `RequestOrchestratorAgent` (reused across every
golden question, matching `main.py`'s own single-shared-instance
convention) and call it per question, mapping `outcome == "needs_clarification"`
onto the report shape with a distinct `failure_stage="orchestrator.needs_clarification"`
value — never conflated with a genuine `outcome == "failed"`.
`tests/integration/insight_pipeline/test_pipeline_chain.py` was never
refactored to share this helper in the first place (see the prior
Phase 8 decision entry above on why), so no other file needed updating.

## 2026-07-29 — Multi-turn Clarification Coordinator triggers on exactly one narrow, already-observed condition

We scoped the Clarification Coordinator's trigger condition to exactly
`schema_mapping_result.tables == []` — a complete resolution failure —
rather than a general ambiguity or low-confidence detector. This is the
exact real failure shape Phase 8's harness hit twice (`gq_007`, `gq_010`,
see `LIMITATIONS.md` item 38), so it is a targeted fix to an observed,
concrete gap rather than a speculative general-purpose mechanism. A
partial resolution (some `unmapped_terms` but at least one real table)
still proceeds to attempt an answer — see `LIMITATIONS.md` item 41 for
the real tradeoff this narrow scoping accepts and what would justify
broadening it.

## 2026-07-29 — A real, live-discovered join-inference gap: a fourth curated `RelationshipConcept` was added, not a new inference mechanism

Phase 9's first real HTTP smoke test of the newly-wired Request
Orchestrator ("What is the total transaction volume by market?") surfaced
a genuine bug: Schema Mapping's `_build_joins` derives joins *only* from
Ontology's curated `RelationshipConcept` matches (see `LIMITATIONS.md`
item 15's original, already-accepted low-recall design), and no curated
concept linked `TRANSACTIONS` and `MARKETS` — so a real, resolved
two-table query produced zero joins, and the generated SQL silently
cross-joined one ungrounded grand total against every market name. We
fixed this by adding a fourth entry, `"Transaction happens in Market"`
(`realizing_table="TRANSACTIONS"`, `subject_key_column`/
`object_key_column="MARKETID"` — the real, literal shared foreign-key
column), to `navigraph_kg.ontology.RELATIONSHIP_CONCEPTS`, rather than
building a new automatic join-inference mechanism (e.g. matching shared
column names across resolved tables). We considered the general
mechanism and rejected it for now: the curated-concept approach is the
existing, deliberate Phase 3 design (real, hand-verified relationships
only, never auto-derived), and expanding it with a fourth real entry
directly addresses the demonstrated gap without introducing a new
heuristic with its own new failure modes (e.g. false-positive joins on
two tables that coincidentally share a generically-named column). See
`LIMITATIONS.md` item 15 for the full root-cause, fix, and verification
detail, and item 44 for the real, live proof this fix produced (`gq_007`
now answers correctly end-to-end where it previously hard-failed).
