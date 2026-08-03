# NaviGraph — Comprehensive Build Specification

**Purpose of this document**: a complete, phase-by-phase specification for
building NaviGraph — a production-grade, multi-tenant conversational BI
platform — from an empty repository to a live, deployed, adversarially-
tested product. This is written as *instructions to follow*, not a
historical narrative (that's `BUILD_LOG.md`'s job). Every phase below was
actually executed, in this order, to build the real, live system described
in `docs/product/prd.md` and `docs/architecture/system-architecture.md`.
Where the real build deviated from an original plan, or where a real bug
was found that changes what you should build differently the second time,
this document says so explicitly, in a **"Real deviation" / "Lesson"**
callout — those callouts are the most valuable content here: they are
what separates a paper plan from an account of what actually happens when
you build this.

**How to use this document**: read the Working Method section first — it
is the reusable discipline that made every phase below verifiable and
low-risk, independent of NaviGraph's specific domain. Then either follow
the phases in order to rebuild NaviGraph from scratch, or use individual
phase sections as a reference for how a specific subsystem (e.g. the
Guardrail domain, the canary CD pipeline) was actually designed and why.

---

## Part 0 — Working Method (the reusable discipline)

This is what to carry into any project like this one, independent of the
specific product being built.

### The three living process logs

Start these on day one of Phase 1, not retroactively:

- **`LIMITATIONS.md`** — every known gap and every real bug found, each
  as its own numbered, sequential entry (never renumbered or reordered).
  A bug entry states: what was found (with the real symptom/error
  string), the root cause (with real evidence — log lines, exact
  reproduction steps), the resolution, and "what full version requires"
  if the fix is partial. An entry is marked `RESOLVED` in its own title
  once fixed — never delete or silently rewrite a closed entry. Every
  other document in the project should link to `LIMITATIONS.md` by item
  number rather than restate a gap's detail.
- **`DECISIONS.md`** — every real architecture/implementation decision,
  as a dated (`## YYYY-MM-DD — <title>`) entry with the actual
  reasoning and rejected alternatives. Written *at the time the decision
  is made*, not reconstructed later from memory.
- **`BUILD_LOG.md`** — a phase-by-phase narrative of what was actually
  built and verified, one entry per phase, including real bugs found and
  fixed inline (cross-referencing the `LIMITATIONS.md` item number).

**Why this matters more than it looks like it should**: by the time this
project reached Phase 10b, `LIMITATIONS.md` had 80 numbered items and
`DECISIONS.md` had over 50 dated entries — and every new agent, every new
doc, every debugging session in this spec was able to move faster by
searching these logs first rather than re-deriving context. This
compounds: the discipline is cheap on day one and extremely valuable by
day thirty.

### Plan → build → verify → log, per phase, never skip a step

For every phase:

1. **Write a phase plan** before writing code — context (what's true
   right now), key decisions (with real alternatives considered), a file
   list, and a verification section. Get it reviewed/approved before
   starting (a human gate, or an explicit self-check if operating
   autonomously) — this is cheap insurance against wasted implementation
   effort on a misunderstood requirement.
