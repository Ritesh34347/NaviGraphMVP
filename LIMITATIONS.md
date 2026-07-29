# Limitations

This document is a deliberately honest, living record of what NaviGraph does **not**
do yet, and why. It is started on **2026-07-28** during the Phase 1 infra scaffold
and is expected to shrink over time as later phases close these gaps. Anything not
listed here is not a known limitation as of the date below — it may still be
incomplete, but nobody has recorded it as an intentional deferral.

---

### 1. Only a Snowflake connector is implemented

**What's deferred**: Postgres and generic REST reference connectors.

**Why**: Phase 1/2 scope is one real, production-quality data source end-to-end
rather than several shallow ones. Snowflake is the customer's actual warehouse.

**What full version requires**: The data-source SDK interface is already written to
be source-agnostic (connection lifecycle, schema introspection, query execution,
credential handling are all behind an interface), but that interface is unproven
against a second, differently-shaped source. A Postgres connector should be built
next specifically to pressure-test the abstraction, followed by a generic REST/API
connector for sources with no SQL surface at all.

### 2. Neo4j runs as a single local instance

**What's deferred**: High-availability Neo4j clustering / Neo4j Aura Enterprise.

**Why**: Local dev and early cloud phases don't need HA; a single instance is
sufficient to validate the knowledge-graph query patterns.

**What full version requires**: Migrating to Aura Enterprise (or a self-managed
causal cluster) as part of the cloud deployment phase, including backup/restore,
read-replica routing, and failover testing.

### 3. Trino has zero real catalogs registered -- RESOLVED in Phase 5 (catalog registration), but the route is not yet default

**What was deferred**: Wiring an actual Snowflake catalog (or any catalog) into Trino.

**Resolution**: Phase 5 registered a real `snowflake.properties` catalog in
both `infra/trino/coordinator/catalog/` and `infra/trino/worker/catalog/`,
fixed a real Trino crash-loop this surfaced (`--add-opens=java.base/java.nio=ALL-UNNAMED`
missing from `jvm.config`), and confirmed via live `SHOW CATALOGS`/
`SHOW SCHEMAS IN snowflake` that Trino genuinely sees the real `FIDELITY_POC`
schema (`far_trans`, `staging`).

**What's still deferred**: `route="trino"` is fully built and unit-tested on
`ExecutionPlan`, but Phase 5's confirmed default (and the only route real
executions currently use) is `route="direct_connector"` -- see item 18
below for why.

### 4. OPA runs an allow-all placeholder policy

**What's deferred**: Real RBAC/ABAC and row-/column-level authorization Rego
policies.

**Why**: The policy engine needs to be wired into the request path structurally
before the real policy logic is written and tested — otherwise policy changes have
no enforcement point to land in.

**What full version requires**: A dedicated later phase to author tenant-, role-,
and attribute-aware Rego policies, plus an adversarial test suite (see
`tests/security/`) that must pass before the placeholder is removed. This is
explicitly not to be marked done without that adversarial test coverage.

### 5. Terraform for Azure is a validated skeleton only

**What's deferred**: Any actually-applied Azure infrastructure.

**Why**: Local-first development via docker-compose is the primary inner loop for
as long as possible. Terraform exists now so the eventual cloud target is designed
deliberately rather than retrofitted, but it is intentionally never run against a
real subscription during this phase.

**What full version requires**: A real Azure subscription, a remote state backend,
a human sign-off step in front of any `terraform apply`, and CI that only ever runs
`fmt`, `validate`, and `plan` (never `apply`) — see `terraform/README.md`.

### 6. SOC 2 Type II controls are scaffolded, not audited

**What's deferred**: Formal documentation and an actual SOC 2 Type II audit.

**Why**: The engineering controls that an audit would check (CI security-scan gate,
CODEOWNERS-enforced review, required-check branch protection) are put in place from
day one so evidence starts accumulating immediately, but "scaffolded" is not
"compliant."

**What full version requires**: Formal policy documentation, a designated
compliance owner, evidence collection over an observation window, and an
independent auditor engagement. This repository's controls are necessary
supporting infrastructure, not sufficient proof of compliance on their own.

### 7. Only one real agent exists (Intent Understanding)

**What's deferred**: The remaining ~24 agents across the Query, Insight, Guardrail,
Ops, and Orchestrator domains.

**Why**: This repo (Phase 1) is infra scaffolding only. Application agents are
being built by a parallel workstream, starting with Intent Understanding as the
proof-of-pattern implementation.

**What full version requires**: Each remaining agent implemented against the
formal contract in `docs/architecture/agent-contract.md`, with its own unit tests
and, where relevant, `@pytest.mark.llm_integration` tests. See
`docs/architecture/overview.md` for the full named list and current status.

### 8. Local tooling versions are not pinned

**What's deferred**: A reproducible, pinned record of exact tool versions for
new-machine setup.

**Why**: This machine had no Docker, Node.js, Terraform, or WSL installed at the
time scaffolding began; they were installed via `winget` during this session
(Node.js v24.18.0, Terraform v1.15.8, Docker Desktop 29.6.2 / Compose v5.3.1 —
see `BUILD_LOG.md`'s 2026-07-28 verification entry). That gets one machine
working but isn't yet a reproducible, version-pinned setup process.

**What full version requires**: A documented, version-pinned bootstrap (e.g. a
`.tool-versions` file, a checked-in `winget` manifest, or devcontainer config)
so a new engineer's machine ends up on the same tool versions without trial and
error.

### 9. LICENSE terms are a placeholder pending legal sign-off

**What's deferred**: Real, legally-reviewed proprietary licensing terms.

**Why**: `LICENSE` currently states a short "all rights reserved" notice under a
placeholder company name (`Navikenz`, matching the deploying organization's email
domain) so the repository isn't left with no license statement at all.

**What full version requires**: Review and sign-off from legal/counsel on the
actual entity name, copyright holder, permitted-use terms for contractors or
partners, and any export-control or data-residency clauses relevant to a
multi-tenant BI product handling customer data.

### 10. Metadata catalog's `connection_ref` is not a real secrets-manager integration

**What's deferred**: Storing/retrieving real data-source credentials via a
proper secrets manager.

**Why**: `DataSource.connection_ref` (added in Phase 2) is deliberately an
opaque JSON pointer (e.g. `{"env_prefix": "SNOWFLAKE"}`), never raw
credentials -- but today that pointer just means "read `SNOWFLAKE_*` from
this process's environment / local `.env`," which only works for a single,
globally-configured data source per environment.

**What full version requires**: Real integration with a secrets manager
(Azure Key Vault, per the Azure target) so each registered `DataSource` row
can reference its own independently-rotatable credential set, supporting
multiple data sources (and eventually multiple tenants' own credentials)
without collisions -- a cloud-deployment-phase concern, not a local-dev one.

### 11. Local dev has a host-level Postgres port conflict, worked around

**What's deferred**: A clean host environment with no port collisions.

**Why**: This dev machine runs a separate, unrelated native Postgres
process already bound to host port 5432. It silently intercepts host-side
TCP connections meant for the docker-compose `postgres` container and
rejects them with a *password authentication failed* error (not
"connection refused"), which is very misleading to debug. Rather than
touch a system service that might belong to something else on this
machine, `infra/docker-compose.yml` now maps the container to host port
**5433** instead (`"5433:5432"`) -- internal container-to-container traffic
(e.g. `agent-runtime` connecting to `postgres:5432`) is unaffected either
way, since that never touches the host-mapped port.

**What full version requires**: Nothing, structurally -- this is a
one-machine quirk, not a design gap. Worth a line in the local-dev runbook
so the next engineer who hits a confusing Postgres auth error on this or a
similarly-configured machine knows to check for a port conflict rather
than doubt their credentials.

### 12. Knowledge graph: Neo4j Community's tenancy is property-based only

**What's deferred**: Real per-tenant database/graph isolation in Neo4j.

**Why**: `infra/docker-compose.yml` runs a single `neo4j:5-community`
instance (see item 2). Multi-database isolation is a Neo4j Enterprise
feature, so every node in `packages/knowledge_graph` carries a `tenant_id`
property instead, filtered explicitly in every Cypher query in
`navigraph_kg.api`. This isn't a new gap introduced by Phase 3 -- it's the
same single-instance limitation already logged in item 2, now visible at
the application-query level rather than only the deployment level.

**What full version requires**: Neo4j Enterprise/Aura Enterprise with real
per-tenant database isolation, as part of the same cloud-deployment phase
already named in item 2 -- not a separate effort.

### 13. Business-glossary and reference-data coverage in the graph is partial by design

**What's deferred**: Nothing -- this is a deliberate, permanent property of
the design, not a gap expected to close over time.

**Why**: Only ~41 of the real crawled columns have a `SCHEMA_ENRICHMENT`
glossary entry (confirmed against the live account); columns without one
get no `BusinessConcept` node at all -- "no business concept exists yet
for this column" is meant to be a legitimate, surfaced answer for a future
NLQ pipeline, not something to synthesize a fallback for. Similarly,
`Sector`/`Industry` edges are only created for the ~50% of real assets
that have a non-null value in Snowflake (bonds/MTF funds legitimately have
neither) -- there is no placeholder "Unclassified" node.

**What full version requires**: Nothing structurally; if broader glossary
coverage is wanted later, it's a data-curation task (adding more
`SCHEMA_ENRICHMENT` rows or a hand-curated equivalent), not a code change.

### 14. Business-concept mappings are anchored to `STAGING`, not `FAR_TRANS`

**What's deferred**: Deciding whether `FAR_TRANS` (not `STAGING`) should be
the canonical resolution target for business terms.

**Why**: The real `STAGING.SCHEMA_ENRICHMENT` glossary only references
`staging_`-prefixed table names, so every `BusinessConcept -> MAPS_TO ->
Column` edge in the graph resolves to the `STAGING` schema's copies (e.g.
`STAGING.STAGING_TRANSACTIONS.UNITS`), never the equivalent `FAR_TRANS`
column, even though both real columns exist side by side. Confirmed live
while building Phase 4's cross-agent integration test: Ontology correctly
resolved "units traded" to the `STAGING` column per the graph's actual
data, which initially broke a test assumption that it would resolve to
`FAR_TRANS` instead.