2. **Build exactly what the phase plan describes** — resist scope creep
   into "while I'm here" additions; log a deliberate scope *addition* if
   one is genuinely warranted (e.g. this project's Phase 1.5), but name
   it as a scope addition explicitly rather than silently fold it into
   the phase.
3. **Verify with real output, never a summary claiming success.** A
   phase is not done because the agent says "tests should pass" — it is
   done when a real command was actually run and its real output is
   shown. This project's own convention, repeated at the end of every
   phase's verification section: *"must show real output, not a summary
   claiming success."*
4. **Update all three logs and commit** before moving to the next phase.

### Real bugs get found by testing the real path, not by code review alone

The single most repeated pattern in this project's entire `LIMITATIONS.md`
is: **a bug was invisible until the actual real path was exercised**, and
became obvious the moment it was. Concretely, in this project:

- `kind`'s local network simulator (`kindnet`) never enforces
  `NetworkPolicy` at all — a real gateway↔agent-runtime egress gap was
  completely invisible in every local `kind` validation and only
  surfaced against the real, NetworkPolicy-enforcing Azure CNI.
- Every prior test of `/ask` called the Request Orchestrator directly
  in-process or via a pod-local `kubectl exec` — the gateway's own HTTP
  timeout and the ingress's `proxy-read-timeout` were both too short for
  real latency, and this was invisible until the very first real browser
  call through the actual public internet-facing path.
- A Semantic Retrieval token-budget bug was invisible in every existing
  test because none of them exercised a question producing 5 real
  unresolved terms against the full, real 114-column catalog at once —
  smaller test fixtures never hit the real token ceiling.

**Lesson for the next build**: budget time specifically for *"exercise
the actual real production path end-to-end, for the first time, as a
real user would"* — not as a formality after everything already "works,"
but as a distinct activity expected to surface real, previously-invisible
bugs. This project found 3 significant, previously-undetected bugs (item
75's timeout gap, item 78's env-var gap, item 79's token-budget gap) in a
single afternoon of exactly this activity, after 10 full build phases had
already been marked "complete and verified."

### When you find a real bug: fix live first, then commit to source

The established, repeated pattern in this project: when a real production
bug is found and the fix is understood, apply the fix directly to the
live environment first (`kubectl set env`, `kubectl apply -f -`,
`kubectl annotate`, etc.) to unblock real usage immediately, confirm it
worked with a real re-test, *then* commit the same fix to source so every
future deploy inherits it. Never leave a confirmed real fix un-committed
"for later."

### Root-cause with real evidence before proposing a fix

Every real bug in this project's `LIMITATIONS.md` was root-caused with
direct evidence — an exact error string, a `tokens_output` value that
matched a `max_tokens` cap exactly, a cross-referenced log line at a
matching `trace_id` across two different services — before a fix was
written. When two plausible explanations exist, test both directly
(e.g. curling a service both via the public ingress hostname and
directly to its ClusterIP, to isolate "ingress routing problem" from
"network policy problem") rather than guessing.

### Ask the human at real judgment-call forks, not everywhere

This project used explicit judgment-call questions at real forks where
the answer was genuinely the product owner's call, not something
derivable from the code or spec — see Appendix A for the complete list.
Everywhere else, proceed on reasoned defaults and document the reasoning
in `DECISIONS.md`. Don't over-ask; don't under-ask.

---

## Part 1 — Phase 0: Prerequisites

Resolve these before writing any code. Each is a real, human judgment
call this project made explicitly (see Appendix A):

| Decision | What NaviGraph chose | Why |
|---|---|---|
| Local dev inner loop | docker-compose as the everyday loop; a local `kind` cluster as a secondary, periodic k8s-shape check | Fast iteration locally; `kind` catches manifest-shape bugs before they reach real cloud cost |
| Target cloud | Azure | Terraform written as a validated skeleton from Phase 1, never applied until the human explicitly authorizes real spend |
| Identity provider | Azure AD (Entra ID), real OAuth/OIDC even in local dev via a dev app registration | Avoids ever building/testing against a fake auth model that diverges from production |
| Compliance regime | SOC 2 Type II | Drives audit-logging, change management (CODEOWNERS, required CI checks), and access-control posture from day one, not bolted on later |
| Data source scope | One real connector (Snowflake) built now; connector SDK interface stays source-agnostic; Postgres/REST reference connectors explicitly deferred | Narrows scope to what's real rather than building fixtures for sources that don't exist yet — log the deferral, don't silently narrow the interface too |
| Repo location | A new, empty local folder, git-initialized fresh | — |

**Real deviation to plan for**: this project's target dataset and the 50
real business questions it would answer did not exist until Phase 2
completed (real Snowflake credentials provided, real schema crawled).
Phase 3's knowledge-graph design was deliberately sequenced *after* that
real data existed, per the explicit rule: never invent a generic ontology
before real data and real target questions exist to ground it. If your
project's target domain isn't decided at Phase 0, plan for a Phase 0.5
gate exactly like this one before any ontology/ knowledge-graph design
work.

---

## Part 2 — Phase 1: Repo Scaffold, CI, Terraform Skeleton, Local Dev Stack

**Objective**: a complete, empty-but-real project skeleton — every
service has a real Dockerfile and a real (thin) test; nothing is
hand-waved as "to be filled in later" except the ~24 agents that don't
exist yet.

### Build

- **Root docs/process**: `README.md`, `LIMITATIONS.md`, `DECISIONS.md`,
  `BUILD_LOG.md`, `CONTRIBUTING.md`, `SECURITY.md`, `CODEOWNERS`,
  `.gitignore`, `.gitattributes`, `.editorconfig`, `LICENSE`.
- **`.github/workflows/`**: `ci.yml` (lint/typecheck/unit tests),
  `security-scan.yml` (`pip-audit`/`npm audit`/`semgrep` — SOC 2
  change-management evidence), `terraform-plan.yml` (`fmt`+`validate`
  always; `plan` only if cloud credentials are present — never `apply`),
  `adversarial-tests.yml` (runs the security test suite once it exists —
  a required check from the start, even before there's anything to
  test).
- **`docs/`**: architecture overview, `agent-contract.md` (the formal
  spec — see Part 13 below), a data-flow narrative, a local-dev smoke
  test runbook, one ADR.
- **`infra/`**: `docker-compose.yml` with every real backing service
  (Postgres, Neo4j single-instance, Redis, OTel collector, Prometheus,
  Grafana, OPA with a placeholder allow-all policy, Trino
  coordinator+worker with zero catalogs registered yet), `.env.example`,
  init SQL/config for each, `k8s/kind-config.yaml` for the local
  secondary k8s check.
- **`terraform/`**: one environment (`environments/dev`) + one skeleton
  module per real resource type your target cloud will eventually need
  (for Azure: resource-group, aks, acr, key-vault,
  postgres-flexible-server, networking, entra-app-registration) — every
  module must `terraform validate` cleanly with commented-out resource
  bodies. State in the README, explicitly: *this is never applied
  without a separate, explicit go-ahead.*
- **`packages/`** (or your language's equivalent workspace layout):
  `shared` (config loader + contract placeholders), `gateway` (thin
  HTTP service, `/healthz`/`/readyz` only), `agent_runtime` (process
  entrypoint, `/healthz` only — **no agents yet**). Each package: its
  own dependency manifest, its own Dockerfile, its own real (if thin)
  test.
- **`web/`**: a minimal frontend scaffold, one placeholder page, one
  smoke test.
- **`tools/scripts/`**: a bootstrap script (env copy + compose up), a
  smoke-test script (curls every `/healthz`), and an agent-scaffolding
  template directory referenced by `agent-contract.md`.
- **`eval/`, `tests/integration/`, `tests/security/`**: placeholder
  READMEs only. State explicitly in `tests/security/README.md`: *no
  security-relevant component exists yet to adversarially test; the
  first such test is a required gate before any auth-adjacent work is
  ever marked done* — this sentence is what makes Phase 6 (Guardrail)
  non-negotiable later.

### Verify

1. Bring up the full compose stack; every service reaches healthy.
2. Run the smoke-test script; every real dependency check passes.
3. CI's lint/typecheck/unit jobs run locally and pass.
4. `terraform validate` passes on every module (no credentials needed);
   `terraform plan` is explicitly skipped and logged as expected if no
   cloud credentials exist yet.
5. Update all three logs; commit.

---

## Part 3 — Phase 1.5: One Real Reference Agent (a deliberate scope addition)

**Objective**: prove the agent contract pattern (Part 13) works with
real, running code — not just asserted in a spec document — before
committing to build ~24 more agents on top of an unproven abstraction.

### Build

Pick the simplest, first-in-every-pipeline agent (in this project:
**Intent Understanding** — classify a question's intent). Build it as a
fully real vertical slice: real LLM call, real unit test (mocked LLM),
one real integration test against the actual LLM provider (marked so it
can be skipped without a real API key), real lineage-event emission,
real observability spans. Wire a minimal single-agent "orchestrator" so
the gateway's `POST /ask` can reach it end to end — the response can
legitimately just say "here's the resolved intent," not a full answer.

**Name this explicitly as a deliberate scope addition**, not a silent
absorption into "Phase 1" — in practice this project folded it into
Phase 1's own `BUILD_LOG.md`/`LIMITATIONS.md` entries rather than giving
it a separate top-level heading, which is an acceptable variant as long
as the addition is called out inline by name; what matters is that a
later reader can tell it was a deliberate choice, not that it necessarily
gets its own heading.

### Verify

A real `POST /ask` against the gateway returns a real, non-empty
`lineage_events` array from the real agent. This is the proof the
pattern works.

---

## Part 4 — Phase 2: Metadata Catalog + Connector SDK

**Objective**: a documented, source-agnostic connector plugin interface,
and a structural (schema-only, no business meaning yet) metadata catalog.

### Key decisions

- **Decouple the connector SDK from the catalog's storage model.** The
  SDK returns plain data-transfer descriptors (schema/table/column shape,
  connection-test result, query result, capabilities) with zero
  dependency on the catalog's storage layer. This is what lets you
  pressure-test the abstraction with a second connector later without
  touching the catalog schema.
- **The one real connector's driver import is lazy** (inside the
  connector module) — importing the SDK's base/registry should never
  require that specific driver to be installed, so unit tests for
  unrelated connectors/registry logic never need it either.
- **Metadata catalog stores raw schema structure only** — data sources,
  schemas, tables, columns, each scoped by a tenant identifier on the
  root `DataSource` entity (tenant scoping structural from day one, not
  retrofitted). No business glossary, no ontology mapping yet — that's
  deliberately later-phase territory; mixing it in now blurs a boundary
  later phases depend on.
- **No secrets in the catalog DB.** A `DataSource`'s connection
  reference is an opaque pointer (e.g. an env-var-prefix string), never
  raw credentials. Real secrets-manager integration is a cloud-phase
  concern — log the gap now rather than build a fake local secrets
  store.
- **Test-tier split**: fast unit tests (registry logic, crawler-vs-a-
  fake-connector, model validation) need no real database or external
  source; a real-infra integration test runs schema migrations against
  the live local database; a `@pytest.mark.<source>_integration`-style
  test is skipped unless real credentials for the actual external source
  are present in the environment.

### Build

Two new library packages: a connector SDK (abstract connector interface
+ a registry keyed by source-type string, raising a clear error for an
unregistered type; the one real connector implementation) and a metadata
catalog (models for DataSource/Schema/Table/Column, a migration tool, a
crawler that takes any connector and upserts its introspection result).

### Verify

1. Fresh install of both new packages alongside the existing ones.
2. Unit tests pass with no real credentials.
3. Migrations run against the real local database; show the resulting
   tables.
4. Once real source credentials are provided (into a local,
   never-committed env file — never pasted back by the agent building
   this): run the source-integration test for real, then run the
   crawler once against the actual real source and show real rows
   landing in the catalog.
5. Update all three logs; commit.

---

## Part 5 — Phase 3: Knowledge Graph / Ontology

**Objective**: design and build the business-concept/knowledge-graph
layer — deliberately *after* real data and real target business
questions exist, never before.

### Key decisions

- **Two-tier graph, not a flat one.** Tier 1: bounded-cardinality
  reference/dimension nodes grounded in the real schema (in this
  project: Asset, Market, Exchange, Sector, Industry, Channel,
  CustomerType, RiskLevel, InvestmentCapacityBand). Tier 2: a
  business-concept mapping layer (a `BusinessConcept` node sourced from a
  real business glossary if one exists; thin `Table`/`Column` proxies
  into the metadata catalog by ID; hand-curated `RelationshipConcept`
  metadata describing fact-level relationships without ever
  materializing per-row edges).
- **Exclude high-cardinality, high-write facts from the graph entirely**
  (in this project: customers and transactions stay in the SQL
  warehouse). The graph supplies reference-data validation and
  business-term resolution; every real question still gets answered by
  generated SQL against the warehouse, never a duplicated data copy in
  the graph.
- **Ingest any real business glossary into the metadata catalog first**,
  as its own model + migration + API, keeping the catalog as the single
  source of truth for everything schema-related; the knowledge graph
  reads the glossary from there, never from the source warehouse
  directly. The knowledge graph talks to the source warehouse directly
  for exactly one thing: crawling live reference data (distinct values
  of the Tier-1 node types) — that's data, not structure, and the
  catalog has no row-level read API by design.
- **Let real data decide modeling questions you can't decide on paper.**
  Before finalizing the node/edge design, run live, read-only queries
  against the real schema to answer: is a candidate 1-to-many
  relationship genuinely 1-to-many? Is a candidate hierarchy actually
  clean, or does real data violate it? What fraction of rows actually
  have a given optional attribute at all? This project found a genuine
  1-to-many relationship (justifying its own graph node) and a real,
  confirmed hierarchy violation (justifying "independent siblings," not
  a strict tree) this way — decisions that would have been guesses
  without the live queries.
- **No auto-fabricated business concepts.** A column with no real
  glossary entry gets no synthesized concept — "no business concept
  mapped yet" is a legitimate, surfaced answer, never papered over.
- **Soft staleness, not hard deletes** on re-ingestion (an `active`/
  `last_synced_at` flag), so in-flight references never break silently
  mid-query.

### Verify

1. New glossary migration applied; show the real table.
2. Real glossary crawl against the real source; show real rows landing
   in the catalog.
3. Real knowledge-graph ingestion pipeline run against the live graph
   database + live source reference data; show real node/relationship
   counts; re-run and show counts unchanged (idempotency proof).
4. Answer at least 3 real target business questions using the graph +
   catalog directly (a business-term resolution, a reference-data
   validation lookup, a relationship-grouping query) — show real query
   results.
5. Update all three logs; commit.

---

## Part 6 — Phase 4: Understanding Domain (remaining agents)

**Objective**: turn a raw natural-language question into a fully
resolved, join-validated schema mapping.

### Key decisions

- **Fix the real pipeline order up front**: a conversational-rewrite
  agent must run *before* intent classification (it resolves a follow-up
  into a standalone question; intent classification must see the
  resolved question, not a fragment). A pure catalog-metadata lookup
  agent is independent of question text and can run in parallel with
  the conversational/intent agents.
- **Only LLM-back the agents that genuinely need judgment.** Every
  agent that's a deterministic lookup/assembly over already-structured
  data (catalog reads, schema assembly) should have zero LLM call, zero
  hallucination risk, zero API cost. Reserve LLM calls for exactly the
  steps needing real natural-language judgment (rewriting a follow-up,
  classifying intent, matching an ambiguous business term).
- **Two-tier term resolution, not one fuzzy pass.** Resolve business
  terms first via a free, zero-hallucination structured match against
  the knowledge graph; only what that pass can't resolve goes to an LLM
  call — and that LLM call must be **hard-constrained to a closed
  candidate list** built from the real catalog inventory (never
  free-form): the LLM selects an existing item or says "no match,"
  never invents one. Validate every returned ID against the candidate
  set before trusting it; an invalid ID is a recoverable error, never a
  crash.
- **Keep the conversational-rewrite agent stateless this phase** — it
  operates on conversation history handed to it directly, never
  fetches/stores anything itself. Session persistence is explicitly a
  later phase's job (this project's Orchestrator-domain Session/Context
  Manager); don't stub a fake in-memory store now that looks
  production-ready without being durable or tenant-isolated.
- **One agent should be the sole assembly point** merging every upstream
  term-resolution result into one deduplicated, join-validated
  structure with a role assigned per column (measure/dimension/filter) —
  this is the shape every downstream query-generation agent consumes.
- **Log heuristics as heuristics.** A role-assignment rule based on data
  type + intent, or a relationship-matching rule that accepts lower
  recall in v1, is a legitimate, shippable v1 choice — but log it in
  `LIMITATIONS.md` explicitly as a heuristic, not a guarantee, so a
  later phase knows exactly what it can and can't rely on.

### Verify

1. Unit tests across all agents in this domain.
2. A real integration test chaining every agent in this domain against
   live infrastructure and the real catalog data, using one real worked
   example question, asserting the correct final resolved schema
   mapping.
3. If a real LLM key is available (optional): run the LLM-integration
   tests for real, and re-run the chain with a real follow-up question to
   prove conversational rewriting actually works end to end.
4. Update all three logs; commit.

---

## Part 7 — Phase 5: Query Domain + Federation

**Objective**: turn a resolved schema mapping into real, safely-bounded,
executed SQL — the first phase that actually runs generated queries
against a live data source.

### Key decisions

- **Resolve the "execute real SQL now, or wait for the policy-engine
  phase" fork explicitly with the product owner.** This project chose
  to execute now, with real structural compensating controls, precisely
  *because* the policy-engine domain is explicitly a later phase per
  the product's own domain ordering — and named those controls as
  compensating, temporary, and not a substitute for that later phase.
- **Verify the execution role/credential is genuinely least-privilege
  before running anything real against it** — a live, read-only grants
  check, not an assumption.
- **Real compensating controls, concretely**: a hard SELECT-only
  allowlist enforced by real statement parsing (not a regex) rejecting
  any DDL/DML/session-control token, even though nothing upstream
  should ever produce one; bind-parameterized values only, never
  string-interpolated (this closes injection risk independent of
  whatever policy layer comes later); a hard row-cap and timeout
  re-verified at the execution-plan level; every executed statement
  carries an embedded trace/tenant audit comment for post-hoc query-log
  auditing.
- **Default the execution route to the simplest direct connection, not
  a federation engine**, even if you've stood up a federation engine
  (e.g. Trino) for real and registered the real catalog in it — routing
  production traffic through a general-purpose distributed SQL engine's
  unaudited access-control surface during the exact window there's no
  policy gate to catch a mistake is the wrong tradeoff. Build and test
  the federation route fully; just don't default to it until a second
  real data source creates genuine federation need, or the federation
  engine's own access control gets independently reviewed.
- **Keep SQL generation dialect-neutral** (no catalog/engine-specific
  prefixing) so the execution-route choice never leaks upstream;
  catalog-prefixing (if the federation route needs it) happens only at
  the execution-route boundary.
- **Give the federation package its own boundary**, separate from the
  connector SDK — a federation engine isn't itself a tenant's data
  source; there's no "federation engine data" independent of what it
  federates, so it doesn't belong in the connector registry.
- **Cache post-optimization results only, with a tenant-prefixed
  (not just tenant-hashed), versioned cache key** — a literal tenant
  segment in the key means a hash collision still can't cross a tenant
  boundary. Reserve an unused policy-version field in the key now, so a
  future per-role/per-policy cache variation is "populate an existing
  field," not a redesign.

### Build

Six agents: a data-source-discovery agent (resolve which real source
owns each mapped table + a live connectivity check), a SQL-generation
agent (deterministic builder for the structural SQL skeleton; one small,
closed-candidate LLM call only for resolving natural-language predicate
values like relative dates — skipped entirely when not needed), a
SQL-optimization agent (rule-based: inject a LIMIT, add the audit
comment), an execution-planning agent (the hard safety gate), a
data-federation agent (the one agent that actually executes), and a
caching agent.

### Verify

1. Unit tests across all six agents.
2. Register the real federation-engine catalog; confirm it can see the
   real schema.
3. A real integration test: real generated SQL, real execution via the
   direct-connector route against the live real source, real rows
   returned, a real cache hit on re-run, and a real rejection of a
   deliberately malicious statement (paste the actual rejection, don't
   just assert it in a test).
4. Rebuild/restart the live agent-runtime process with the new agents
   wired in; confirm via a real call that at least one responds
   correctly.
5. Update all three logs; commit.

---

## Part 8 — Phase 6: Guardrail Domain + Real Policy Engine

**Objective**: close the compensating-controls gap Phase 5 explicitly
logged as temporary — real RBAC/ABAC authorization, schema-constraint
validation, cost/row-limit enforcement, and PII protection, all before
any query executes.

### Key decisions

- **Placement follows data availability, not a fixed table position.**
  Insert each guardrail agent at the earliest point in the pipeline
  where the specific fields it needs actually exist as a contract shape
  — for example, schema-constraint/authorization/PII checks can run
  right after SQL generation (which is the only upstream shape carrying
  referenced-table/column detail), while a cost/row-limit check that
  needs an *optimized* statement's row-count estimate must wait until
  after the optimization step.
- **Split the policy engine and PII enforcement into two independent
  layers, not one.** Route RBAC/tenant-ABAC through a real, general
  policy-engine service (e.g. OPA) — keep its policy language simple
  and independently auditable. Handle column-level PII enforcement as
  plain application code in its own dedicated agent, querying the
  catalog's PII tags directly — don't route data-classification facts
  through the policy engine unless it has a live way to receive them.
- **Choose fail-closed vs. fail-open per-agent deliberately, and state
  the reasoning.** The policy-authorization agent must fail closed if
  the policy engine is unreachable (an unreachable policy engine
  silently becoming an implicit allow would disable RBAC entirely). A
  caching agent, by contrast, can reasonably fail open (a cache miss
  just costs a real re-execution, not a security hole). Don't apply one
  blanket rule everywhere — reason about the actual consequence of each
  agent's own failure mode.
- **Give the policy-engine client the same real-vs-fake-double pattern**
  every other external client in the codebase already uses (a real HTTP
  implementation + a no-network test double), so every guardrail agent's
  unit tests need zero real policy-engine instance.
- **Put a PII flag on every column, not on the optional business-
  glossary entry** — PII sensitivity must apply to every column
  unconditionally, and most real columns don't have a glossary entry at
  all. Before writing this migration, run a live, read-only discovery
  query against the real catalog to find real candidate PII column
  names — never invent them — then backfill via a small, idempotent,
  human-run script, not an automatic classifier.
- **Cost/row-limit policy can be a plain, hardcoded per-role mapping in
  code for v1** — it doesn't need the policy engine's deny-by-default
  semantics. State plainly that the specific numbers are placeholders
  pending real business confirmation, not a final policy.
- **Write the adversarial test suite as a required gate, not an
  afterthought.** Minimum coverage: tenant isolation (a request claiming
  one tenant but authenticated as another must be denied), fail-closed
  behavior on missing/invalid roles *and* on the policy engine being
  unreachable, a parametrized sweep of adversarial policy inputs
  (malformed tenant IDs, empty roles, a self-declared privileged role
  with no backing claim — flag the last one explicitly as the shape of
  whatever identity-verification gap your project still has open), and
  PII-denial-vs-clearance for both an unauthorized and an authorized
  role.

### Verify

1. Live discovery query confirming real PII-candidate column names
   before any backfill.
2. Migration applied; show the real flag column and the real backfilled
   rows.
3. Unit tests across all four new agents.
4. A real integration test chaining Understanding → Query →
   all four guardrail agents → the rest of Query, for a real worked
   question and for one deliberately unauthorized statement.
5. The real adversarial test suite run against the real, non-allow-all
   policy — show every case's actual denial reason, not just a passing
   assertion.
6. Rebuild/restart the live agent-runtime; confirm a real HTTP call to
   the policy-authorization agent reaches the real policy engine.
7. Update all three logs; commit.

---

## Part 9 — Phase 7: Insight Domain

**Objective**: turn a real, executed result set into a chart, a grounded
narrative, detected anomalies, and next-step suggestions.

### Key decisions

- **Thread already-computed signals forward; never re-derive them from
  raw values.** If an upstream agent already computed a column's
  semantic role (measure/dimension), a downstream chart-selection agent
  should consume that role directly, not re-infer it by inspecting raw
  cell values (which can arrive in inconsistent runtime types depending
  on the driver). Where a real contract gap exists (an upstream agent's
  own column-aliasing convention isn't threaded through to a downstream
  contract yet, because no real orchestrator exists yet to do it), name
  the gap explicitly in `LIMITATIONS.md` rather than silently working
  around it by reaching back into an already-shipped upstream agent's
  contract.
- **Keep purely deterministic insight steps free of any LLM call.** A
  chart-type selector and a statistical anomaly detector (e.g. a
  z-score check using only the standard numeric library already
  available — no new dependency) need no LLM and should have none.
- **Give the anomaly-detection step its own real, named place in the
  pipeline** — between chart selection and narrative generation — and
  make its output *both* consumed by the narrative agent as grounding
  material *and* returned standalone, never merged away into narrative
  text only, so an anomaly claim is independently auditable.
- **Anti-hallucination for the narrative-generation agent must be a
  real, structural mechanism, not a prompt instruction alone.** The LLM
  returns narrative text plus a list of citations, each naming exactly
  which real result cell it's grounded in; validate every citation
  against the real result set after the fact — a citation that doesn't
  match a real cell is dropped, not trusted, and recorded as a
  recoverable error. Layer a second, defensive scan of the narrative
  text for any number the model never bothered to cite at all. State
  plainly, as a real logged limitation, what this mechanism does *not*
  catch (e.g. a real value correctly present in the result set but
  attributed to the wrong row) — a real, honestly-scoped blind spot,
  not a false claim of complete grounding.
- **A follow-up-question-suggestion agent is exempt from the closed-
  candidate-list discipline** used elsewhere — a suggested question is
  a proposal, not a factual claim, and applying strict grounding here
  would reject exactly the useful, exploratory suggestions the agent
  exists to produce. Apply shape validation only (a bounded number of
  non-empty suggestions).
- **Give every LLM-backed insight agent a deterministic short-circuit
  for the zero-result case** (no LLM call at all when there's nothing
  to explain) — there's no other legitimate "not needed" case for these
  agents, unlike upstream agents' empty-candidate short-circuits.

### Verify

1. Unit tests across all four new agents.
2. A real integration test chaining the full pipeline (Understanding →
   Query → all Guardrail gates → real execution this time, not stopped
   short) into all four Insight agents, for a real worked question —
   asserting a real chart spec, a real independently-recomputed anomaly
   check compared against the agent's own output, a real citation-
   acceptance case *and* a real citation-rejection case (one
   deliberately fabricated citation), and real follow-up suggestions.
3. Rebuild/restart the live agent-runtime; confirm a real HTTP call to
   at least one new agent responds correctly.
4. Update all three logs; commit.

---

## Part 10 — Phase 8: Lineage Recording + Evaluation Harness

**Objective**: a real, persisted, queryable audit trail for every
request, and a real, quantitative way to measure whether the whole
pipeline actually produces correct answers.

### Key decisions

- **Give the lineage store its own package and its own migration
  chain**, reusing the same physical database instance as the metadata
  catalog (tenant isolation in this codebase is already row-level
  everywhere, never database-level, so a new database service isn't
  warranted) but with its own separate migration-tracking table to
  avoid colliding with the catalog's.
- **Use the lineage event's own natural identifier as the real primary
  key**, not a synthetic surrogate — this makes idempotent re-recording
  a real, database-enforced property (an upsert-or-ignore on conflict),
  not just an application-level convention, and it's directly,
  concretely testable.
- **Record lineage incrementally, one call per upstream agent's own
  output**, not a single end-of-request batch flush — matches the
  product's own "lineage recorded at every stage" narrative.
- **Build the evaluation-judge as a real agent, not a bare script
  function** — every other meaningful step in this codebase is a real
  agent with a lineage event, a confidence score, and a uniform HTTP
  surface; scoring a real answer is at least as consequential and
  deserves the same treatment. Anything mechanically checkable (e.g.
  comparing an actual classified intent against an expected one) should
  be a plain equality check in code — never delegate a closed-vocabulary
  comparison to the judge model.
- **Extract the full pipeline-chaining logic into one real, shared,
  importable module** used by both the integration test suite and the
  evaluation harness, rather than duplicating the chain-calling logic in
  two places — every future pipeline change (a new agent inserted, a
  field renamed) should need exactly one edit, not two kept manually in
  sync.
- **Keep the evaluation harness deliberately out of the standard CI
  gate** if it needs real, costed API calls the CI environment has no
  secrets for — state this as a deliberate deferral in its own README,
  not an oversight, and provide a `--compare-to` regression-comparison
  flag so it can still be run deliberately and compared against a prior
  baseline.
- **Set the real golden-question-set size to what's actually achievable
  given each question's real cost** (this project started at a
  50-question aspiration and confirmed, with the product owner, a
  10-question real set instead, once it became clear every question
  round-trips the entire real pipeline plus multiple real LLM calls) —
  state the real, current size plainly rather than silently keep an
  unmet original target in a README.

### Verify

1. Unit tests across the new package and the two new agents.
2. Migration applied against live infrastructure; show the real new
   table and its independent migration-tracking table.
3. A real integration test proving a full real trace assembles
   correctly and idempotently (re-recording the same events is a real,
   asserted no-op).
4. A real HTTP round-trip: record an event, then read back the full
   trace for its real trace ID.
5. A real run of the evaluation harness against the live stack and the
   real golden question set — show real per-question scores and an
   aggregate summary.
6. Update all three logs; commit.

---

## Part 11 — Phase 9: Orchestrator Domain

**Objective**: replace every hand-threaded pipeline chain (used until
now only by tests and the eval harness) with one real, callable
orchestrator agent — the actual, single entry point real callers use.

### Key decisions

- **Revisit an early orchestration-framework decision honestly, based on
  real accumulated evidence, rather than defaulting to sunk-cost
  consistency.** This project's original Phase-0 decision committed to
  a graph-orchestration framework for exactly this reason; by Phase 9,
  roughly 22 agents had been built and chained via a plain, direct
  async-call pattern with zero real need for graph-level checkpointing
  or resumability ever emerging. The real, evidence-based call: reverse
  the original framework decision, keep the orchestrator a plain async
  function, and log the reversal explicitly in `DECISIONS.md` as a
  deliberate reversal, not an oversight — including exactly which
  existing document (an early ADR) needs a correcting note as a result.
- **Delete the interim hand-threaded chaining module once the real
  orchestrator supersedes it**, after confirming via a real search that
  nothing else still imports it — don't leave two parallel
  pipeline-chaining implementations that can silently drift apart.
- **Confirm the real service topology (are the gateway and the agent
  runtime actually separate deployable services, or one process?)
  before wiring the new orchestrator's entry point** — this determines
  whether the gateway calls the orchestrator over real HTTP or
  in-process.
- **Give session/conversation state a lifecycle-appropriate store.**
  Short-lived, naturally TTL-bounded state (an in-progress
  conversation's turn history) belongs in a cache store with real
  expiry, not a permanent database table that would need its own
  eviction job built for free elsewhere.
- **Keep cross-cutting request identity out of any domain-specific
  payload.** A session identifier that only concerns the orchestration
  layer belongs in the orchestrator's own payload shape, not bolted onto
  the one shared, tenant-wide request-context contract every other agent
  already depends on.
- **A clarification/ambiguity-handling agent should trigger on exactly
  one narrow, already-observed real condition** (in this project: the
  schema-mapping step resolving zero tables at all) — not a general,
  vague "low confidence" heuristic. A partial resolution should still
  proceed normally; only a total resolution failure should short-circuit
  into asking the user a real clarifying question instead of failing
  outright.
- **The orchestrator should resolve any omitted routing parameter
  itself** (e.g. which data source to use, if the caller didn't specify
  one) via a real, unambiguous lookup — exactly one match proceeds;
  zero or more than one is a real, structured failure, never a silent
  guess.
- **Thread lineage recording through every stage from inside the
  orchestrator itself**, and treat a lineage-recording failure as
  logged-but-non-fatal (an audit side channel, not a correctness gate)
  — state this as a deliberate, named behavioral choice.

### Verify

1. Unit tests across all three new agents.
2. A real integration test: the full real pipeline for a real worked
   question, asserting a real, complete `answered` outcome; a real
   session round-trip (mint a session, inspect the real cache entry
   directly, confirm a second call with that session ID sees the
   persisted history); a real clarification trigger for a
   known-zero-table-resolution scenario, asserting the structured
   clarification outcome, never a bare failure.
3. Rebuild/restart both the gateway and the agent runtime; a real HTTP
   call through the actual public-facing entry point showing a real,
   complete answer end to end through the real orchestrator.
4. Re-run the evaluation harness; confirm the specific real questions
   that previously hard-failed (zero-table-resolution cases) now
   produce a real clarification outcome instead.
5. Update all three logs and any now-corrected earlier documents (e.g.
   an ADR); commit.

---

## Part 12 — Phase 10a: Kubernetes Manifests + Zero-Cost CD Pipeline

**Objective**: everything needed for a real cloud deployment — manifests,
the CD workflow, the canary mechanism, adversarial cloud-security tests —
built and *proven for real*, entirely before any real cloud cost is
incurred.

### Key decisions

- **Split cloud deployment into two hard-gated sub-phases with an
  explicit stop between them.** Everything buildable and provable with
  zero cloud cost (manifests, CD workflow logic, a local
  cluster-simulator validation of the canary mechanism itself,
  adversarial test code) is one sub-phase; anything requiring real,
  billable cloud credentials is a separate sub-phase that cannot start
  until the product owner explicitly provides real access. This mirrors
  — at an even more conservative point — the product's own "explicit
  stop before any real-data go-live" requirement: stop before any real
  *money* go-live too.
- **Choose the deployment-manifest tool that adds no new operational
  surface** over what your target platform's own CLI already does
  natively (this project chose plain Kustomize over a templating
  engine) — same reasoning that ruled out a heavier GitOps controller in
  favor of a push-based CI workflow.
- **Canary must be real, weighted traffic splitting at the ingress
  layer**, not a bare rolling-update deployment (which delivers no real
  canary semantics at all). Give only the user-facing entry-point
  services a permanent stable/canary deployment-and-service pair and a
  second, canary-annotated ingress route; keep every internal-only
  service on a plain rolling update.
- **A canary rollout in progress must never risk breaking a code path
  that users didn't explicitly opt into.** In this project: server-side
  rendering always calls the stable backend track, never canary; only a
  direct browser-side call sees canary-weighted traffic. State this as
  a deliberate, named asymmetry.
- **Read the promotion gate's real signal from infrastructure-layer
  metrics already labeled per backend**, not app-level metrics that
  would need new instrumentation in every service — one uniform
  mechanism then covers every canary-tracked service with zero app code
  changes. Define concrete, numeric pass/fail thresholds (error rate,
  relative error rate vs. the stable track, latency ratio) checked at
  each weight step with a bounded bake window, and make the same script
  usable by both the automated pipeline and a human running it by hand.
- **Give a human an independent, immediate manual rollback path** to any
  prior known-good version, separate from the automated gate — the
  automated gate can misjudge a real issue; a human escape hatch should
  never depend on the same logic.
- **Secrets come from your cloud's real secrets manager via whatever
  CSI-driver-style integration it offers**, synced into one shared,
  per-namespace secret object — never plaintext secrets committed to
  git, even in a local-cluster overlay (use a manual, gitignored,
  human-run secret-creation step there instead).
- **Name every known, accepted security gap honestly, in its own
  adversarial test that proves and documents the gap** rather than
  silently assuming it away — e.g. a shared-identity secrets-integration
  scoping gap, or a missing identity-provider-integrated cluster RBAC in
  a non-production environment. A test that documents a real, accepted
  gap is more valuable than no test at all.
- **Prove the canary mechanism itself for real**, in CI, before any real
  cloud environment exists — build a second, marker-bearing image,
  load it into a local ephemeral cluster, set a real weight, fire a
  real batch of requests at the ingress, and check the marker's observed
  proportion against the expected weight. This is the single highest-risk
  new mechanism in the whole deployment design; prove it before trusting
  it against real, billable infrastructure.

### Verify

1. Infra-as-code validation (`fmt`/`validate`, no credentials needed).
2. A full local-cluster sequence: cluster up, ingress controller
   installed, all real images built and loaded, manifests applied,
   every workload reaches ready, a real HTTP smoke test succeeds, and a
   real end-to-end request round-trips against the local cluster's own
   ephemeral database.
3. The canary-weighting proof described above, run for real and shown,
   not assumed.
4. The local-cluster validation workflow passing for real in CI on every
   relevant PR, continuously — not just verified once by hand.
5. Update all three logs; commit — **before** proceeding to the next
   sub-phase.

---

## Part 13 — Phase 10b: Real Cloud Deployment

**This sub-phase does not start until the product owner explicitly
provides real cloud access** — never fabricate, guess, or proceed with
placeholder credentials for any step here. Real credentials go directly
into a local, gitignored variables file, never echoed back, never
committed, matching exactly how every other real external credential in
this project was handled.

### Sequence

1. **Real plan, shown in full, before any real apply.** A blanket
   earlier go-ahead on the phase does not authorize this specific,
   billable step — get separate, explicit confirmation on the actual
   plan output.
2. **Real apply** — creates real, billable resources that keep costing
   money until torn down.
3. **One-time, human-run bootstrap steps** that aren't infrastructure-as-
   code and aren't automatable from a normal CI run: adding a second
   federated-credential subject to an existing CI identity so a new
   workflow can authenticate too (a real, exact-format matching problem
   — expect this to need a real fix if your cloud's federated-identity
   subject format is stricter or different than initially assumed);
   configuring the CI secrets for real for the first time; installing
   cluster-wide add-ons (an ingress controller, a certificate manager)
   via their official manifests.
4. **A real CD run**: build and push real images, deploy to the real
   cluster, run the real canary rollout against real metrics, promote or
   roll back for real.
5. **A real evaluation-harness run** against the real cloud-deployed
   environment — same golden questions, same real external providers —
   confirming the deployment behaves identically to the verified local
   stack, not just that workloads are reported as running.
6. **A real adversarial security review**, re-pointed at the real
   deployed policy engine and the real cloud resources — report whatever
   it actually finds, including every already-anticipated accepted gap,
   rather than tuning the tests to pass.
7. **Update all three logs with the real findings** from steps 4–6,
   whatever they turn out to be.
8. **An explicit stop before any real-data go-live.** Everything above
   stands up a real environment for verification purposes; treating it
   as the promoted, real-customer-facing system requires its own
   separate, explicit go-ahead after reviewing the security-review
   findings — never assumed as a natural continuation of "the deployment
   worked."

---

## Part 14 — Post-Launch: Real-World Hardening (the phase most plans omit)

**This is not an optional phase.** Every prior phase's verification was
real, but every one of them exercised the system through a test harness,
an integration test, or a pod-local call — until, for the first time,
the actual real public path is exercised by an actual real client (a
browser, a demo user). Budget real time for this, expect it to surface
genuinely new bugs even after 10 "complete" phases, and treat it with
the exact same discipline as every earlier phase: reproduce live,
root-cause with real evidence, fix live first then commit to source,
verify live again, log it.

**Real bug classes this project found in exactly this phase** — treat
these as a checklist to actively look for in your own post-launch pass,
not just as this project's specific history:

1. **A default-deny network policy misses a newly-introduced pod type.**
   Happened three separate times in this project (a service-to-service
   egress half missing, external-database egress missing, a
   dynamically-created certificate-challenge solver pod missing) — every
   *new* pod type, static or dynamic, needs its own explicit allow-rule;
   there is no implicit "internal traffic is fine" default, and a local
   cluster simulator that doesn't enforce network policy at all will
   never catch this.
2. **A timeout budget calibrated for an internal/short call turns out too
   short for the real, multi-hop, multi-LLM-call production path.** Two
   independent layers (an application HTTP client's own timeout, and the
   ingress's own proxy timeout) both need to agree on the real worst-case
   latency — raising only one just moves the bottleneck to the other.
3. **A build-time-vs-runtime environment-variable assumption breaks
   silently for a dynamically-rendered page.** If a frontend page makes
   its own dynamic (non-cached) data fetch, a framework that would
   otherwise inline a "build-time" public environment variable may
   instead read it fresh from the actual runtime process environment on
   every request — and if your container build has separate build and
   runtime stages, an environment variable set only in the build stage
   is silently absent at real runtime. Test this specific case explicitly
   once any browser-facing feature reads such a variable.
4. **An LLM call's token budget, sized against a small test fixture,
   silently trutruncates once tested against a production-scale input.**
   A closed-candidate-list LLM call whose prompt size scales with a real
   catalog's real size (not a fixed handful of test candidates) can
   silently blow through a token budget that worked fine in every unit
   test — watch for a real response's token-usage metadata landing
   exactly at the configured cap with empty resulting content; that's the
   signature of truncation, not a transient glitch, and a "retry the
   exact same call once" mitigation (good for real transient empty
   completions) will not fix a structural under-budgeting problem.
5. **A phrase-trigger heuristic fixed for one exact phrasing resurfaces
   under a different phrasing of the same underlying question shape.**
   If a heuristic exists specifically to override a default aggregation
   choice for one question shape ("how many X" → always count, never
   sum), expect a semantically identical but differently-worded question
   to slip past the same narrow trigger — and prefer fixing the more
   general root cause (e.g. "an identifier-shaped column is never a
   valid sum target, regardless of phrasing") over widening the phrase
   list, especially if the same query might legitimately need both a
   count *and* a real sum from different resolved columns at once.
6. **A CI/CD promotion job's own bot-commit can race a concurrent
   deployment's bot-commit.** If your CD pipeline writes back a deployed
   version identifier into the same tracked file every deploy, two
   overlapping real deployments can produce either a cleanly-rebasable
   rejected push (fix: bounded retry-and-rebase) or a genuine same-line
   merge conflict (no auto-resolution is safe — manually reconcile the
   tracked file against the confirmed-live deployed state, and consider
   serializing the promotion step across concurrent runs as a more
   robust long-term fix). Always confirm the live deployment itself
   is unaffected before treating this as urgent — the actual deployment
   commands typically run before the git bookkeeping step, so a
   git-only failure rarely means a bad deploy.
7. **A shared external-API account usage cap can be exhausted by your own
   real debugging activity.** Observed live in this project (recorded
   inline in `LIMITATIONS.md` item 63's discussion and in
   `docs/runbooks/operations-runbook.md`, rather than as its own
   numbered item — worth giving it one if it recurs): if a real bug's
   root-cause investigation requires many real calls against a
   rate/spend-capped external API, expect the same cap to eventually
   block further live verification entirely — recognize the resulting
   error for what it is (an account limit, not a code bug) and either
   wait for the real reset time or use a separate budget, rather than
   mis-attributing it to your own recent change.

---

## Part 15 — The Agent Contract Pattern (reusable template)

The single most important reusable shape in this system. Every agent
(all ~25 in NaviGraph) is exactly:

```
<domain>/<agent_name>/
  agent.py        # the Agent class + its run() method
  contracts.py     # Payload / Input(AgentInput) / Result / Output(AgentOutput)
  prompts/         # only if LLM-backed
  tests/
    test_agent.py                 # unit, mocked LLM client, no network
    test_agent_llm_integration.py # optional, real LLM call, marked skippable
```

Contract rules, non-negotiable:

- Every `AgentInput` subclass carries a mandatory, non-optional shared
  request-context object (tenant ID, user ID, trace ID, roles/claims) —
  this is a graph-level invariant that later makes tenant-isolation
  adversarial testing meaningful, not just a convention.
- Every `AgentOutput` subclass carries `lineage_events`, `errors`
  (each with a stable string code and a `recoverable` flag), and
  `metadata` (latency, and — if LLM-backed — model version, prompt
  version, token counts).
- Every agent is invocable two ways: in-process (the real production
  path) and via a thin, uniform HTTP wrapper
  (`POST /agents/{domain}/{agent_name}/invoke`) for isolated testing,
  debugging, and the evaluation harness — without any contract change
  between the two invocation styles.
- Unit tests use a real-vs-fake-double pattern for every external
  client (a real implementation + a no-network test double recording
  every call made to it) — the fake is the default in unit tests; a
  small number of tests are marked to make one real external call and
  are skipped without real credentials.
- A malformed or missing upstream response (bad JSON, a hallucinated
  ID not in a closed candidate list, an empty completion) is always
  handled by falling back to a safe default plus a recorded, recoverable
  error — never a crash, and never silently trusted.
- Sibling agents that need each other's *shape* mirror that shape
  locally with a rationale comment, rather than cross-importing each
  other's contract module — this stops two leaf agents coupling to each
  other's internals. The one deliberate exception: the single real
  orchestrator that calls every other agent in sequence is allowed (and
  expected) to import each downstream agent's real input/payload types
  directly, since it must construct their actual input shape to invoke
  them at all.

---

## Appendix A — Real Judgment Calls Made During This Build

Every fork below was a genuine product-owner decision, not something
derivable from the code or spec alone — resolve equivalents explicitly
in your own build rather than guessing:

| Fork | Resolution |
|---|---|
| Local dev inner loop vs. cluster-first | docker-compose primary, `kind` secondary |
| Target cloud | Azure |
| Identity provider | Azure AD/Entra ID, real even in local dev |
| Compliance regime | SOC 2 Type II |
| Which data source to build for real now | One connector only (Snowflake); interface stays source-agnostic |
| Real target dataset + business questions | Provided by the product owner after Phase 2's real crawl |
| Execute real SQL before the policy-engine domain exists? | Yes, with named, temporary compensating controls |
| Verify the execution role is genuinely read-only first? | Yes, live grants check before any real execution |
| Federation engine as default execution route? | No — direct connector stays default; federation route built and tested but not default |
| Orchestration framework: keep the original graph-framework decision or reverse it? | Reversed, based on real accumulated evidence across 8 phases of zero real need for it |
| GitOps mechanism: a full controller or a push-based CI workflow? | Push-based CI workflow — no new cluster-side operational surface |
| Domain name for the real deployment | Deferred — a free wildcard DNS scheme accepted for the `dev` environment |
| Golden question set size | Reduced from an original 50-question aspiration to a real, achievable 10, given real per-question cost |
| Fix a newly-found bug now, or defer for a bigger priority (e.g. documentation work)? | Product owner's explicit call each time — logged, not silently deferred |

## Appendix B — Recurring Bug-Class Catalog (see Part 14 for full detail)

A condensed index — search `LIMITATIONS.md` by these shapes when
debugging a similar system:

1. Default-deny network policy missing a new pod type's explicit allow-rule.
2. Timeout budgets not raised consistently across every hop in a
   multi-service, multi-LLM-call request path.
3. Build-time-inlined environment variables silently absent at runtime
   for a dynamically-rendered page.
4. LLM token budgets sized against small test fixtures, truncating
   against production-scale real inputs.
5. Phrase-trigger heuristics fixed for one exact phrasing, missing a
   semantically identical but differently-worded case.
6. Concurrent CI/CD promotion jobs racing on the same tracked
   bookkeeping file.
7. Shared external-API usage caps exhausted by the debugging process
   itself (observed live; worth its own numbered log entry if it
   recurs).

---

## Where the rest of this project's documentation lives

This specification is deliberately the "how to build it" document. For
the finished product's own reference material, see:

- [`README.md`](./README.md) — orientation and current status.
- [`docs/product/prd.md`](./docs/product/prd.md) — product requirements.
- [`docs/architecture/`](./docs/architecture/) — overview, data-flow,
  system architecture, data model.
- [`docs/security/security-compliance.md`](./docs/security/security-compliance.md)
  — the real SOC 2-oriented controls mapping.
- [`docs/testing/test-strategy.md`](./docs/testing/test-strategy.md) —
  the real test pyramid.
- [`docs/runbooks/`](./docs/runbooks/) — local dev, cluster validation,
  and production operations.
- [`ONBOARDING.md`](./ONBOARDING.md) — new-engineer guide.
- [`LIMITATIONS.md`](./LIMITATIONS.md), [`DECISIONS.md`](./DECISIONS.md),
  [`BUILD_LOG.md`](./BUILD_LOG.md) — the three living process logs
  referenced throughout this specification.