**What full version requires**: A decision (not made yet) on which schema
is the intended long-term query target for generated SQL. If `FAR_TRANS`
is meant to be canonical (`STAGING` reads as an ETL staging area, not a
production query surface), the glossary crawl and/or the knowledge-graph
ingestion would need a preference rule to resolve business terms against
`FAR_TRANS` columns when an equivalent exists in both schemas. This is a
real, live-verified fact about the current system, not a hypothetical —
whichever phase owns SQL Generation (Query domain) needs to account for it
one way or another.

### 15. Ontology Agent's relationship-concept matching accepts low recall

**What's deferred**: Fuzzy/paraphrase matching of relationship-shaped
questions (e.g. "which customers hold X").

**Why**: Ontology Agent matches raw extracted entity strings against the
hand-curated `RelationshipConcept` seed data's `subject_label`/
`object_label` fields via case-insensitive substring matching only — it
will miss many real phrasings of a relationship. This is deliberately
additive-only (a missed match just means `relationship_resolutions` stays
empty, never a wrong answer), not silently accepted — expanding Semantic
Retrieval's LLM-backed fallback to also handle relationship shapes (not
just single-column terms) was considered and explicitly deferred rather
than folded in without a decision.

**What full version requires**: A design decision on whether relationship
resolution gets its own fuzzy-matching stage, or whether Semantic
Retrieval's contract expands to cover it — not yet decided.

### 16. Schema Mapping's measure/dimension role assignment is a heuristic

**What's deferred**: A real, stored semantic-role field in the metadata
catalog.

**Why**: `role` (`measure` vs `dimension`) is inferred from `data_type`
(numeric or not) plus the classified intent, entirely in Schema Mapping
Agent's business logic — `CatalogColumn`/`ColumnGlossary` have no field
recording a column's intended semantic role. This works for the columns
seen so far but is a guess, not an authoritative signal.

**What full version requires**: If the Query domain (SQL Generation, not
built yet) later needs a firmer signal than this heuristic provides, add a
real `semantic_role` column to `navigraph_catalog`'s schema (a migration,
not a heuristic change) rather than making the heuristic more elaborate.

### 17. Conversation Agent has no real persistence this phase

**What's deferred**: Storing, retrieving, summarizing, or evicting
conversation history across turns/sessions.

**Why**: Conversation Agent operates purely on a `conversation_history`
list handed to it directly in its input — it never fetches or stores
anything itself. This is deliberate, not an oversight: a fake in-memory
store would look production-ready without being durable, multi-instance
safe, or tenant-isolated. The real home for this is the Memory Agent
(Phase 9, Orchestrator domain) — Conversation Agent's `conversation_history`
field is the seam Phase 9 fills in.

**What full version requires**: Phase 9's Memory Agent, plus whatever
Coordinator wiring (also Phase 9) actually populates `conversation_history`
before invoking Conversation Agent turn over turn.

### 18. The Guardrail domain (real RBAC/ABAC/row-column policy) does not exist yet -- Phase 5 executes real SQL against live Snowflake with compensating controls only

**What's deferred**: Real, policy-driven access control (OPA Rego rules
evaluated per-request against a user's role/attributes, row-level and
column-level masking). OPA currently runs the same allow-all placeholder
policy from Phase 1 (item 4) — nothing added this phase.

**Why**: The product spec places Guardrail immediately after Query
(this phase), not before it. Rather than block all real SQL execution
until Guardrail lands, Phase 5 was built with the user's explicit,
confirmed go-ahead to execute real SQL now, backed by real, structural
compensating controls that do not depend on Guardrail existing:

- Execution Planning Agent's real string-masking SQL parser hard-rejects
  anything that isn't a single read-only `SELECT`/`WITH` statement —
  verified live: a deliberately malicious `SELECT 1; DROP TABLE ...`
  statement was rejected by this exact gate in
  `tests/integration/query_pipeline/test_pipeline_chain.py`, and never
  reached Data Federation.
- Every literal predicate value is bind-parameterized (`%(name)s`), never
  string-interpolated into SQL text — closes SQL injection independently
  of Guardrail.
- A live, read-only `SHOW GRANTS TO ROLE FIDELITY_ANALYST_ROLE` check
  (run with the user's explicit approval) confirmed the account's
  Snowflake role has zero write privileges — only `USAGE`/`READ`/`SELECT`.
- A hard row-cap (`max_rows`, capped at 10,000) and timeout
  (`timeout_seconds=30`) are re-verified at the `ExecutionPlan` level,
  not just trusted from upstream.

**What full version requires**: Phase 6's real OPA Rego policies (RBAC by
Azure AD role, ABAC by claim, row/column masking), the Security Validation
Agent, and the adversarial tests (`tests/security/`) the user's working
method requires before any of it is marked done. None of Phase 5's
compensating controls are a substitute for this — they exist because of
the gap, not instead of closing it.

### 19. Execution defaults to the direct Snowflake connector, not Trino

**What's deferred**: Routing real query execution through Trino by default.

**Why**: Confirmed with the user during Phase 5 planning — routing through
a general-purpose distributed SQL engine's unaudited access-control
surface during the exact window there is no policy gate (see item 18) to
catch a mistake is the wrong tradeoff. `route="trino"` exists on
`ExecutionPlan` and is unit-tested, but Execution Planning Agent never
assigns it yet; `route="direct_connector"` is the only route any real
execution in this environment has used, including the live proof in
`tests/integration/query_pipeline/`.

**What full version requires**: Either a second real registered data
source creating genuine federation need, or an independent review of
Trino's own access-control configuration — whichever comes first.

### 20. Data Federation's multi-source combine path is real code, unit-tested only against fakes

**What's deferred**: Proving `DataFederationAgent._combine_results`'s
2+-source join/union logic against two genuinely distinct, live data
sources.

**Why**: Exactly one real data source (`fidelity_poc_snowflake_v2`,
Snowflake) is registered in this environment, so every real execution this
phase — including `tests/integration/query_pipeline/`'s live proof — only
ever exercises the single-source pass-through branch. The 2+-source
combine branch (join on shared column names, or union if none are shared)
is real, working code, exercised only by this package's own unit tests
using fake `SourceQueryResult` objects.

**What full version requires**: A second real registered data source, and
a real `ExecutionPlan` field naming the intended join key(s) explicitly
(today the combine step *infers* a join key from column-name overlap,
which is a documented heuristic, not a real join predicate — see
`agent.py`'s `_combine_results` docstring).

### 21. Connector credential routing is global-env-var-based, not per-`DataSource`

**What's deferred**: Resolving distinct credentials for two `DataSource`
rows that share the same `source_type`.

**Why**: `DataSource.connection_ref` is only an opaque pointer (e.g.
`{"env_prefix": "SNOWFLAKE"}`); every connector this phase constructs is
built with no arguments (`get_connector_class(source_type)()`), which
reads that connector class's own global env-var-backed settings. Two
`DataSource` rows of the same `source_type` are therefore indistinguishable
to Data Source Discovery and Data Federation — both resolve to a connector
reading the identical global env vars. Harmless today (exactly one
Snowflake data source is registered), but a real gap.

**What full version requires**: A per-`DataSource` credential-routing
layer (e.g. resolving `connection_ref.env_prefix` to a distinct settings
instance per row) that doesn't exist anywhere in this codebase yet.

### 22. Caching TTL is a flat, conservative default, not a per-intent policy

**What's deferred**: Varying cache TTL (or whether a result is cacheable
at all) by `IntentLabel`, query shape, or a future Guardrail policy.

**Why**: `CachingPayload.ttl_seconds` defaults to a flat 300 seconds for
every cached result, regardless of intent — a deliberate v1 simplification,
not a policy decision made unilaterally. `CachingPayload.policy_version`
is already reserved (defaulted to `"none"`) specifically so a real
Guardrail-driven policy variation becomes "populate an existing field"
later, not a cache-key redesign.

**What full version requires**: Whichever future phase actually needs
intent-aware or policy-aware cache TTLs to populate `policy_version` and
vary `ttl_seconds` accordingly — not addressed here since nothing yet
depends on it.
