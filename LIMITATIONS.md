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

### 15. Ontology Agent's relationship-concept matching accepts low recall — a real gap this caused was found and fixed in Phase 9

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

**Real gap found and fixed in Phase 9**: this limitation was not just
theoretical — a real HTTP smoke test of the newly-wired Request
Orchestrator against the live stack ("What is the total transaction
volume by market?") hit exactly the "missed match" case documented above.
`RELATIONSHIP_CONCEPTS` had no entry linking `TRANSACTIONS` and `MARKETS`,
so Schema Mapping's `_build_joins` (which derives joins *only* from
`relationship_resolutions` — see its own docstring) emitted zero joins
even though the resolved columns spanned both tables. SQL Generation then
had no way to connect them, and the real, generated SQL computed one
ungrounded grand total over all of `TRANSACTIONS` and cross-joined it
against every distinct market name — every row of the answer showed the
identical wrong total. The system's own grounding checks caught the smell
(Grounded Narrative Generation flagged `narrative_contains_unverified_number`;
Anomaly/Outlier Highlighter noted "zero variance across all groups"), which
is what surfaced this during manual review of a real answer rather than
silently shipping it. Fixed by adding a fourth curated entry,
`"Transaction happens in Market"` (`realizing_table="TRANSACTIONS"`,
`subject_key_column`/`object_key_column="MARKETID"`, the real, literal
foreign-key column shared by both tables), to
`navigraph_kg.ontology.RELATIONSHIP_CONCEPTS`, then re-running the real,
idempotent `navigraph_kg.ingestion.pipeline.run_ingestion` against the live
Neo4j to sync the new node. Verified two ways: a direct, deterministic
`POST /agents/understanding/ontology/invoke` call confirming the new
`relationship_resolutions` entry appears, and a direct
`POST /agents/understanding/schema_mapping/invoke` call confirming the
real join (`MARKETS.MARKETID = TRANSACTIONS.MARKETID`) now gets built —
not just an end-to-end re-run, since Semantic Retrieval's LLM-backed
column choice is nondeterministic and a single successful re-run would not
have been conclusive proof (an early re-run attempt, in fact, coincidentally
avoided the join entirely by resolving "market" to `TRANSACTIONS.MARKETID`
directly rather than `MARKETS.NAME`). **This is a fix to one specific,
now-observed case, not a fix to the underlying low-recall limitation
itself** — other real questions needing a join the curated seed list
doesn't yet cover will still silently produce zero joins rather than a
loud error. The broader design decision above (fuzzy relationship matching,
or an expanded Semantic Retrieval contract) remains open.

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

### 17. Conversation Agent has no real persistence this phase -- RESOLVED in Phase 9

**What was deferred** (Phase 4): Storing, retrieving, summarizing, or
evicting conversation history across turns/sessions.

**Why it was deferred**: Conversation Agent operates purely on a
`conversation_history` list handed to it directly in its input — it never
fetches or stores anything itself. This was deliberate, not an oversight:
a fake in-memory store would have looked production-ready without being
durable, multi-instance safe, or tenant-isolated.

**Resolution (Phase 9)**: the Session/Context Manager agent
(`orchestrator.session_context_manager`) now provides real,
Redis-backed persistence — a tenant-scoped, sliding-TTL (1800s) key per
session (`navigraph:v1:{tenant_id}:session:{session_id}`), storing up to
the most recent 20 turns. The Request Orchestrator reads a session's
history via this agent before calling Conversation Agent, and appends the
new turn after every branch (success, failure, or clarification) — real,
proven via `tests/integration/orchestrator_pipeline/test_pipeline_chain.py`'s
session round-trip test (a real Redis key inspected directly, a second
call with the same `session_id` seeing the persisted history). No
"Memory Agent" by that name was built — `docs/architecture/overview.md`'s
Orchestrator table names it "Session/Context Manager", which is what this
resolution actually built; see this file's item 32 on documentation
staleness for the broader note that agent names across docs have drifted
from what's actually shipped.

### 18. The Guardrail domain (real RBAC/ABAC/row-column policy) does not exist yet -- RESOLVED in Phase 6

**What was deferred** (Phase 5): Real, policy-driven access control (OPA
Rego rules evaluated per-request against a user's role/attributes, and
column-level PII enforcement). OPA ran the same allow-all placeholder
policy from Phase 1 (item 4).

**Resolution**: Phase 6 built the real Guardrail domain: the 4 agents
`docs/architecture/overview.md` actually names (**Schema Constraint
Validator**, **Policy Authorization**, **Query Cost/Row-Limit Estimator**,
**PII Exposure Checker**), a real `infra/opa/policies/authz.rego` policy
(deny-by-default RBAC + tenant ABAC, replacing the allow-all placeholder),
and the adversarial test suite `tests/security/` now contains for real —
see that directory's README for exactly what each test proves. (This
item's earlier draft used the imprecise phrase "the Security Validation
Agent," which didn't correspond to anything in `overview.md`'s actual
4-agent list; corrected here to name the real agents that shipped.)

Compensating controls Phase 5 relied on remain in place, now layered
underneath real policy enforcement rather than standing in for it:
Execution Planning's read-only-SELECT gate, bind-parameterized predicate
values, and the live-verified `FIDELITY_ANALYST_ROLE` read-only grant.

**What's still deferred**: see items 23–25 below.

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

### 23. Azure AD token verification is not implemented — `RequestContext.roles`/`claims` remain caller-supplied

**What's deferred**: Real JWT/OIDC validation of an Azure AD (Entra ID)
token, extracting `roles`/`claims` from a cryptographically verified
identity rather than trusting whatever the caller directly supplies.

**Why**: Confirmed explicitly with the user before building Phase 6 (via
`AskUserQuestion`) — the real policy engine and Guardrail agents were
built to evaluate `RequestContext.roles`/`claims` exactly as every other
agent already trusts that field, deliberately deferring gateway-level
token verification rather than blocking this phase on an Azure Portal
app-registration step. This is what makes
`tests/security/test_opa_policy_adversarial.py`'s
`test_self_declared_role_escalation_with_a_matching_tenant_claim_is_allowed`
test pass — a self-declared `roles=["admin"]` with a matching tenant claim
IS allowed by the real policy today, because Rego has no cryptographic
identity to check that claim's provenance against.

**What full version requires**: Real Azure AD JWT/JWKS validation
middleware in the gateway (or agent-runtime), populating
`RequestContext.roles`/`claims` from a verified token rather than a
caller-supplied field — the terraform/entra-app-registration skeleton
(item 5) and a real dev app registration are prerequisites.

### 24. Query Cost/Row-Limit Estimator's per-role limits are a hardcoded Python dict, not policy-driven

**What's deferred**: Sourcing per-role row limits from OPA/Rego (or any
other centrally-managed policy store) instead of a module-level constant.

**Why**: `guardrail.query_cost_estimator.agent.ROLE_ROW_LIMITS` is a plain
Python dict (`{"analyst": 5_000, "pii_viewer": 5_000, "admin": 10_000}`,
default `1_000`, capped at `MAX_ROWS_CAP=10_000`) — cost/capacity policy is
a distinct concern from authorization (see DECISIONS.md), and doesn't need
Rego's deny-by-default semantics. `QueryCostEstimatorResult.cost_policy_version`
is reserved (always `"v1"` today) specifically so a future policy-driven
variant is "populate an existing field," not a redesign — mirrors
`CachingPayload.policy_version`'s identical precedent.

**What full version requires**: The exact `ROLE_ROW_LIMITS` numbers are the
build's own placeholders, not a real, confirmed business requirement —
flagged for the user to confirm or override before relying on them as
real policy. Whichever future phase needs the limits centrally managed
(rather than redeployed per code change) should push them through Rego or
a config service, populating `cost_policy_version` at that point.

### 25. PII tagging (`CatalogColumn.is_pii`) is a manual, scripted backfill

**What's deferred**: Automatically inferring which columns carry PII (from
Snowflake column tags/comments, a real DLP/classification scan, or a
naming heuristic run at crawl time).

**Why**: `is_pii` defaults to `false` for every crawled column and is only
ever set true by a human deliberately running
`tools/scripts/tag_pii_columns.py` against a column list confirmed via a
real, live discovery query — never guessed automatically. In the real
`FIDELITY_POC` dataset, this surfaced a genuine finding: there are no
traditional PII fields at all (no name/email/phone/address columns) — the
one real, defensible PII-shaped column is `CUSTOMERID` (a direct customer
identifier), tagged on both registered data sources
(`fidelity_poc_snowflake` and `fidelity_poc_snowflake_v2`) across
`CUSTOMER_INFORMATION`/`STAGING_CUSTOMER_INFORMATION`/`V_CUSTOMER_CURRENT`.
This was confirmed with the user (via `AskUserQuestion`) rather than
decided unilaterally, given the real compliance-classification judgment
call involved.

**What full version requires**: A real DLP scan or Snowflake-native
column-tagging integration, if/when this dataset (or a future real
tenant's dataset) has richer PII surface than a bare identifier column.

### 26. Two registered data sources exist for one tenant, with divergent PII tagging risk

**What's deferred**: Reconciling why `navikenz-poc` has two registered
`DataSource` rows for the same underlying Snowflake account
(`fidelity_poc_snowflake` and `fidelity_poc_snowflake_v2`, both created
during Phase 2/3 crawls) — a real, pre-existing condition Phase 6's PII
backfill surfaced concretely: `DataSourceDiscoveryAgent`'s table-owner
resolution picks whichever data source it encounters first for a given
table name (no defined ordering), so `STAGING_TRANSACTIONS` and
`CUSTOMER_INFORMATION` currently resolve to `fidelity_poc_snowflake` (the
older registration) at runtime, not `_v2`. Phase 6's PII tagging was
applied to BOTH registrations specifically to avoid this ambiguity causing
a real, silent security gap (tagging only `_v2` while the pipeline
actually resolves the other one — a real mistake caught live via
`tests/integration/guardrail_pipeline/` before this fix).

**What full version requires**: A decision on which of the two data
source registrations is canonical (or a real de-duplication pass), so
future catalog-derived decisions (PII tagging, glossary curation, business
concept mapping) don't need to be applied twice defensively.

### 27. Rego policy hardening found live, during adversarial testing, not before

**What's deferred**: A general practice note, not a specific gap — two
real correctness issues in `infra/opa/policies/authz.rego` were found only
by actually running `tests/security/test_opa_policy_adversarial.py`
against the real, live policy, not by reading the Rego: (1) `input.claims`
being `null` produced an empty `deny_reasons` list despite correctly
denying (an `object.get` internal error silently dropped that rule
instance); (2) an empty-string `tenant_id` matching an equally
empty-string claim was structurally `==` and therefore incorrectly
**allowed**. Both are fixed in the shipped policy (`default claims := {}`
null-coalescing, and an explicit `input.tenant_id != ""` check).

**Why this is logged at all**: a reminder, for whichever future phase adds
more Rego rules, that "the policy compiles and the happy path works" is
not sufficient evidence of correctness — adversarial inputs against the
real OPA service are required before any policy change is considered
done, exactly as this project's working method already states.

### 28. Chart Selection's column-role linkage across SQL Generation's aliasing is manually threaded, not structurally carried by any contract -- PARTIALLY RESOLVED in Phase 9

**What was deferred** (Phase 7): No contract between SQL Generation and Data
Federation (`OptimizedSql`, `ExecutionPlan`, `SourceQueryResult`,
`DataFederationResult`) preserves a resolved column's measure/dimension
role or its real result-set header. `DataFederationResult.final_columns`
is a bare `list[str]`.

**Why it was deferred**: SQL Generation's own aggregation aliasing
(`sql_generation.agent._generate_statements`/`_aggregation_function`: a
`role="measure"` column becomes `{column_name}_TOTAL` in the real SELECT
list, e.g. `UNITS` → `UNITS_TOTAL`) means a measure's catalog
`column_name` and its real result-set header diverge — so Chart Selection
needs both the role AND the real alias to pick sensible x/y columns.
`ChartColumnRef.result_alias` exists specifically to carry this, but until
Phase 9 the CALLER (a human-written test, absent a real Orchestrator)
populated it by hand, replicating SQL Generation's alias rule —
demonstrated concretely in
`tests/integration/insight_pipeline/test_pipeline_chain.py` rather than
glossed over.

**Partial resolution (Phase 9)**: the real Request Orchestrator now builds
`ChartColumnRef` itself, in real production code, via a real `_alias_for(...)`
helper (ported verbatim from the pre-Phase-9 pipeline-chaining logic) —
so every real request through the real orchestrator gets this threaded
correctly, not just a hand-written test. This is only a PARTIAL resolution,
not a full structural fix: `_alias_for` still duplicates SQL Generation's
aliasing rule rather than SQL Generation/Data Federation's own contracts
carrying the mapping forward as a first-class field. No prior phase has
gone back to modify an already-shipped upstream agent's contract for a
downstream phase's convenience — this remains true; Phase 9 solved the
"who computes it" problem (a real caller now exists) without solving the
"is duplicating the rule correct forever" problem.

**What full version requires**: either accept `_alias_for`'s
rule-duplication as a permanent, working pattern (same category as this
codebase's other sibling-duplication conventions), or add a real field to
`GeneratedSql`/`OptimizedSql` carrying the alias mapping forward
structurally — not yet decided.

### 29. Anomaly/Outlier Highlighter's z-score threshold and minimum-group-count are placeholders pending business confirmation

**What's deferred**: `insight.anomaly_outlier_highlighter.agent._Z_SCORE_THRESHOLD`
(`2.0`) and `_MIN_GROUPS_FOR_DETECTION` (`3`) are reasonable v1 defaults,
not a confirmed business requirement.

**Why**: Same category as item 24's `ROLE_ROW_LIMITS` — a real,
conservative default was needed to ship a working agent; the exact
numbers were never validated against a real business threshold for "how
unusual is unusual."

**What full version requires**: Confirm or override with the user once
this matters in practice — e.g. real users flagging too many/too few
detected anomalies against the actual data distributions NaviGraph sees.

### 30. Grounded Narrative Generation's numeric-hallucination check has a real, scoped blind spot

**What's deferred**: Catching a real value *misattributed* to the wrong
row/group (e.g. citing the West region's real number as if it were the
East region's).

**Why**: The two-layer validation (`_validate_citations`'s closed
candidate-set check, `_scan_for_unverifiable_numbers`'s whole-narrative
scan) can only ever catch wholesale fabrication — a number that doesn't
match ANY real value anywhere in the data. If the LLM cites a genuinely
real value but attaches it to the wrong row/column, and that same value
also doesn't happen to be wrong-but-absent elsewhere, both layers pass it
through. Catching misattribution would require re-deriving each claim's
intended row/group from the narrative's own prose, which neither layer
attempts.

**What full version requires**: A more sophisticated grounding check (or
a stricter prompt constraining the LLM to only ever restate values
verbatim from a single, pre-selected row) if misattribution turns out to
be a real, observed failure mode in practice — not addressed
speculatively here.

### 31. Follow-up Suggestion's question text is unvalidated free text beyond shape/length/count checks

**What's deferred**: Any grounding/hallucination check on suggested
follow-up question text.

**Why**: Deliberate, not an oversight — a suggested question is a
proposal, not a factual claim, and routinely and correctly introduces
concepts outside the closed candidate list on purpose (the worked
example's own "did any single account drive the Southwest spike"
deliberately introduces "account," absent from `final_columns`). Applying
Grounded Narrative Generation's closed-candidate-list discipline here
would reject exactly the useful, exploratory suggestions this agent
exists to produce. Only shape validation applies: 1-3 non-empty
suggestions, or a recoverable `AgentError` + empty fallback.

**What full version requires**: Nothing planned — this is a permanent
design boundary, not a gap expected to close.

### 32. Documentation staleness is broader than previously logged

**What's deferred**: `docs/architecture/overview.md`'s domain status
tables still mark every Understanding/Query/Guardrail/Insight agent
`DESIGNED` (none reflect the ~20 real agents actually shipped across
Phases 4-7); `docs/architecture/data-flow.md` still narrates Query/
Guardrail/Ops stages as "(designed)" throughout; `packages/agent_runtime/navigraph_agents/__init__.py`'s
and `packages/gateway/navigraph_gateway/main.py`'s module docstrings still
say "Currently exactly one agent is registered." None of these were
updated as Phases 4-7 actually shipped real, verified agents — this is
stale documentation, not a functional gap.

**Why this wasn't fixed in Phase 7**: reconciling ~20 real agent names
across 4 domains' worth of drift accumulated over 4 prior phases is a
real, careful task on its own. Bundling it into Phase 7 (or any single
feature phase) risks fixing one domain's rows while leaving, or further
obscuring, the rest inconsistent. `LIMITATIONS.md`/`DECISIONS.md`/
`BUILD_LOG.md` — the three documents this project's working method
actually requires kept current every phase — ARE current; the
`docs/architecture/` narrative docs and two module docstrings are a
separate, pre-existing set that was never part of that per-phase
discipline.

**What full version requires**: A dedicated, later "docs reconciliation"
phase whose only job is updating `docs/architecture/overview.md`'s and
`data-flow.md`'s domain tables/narrative and the two stale module
docstrings to reflect the real, current agent roster — not addressed
here, per the recommendation in DECISIONS.md.

### 33. Golden set is 10 questions, not the README's "50+" target

**What's deferred**: Growing `eval/golden_set/` from 10 real,
schema-grounded questions to the 50+ `eval/README.md` originally described.

**Why**: Confirmed with the user — each golden question round-trips the
entire real pipeline (~19 real agent stages, one real Snowflake execution,
one real narrative-generation LLM call) plus one real judge-model LLM
call. 10 questions already cover all 4 real `IntentLabel` values and 4
real tables (`STAGING_TRANSACTIONS`, `STAGING_CUSTOMER_INFORMATION`,
`STAGING_ASSET_INFORMATION`, `STAGING_MARKETS`) at real, moderate cost;
50 would be a 5x real API-spend and wall-clock multiplier not justified
until the harness itself was proven correct on a small set first — which
it now is (see item 38 below for what its first real run found).

**What full version requires**: Grow the set incrementally in a future
phase, once there's a concrete need (e.g. broader regression coverage).

### 34. Regression-tracking thresholds and the judge's 1-5 scale are unvalidated placeholders

**What's deferred**: `run_harness.py`'s `_REGRESSION_SCORE_DROP_THRESHOLD`
(`2`, on the judge's 1-5 scale) and the scale itself have no human-graded
calibration behind them.

**Why**: A real, reasonable v1 default was needed to ship a working
regression check — same category as item 24's `ROLE_ROW_LIMITS` and item
29's z-score threshold. Nothing yet confirms a 2-point drop (vs. 1 or 3)
is the right sensitivity, or that 1-5 (vs. some other scale) best
distinguishes real answer-quality differences.

**What full version requires**: A human-graded calibration set comparing
judge scores against real human judgment, once this matters in practice.

### 35. `docs/architecture/overview.md`'s Ops-domain table incorrectly lists two already-shipped Query-domain agents as separate, still-`DESIGNED` work

**What's deferred**: Correcting `overview.md`'s Ops table, which lists
"Federated Query Executor (Trino)" and "Result Caching" as `DESIGNED`.

**Why**: Both are already shipped, verified, real agents under the Query
domain (`query.data_federation`, `query.caching`, Phase 5) — the table
was never updated, consistent with item 32's broader documentation-
staleness finding. "Error/Retry Handler," the table's 4th listed agent,
remains genuinely deferred — a separate line in the same document
explicitly assigns "retries... and error handling across stages" to the
Orchestrator domain instead, so it is not this phase's job either.

**What full version requires**: The same dedicated docs-reconciliation
phase item 32 recommends, not addressed piecemeal here.

### 36. Evaluation Judge's 1-5 scoring scale and dimension weighting are unvalidated

**What's deferred**: Confirming the judge model's `correctness`/
`groundedness`/`narrative_quality` scores actually correlate with real
human judgment of answer quality.

**Why**: No human-graded calibration set exists yet — same underlying gap
as item 34, restated here specifically for the judge agent's own design
(the scale and the three chosen dimensions), not just the regression
threshold that consumes its output.

**What full version requires**: A calibration pass once real usage
provides enough real judged answers to compare against human review.

### 37. Real LLM responses wrap structured JSON in markdown code fences — found live, fixed, but a reminder for future agents

**What happened**: The evaluation harness's first-ever real Anthropic call
(every LLM-backed agent in this project had previously only run against
`FakeLLMClient` in unit tests, or been skipped in the `llm_integration`
tier for lack of a real API key) immediately failed: the real
`claude-sonnet-5` model wrapped its JSON response in a
` ```json ... ``` ` markdown code fence even though every system prompt
explicitly asks for "strict JSON." Every one of the 7 LLM-backed agents
(Conversation, Intent Understanding, Semantic Retrieval, SQL Generation,
Grounded Narrative Generation, Follow-up Suggestion, Evaluation Judge)
called `json.loads(llm_response.text)` directly and had the identical
gap. Fixed once, centrally, in
`navigraph_shared.llm.strip_json_code_fence` — every agent's
`_parse_llm_response` now strips a wrapping fence (if present) before
parsing; a genuinely malformed response still fails exactly as before, so
this closes a real, comprehensively-observed gap without masking actual
malformed output.

**Why this is logged at all**: a reminder, for whichever future phase
adds a new LLM-backed agent, that unit tests against `FakeLLMClient` never
exercise this exact failure mode — a real model's actual output shape can
only be proven correct by a real call. `strip_json_code_fence` must be
called by any new agent's own JSON-parsing path, not reinvented.

### 38. Real findings from Phase 8's first live, full-real-model harness run (10/10 questions, all with a real Anthropic model)

**What was found, run against the real 10-question golden set for real**
(pipeline success rate 60%, avg scores 3.0/2.8/3.0 out of 5 — see
`eval/results/` for the full report):

- **Two real, correct PII rejections** (`gq_005`, `gq_009`, both touching
  `RISKLEVEL`): the Guardrail domain's PII Exposure Checker correctly
  blocked the `"analyst"` role from a real PII column — the system working
  exactly as designed, not a bug.
- **A real, correctly-caught hallucination** (`gq_006`): the real model
  cited a value not present in the real data; Grounded Narrative
  Generation's citation-validation mechanism dropped it and recorded
  `llm_cited_fabricated_value` — proof the mechanism works against a
  genuine model, not just the hand-scripted rejection case in
  `tests/integration/insight_pipeline/`.
- **A real SQL Generation aggregation gap** (`gq_002`, "how many
  transactions has each customer made"): the generated `SUM`-based
  aggregate produced nonsensical per-customer totals (e.g. "1,229,737,256
  transactions"), triggering 14 `narrative_contains_unverified_number`
  errors — `sql_generation.agent._aggregation_function`'s current rule
  (numeric `data_type` + a measure-shaped intent → `SUM`) does not
  distinguish "sum this quantity" from "count these rows," which a
  "how many X" phrasing needs. Not fixed here — a real, scoped gap for
  whichever future phase revisits SQL Generation's aggregation-choice
  heuristic.
- **Two real schema-resolution misses** (`gq_007`: "transaction volume" +
  "markets"; `gq_010`: "transaction pattern"): Ontology/Semantic Retrieval
  failed to resolve these real phrasings to any real column against a
  real (non-canned) model, unlike every phrasing previously hand-picked
  for canned test fixtures. A real recall gap in term resolution, not a
  crash — the pipeline correctly reported `succeeded=False` rather than
  guessing.
- **A real golden-set calibration gap** (`gq_008`): the real Intent
  Understanding classification returned `"unknown"` for a question this
  golden question's own hand-authored `expected_intent: metric_lookup`
  assumed would classify cleanly — either the golden set's expectation or
  Intent Understanding's real classification behavior for this exact
  question shape needs a closer look; not resolved here.
- **The judge model's own occasional malformed-JSON response rate isn't
  zero either** (`gq_002`, `gq_006`): both times handled gracefully by
  `EvaluationJudgeAgent`'s existing fallback (all three dimensions to
  `score=1`, one `judge_response_malformed` error), never a crash.

**Why this is logged as a single item**: all of the above were discovered
by the SAME event (the harness's first live run against a real model) and
are exactly the kind of honest, real signal the harness exists to
surface — logging them individually would fragment one coherent finding.
None are fixed in this phase; fixing any of them is real, valuable, and
explicitly out of scope for "build a working harness that produces real
signal," which this phase's job was.

### 39. No real checkpointing or resumability for a mid-pipeline crash

**What's deferred**: If the agent-runtime process crashes or is killed
partway through the Request Orchestrator's ~19-stage sequence, the entire
request is lost — there is no persisted intermediate state to resume from.

**Why**: this is the direct, accepted consequence of the Phase 9 decision
to build a plain Python orchestrator instead of a LangGraph graph (see
`DECISIONS.md`) — LangGraph's checkpointing was the one concrete
capability given up in that reversal. Session/Context Manager's Redis
persistence covers cross-*request* conversational continuity (the caller
can retry with the same `session_id` and the prior turn's resolved
context is still there), but not resuming a single in-flight request
from wherever it crashed.

**What full version requires**: a real, business-driven need for
mid-pipeline resumability has not materialized in 9 phases and ~25 real
agents — if one does, this is exactly the seam a LangGraph (or equivalent
checkpointed-graph) migration would target.

### 40. Session TTL (1800s) and max-stored-turns (20) are unvalidated placeholders

**What's deferred**: real usage data to confirm these numbers are right.

**Why**: both are reasonable v1 guesses (a half-hour of inactivity before
a session is considered abandoned; 20 turns is generous for what's
realistically a short conversational-BI exchange), not derived from any
real user behavior — same category as `query_cost_estimator.ROLE_ROW_LIMITS`
(item 24) and the z-score threshold (item 29).

**What full version requires**: real session-length/turn-count telemetry
once real users exist, then a confirmed (not guessed) value.

### 41. Multi-turn Clarification Coordinator triggers on exactly one narrow condition

**What's deferred**: a general ambiguity/low-confidence detector.

**Why**: the Clarification Coordinator is invoked ONLY when
`schema_mapping_result.tables == []` — a complete resolution failure, the
exact real shape `gq_007`/`gq_010` (item 38) hard-failed on in Phase 8.
A PARTIAL resolution (some `unmapped_terms` but at least one real table)
still proceeds to attempt an answer, even though the result may be
incomplete or the wrong shape for what the user actually meant. This is
deliberately narrow and additive (the same failure mode Phase 8 already
observed twice, now handled, nothing broader risked) rather than a general
low-confidence gate that could reject otherwise-answerable questions.

**What full version requires**: real usage data on how often a
"technically resolved, but probably wrong" partial answer actually
confuses users, before broadening the trigger condition is justified.

### 42. `data_source_id` auto-resolution requires exactly one match, with no "default" concept

**What's deferred**: a real `is_default` flag (or equivalent) on
`navigraph_catalog.DataSource`.

**Why**: the Request Orchestrator resolves `data_source_id` from
`tenant_id` via `list_data_sources` only when the caller omits one —
exactly one match is used automatically; zero or more than one is a
structured `outcome="failed"` (`failure_stage="orchestrator.data_source_resolution"`).
`navikenz-poc` is a real, concrete case of the "more than one" branch
(`fidelity_poc_snowflake` and `fidelity_poc_snowflake_v2`, see item 26) —
every real call in this phase's own verification had to supply
`data_source_id` explicitly to get past this. A real "default data
source" flag would be a new migration and a bigger surface change, out of
scope for this phase.

**What full version requires**: either resolve item 26 (reconcile the two
`navikenz-poc` registrations down to one) or add a real default-designation
field to `DataSource` — not yet decided.

### 43. Gateway → agent-runtime remains a real, un-collapsed HTTP hop

**What's deferred**: nothing is actually wrong here — this is a reminder,
not a gap. `packages/gateway` and `packages/agent_runtime` are two
separate containers/services (confirmed via `infra/docker-compose.yml`
during Phase 9 planning); `/ask` now POSTs to
`/agents/orchestrator/request_orchestrator/invoke` over real HTTP inside
the docker network, exactly like the one agent Phase 1.5's `/ask` called.
The "modular monolith" decision was always about the ~25 agents sharing
one process (`agent-runtime`), never about collapsing gateway into it —
logged here only so a future reader doesn't mistake the real HTTP hop for
an oversight.

### 44. Real findings from Phase 9's first live run of the Request Orchestrator (10/10 golden questions, real Anthropic model, real Snowflake)

**What was found** (pipeline success/answered rate 70%, up from Phase 8's
60% — see `eval/results/` for the full report):

- **A real, live-discovered join-inference bug, found and fixed mid-phase**
  (see item 15's "Real gap found and fixed in Phase 9" section for the
  full root-cause and fix): `gq_007` ("Which markets have the highest
  transaction volume?") — one of the two questions that hard-failed in
  Phase 8 — now resolves and **answers correctly** (correctness 5,
  groundedness 5, narrative_quality 4), a direct result of adding the
  missing `"Transaction happens in Market"` `RelationshipConcept`.
- **`gq_010` ("Is there anything unusual about a specific customer's
  transaction pattern?") now produces a real `needs_clarification`**
  outcome with a genuine, on-topic clarifying question, instead of Phase
  8's bare pipeline failure — exactly the target behavior Phase 9's
  Clarification Coordinator was built for.
- **Two real, correct PII rejections** (`gq_005`, `gq_009`, both touching
  `RISKLEVEL`) — same real Guardrail behavior as Phase 8, still working
  exactly as designed. Both reported `data_source_id=85db584d...` (the
  OLDER `fidelity_poc_snowflake` registration, not `_v2`) — a live,
  concrete confirmation of item 26's already-logged `DataSourceDiscoveryAgent`
  first-match ambiguity, not a new bug.
- **`gq_002`, `gq_004`, `gq_006`, `gq_008` all scored low (1-2 out of 5)**
  on correctness/groundedness — real, valuable signal about the current
  pipeline's real-world accuracy on aggregation-shape and comparison
  questions (matching Phase 8's already-logged `gq_002` `SUM`-vs-`COUNT`
  aggregation gap, item 38), not newly introduced by Phase 9's
  orchestrator wiring itself. `gq_008`'s intent classification, calibration
  gap-flagged in Phase 8 (`"unknown"` that run), classified correctly this
  run (`intent_match=True`) — real model non-determinism between runs on
  the same question, not a fix.

**Why this is logged as a single item**: same reasoning as item 38 — one
coherent event (this phase's first live orchestrator run), logged
together rather than fragmented. None of the low-scoring questions are
fixed here; they are the real signal a real harness run against a real
orchestrator was built to produce.

### 45. `AGENT_RUNTIME_URL` was a dead env var since Phase 1 — found and fixed in Phase 10

**What was wrong**: `infra/docker-compose.yml`'s `gateway` service set
`AGENT_RUNTIME_URL`, but `GatewaySettings.agent_runtime_base_url` actually
maps to `AGENT_RUNTIME_BASE_URL` (pydantic-settings uppercases the field
name) — the compose env var was silently never read, "working" only
because its default value happened to equal the intended one.

**Why it wasn't caught until now**: nothing ever set
`AGENT_RUNTIME_BASE_URL` to a DIFFERENT value than the default, so the
mismatch had zero observable effect until Phase 10 needed the exact same
config wired correctly into `infra/k8s/base/configmap-app-env.yaml`.

**Fix**: renamed to `AGENT_RUNTIME_BASE_URL` in both
`infra/docker-compose.yml` and the new K8s ConfigMap.

### 46. Terraform's Postgres Flexible Server module never created the real application database — found and fixed in Phase 10

**What was wrong**: `terraform/modules/postgres-flexible-server` created
only the server resource itself, leaving just its default `postgres`
database — every real `POSTGRES_DB=navigraph` connection
(`navigraph_catalog`, `navigraph_lineage`) would have failed against a
real, newly-applied server.

**Fix**: added a real `azurerm_postgresql_flexible_server_database`
resource (new `database_name` variable, default `"navigraph"`) to the
module. `terraform validate` confirmed clean; never applied.

### 47. Kustomize's default file-load sandbox required the standalone `kustomize` CLI, not plain `kubectl apply -k`

**What's deferred**: nothing — this is a real, resolved constraint, logged
so a future reader doesn't reach for `kubectl apply -k` directly and get a
confusing "file is not in or below" error.

**Why**: `infra/k8s/base/kustomization.yaml`'s `configMapGenerator`
entries deliberately pull real config directly from `infra/opa/`,
`infra/neo4j/`, `infra/prometheus/`, `infra/grafana/` (a single source of
truth, never duplicated into `infra/k8s/`) — but those paths are outside
`base/`'s own directory, which Kustomize's default security sandbox
(`LoadRestrictionsRootOnly`) forbids. `kubectl`'s embedded Kustomize
support has no flag to relax this; only the standalone `kustomize` CLI's
`--load-restrictor LoadRestrictionsNone` does.

**What full version requires**: nothing further — every real invocation
(`.github/workflows/{cd-deploy,k8s-manifests-ci}.yml`, `docs/runbooks/k8s-local-validation.md`)
already uses the standalone binary with this flag.

### 48. Real K8s manifest bugs found and fixed only by actually running a live `kind` cluster

**What was found**: six genuine, load-bearing bugs (PVC `storageClassName`
mismatch, `configMapGenerator` resources landing in the wrong namespace,
OPA's recursive directory scan hitting ConfigMap symlink duplication, a
probe-timeout/app-timeout race causing `web` `CrashLoopBackOff`, the
official neo4j image auto-translating a plain `NEO4J_PASSWORD` env var
into an invalid config setting, and a Kustomize patch silently dropping
required PVC fields) — none of which would have been caught by `terraform
validate`, `kustomize build`, or reading the YAML, only by actually
deploying to a real (if local) cluster and watching pods fail. Full
detail and fixes in `docs/runbooks/k8s-local-validation.md`'s "Real bugs
found and fixed" section.

**Why this matters beyond the fixes themselves**: it's the concrete,
lived reason Phase 10a's plan insisted on a real `kind` validation step
before ever touching Azure — `terraform validate`/`kustomize build`
succeeding is necessary but nowhere near sufficient evidence a K8s
deployment actually works.

### 49. A local, environment-specific `kind`/Docker-Desktop networking quirk (not a manifest defect)

**What was found**: during local `kind` validation, `agent-runtime` pods
became genuinely unreachable from every other pod over the real cluster
network (a fresh, unrelated debug pod also failed to reach it), while: the
app responded correctly on `localhost` from inside its own pod; kubelet's
own httpGet probes against the same pod IP succeeded continuously; and an
architecturally identical pod-to-pod path
(`ingress-nginx-controller` → `gateway-stable`/`web-stable`) worked
correctly at the same time. Restarting the affected pods did not resolve
it; Docker resource usage was not elevated.

**Why this is logged as a limitation, not silently worked around**: this
strongly points at a `kindnet`-on-Windows/Docker-Desktop/WSL2-specific
pod-routing flake — real AKS uses Azure CNI on real Linux nodes, an
entirely different networking stack, so this has no reason to recur in
Phase 10b. It is logged rather than chased further because continuing to
debug a local-only environment quirk would not have improved the actual
deliverable (the K8s manifests themselves, which were separately,
thoroughly proven correct via the ingress/canary tests). See
`docs/runbooks/k8s-local-validation.md`'s own note on this for
troubleshooting if it recurs.

**What full version requires**: nothing from this codebase — if this
recurs during a future local validation session, try recreating the
affected pods or the whole `kind` cluster; if it ever reproduces against
real AKS in Phase 10b, that would be a genuinely new, real finding
worth its own investigation, not a repeat of this one.

### 50. Secret scoping is genuinely per-service, not just apparent — a design improvement over the original technical plan

**What's resolved, not deferred**: the original Phase 10 technical design
(from the Plan agent's initial draft) proposed one shared
`navigraph-app-secrets` Kubernetes `Secret` name synced by every
`SecretProviderClass`, which would have made real per-service secret
scoping impossible (multiple `SecretProviderClass` resources all writing
to the same Secret object name would stomp on each other, and any pod
reading that Secret could see every other service's values). The actual
implementation instead gives each service its own Secret name
(`agent-runtime-secrets`, `neo4j-secrets`, `grafana-secrets`), and
`gateway`/`web` (which need zero secret values) get no
`SecretProviderClass` or CSI volume at all.

**Why this is logged**: `tests/security/cloud/test_secret_provider_scoping.py`
was originally anticipated (by the initial technical design) to find and
report a real scoping gap — instead it's real, passing proof the scoping
already works correctly. Logged here so the discrepancy between the
original design narrative and the actual implementation is explicit, not
silently absorbed.

**Real, still-open caveat**: all `SecretProviderClass` resources in
`overlays/dev` share ONE AKS-managed addon identity (the
`key_vault_secrets_provider` addon), not per-pod Azure Workload Identity
federation. The real isolation boundary is therefore "which secret names
each `SecretProviderClass` declares" (which IS correctly scoped, per
above), not a hard per-pod Azure-identity wall — anyone who can create or
modify a `SecretProviderClass`/`Pod` in the `navigraph` namespace could, in
principle, declare a new one requesting a different service's secret
names. This ties directly into item 51's RBAC gap below, not a separate
hole.

### 51. No AAD-integrated Kubernetes RBAC in `dev` — a real, deliberate scope limit

**What's deferred**: namespace-scoped Kubernetes RBAC tied to real Azure
AD identities. `terraform/modules/aks` has no
`azure_active_directory_role_based_access_control` block — the cluster
uses local Kubernetes accounts, so once any identity can fetch a
kubeconfig at all (granted via the real `Azure Kubernetes Service Cluster
User Role` `azurerm_role_assignment` this phase added for the CI service
principal), it is effectively cluster-admin.

**Why**: real AAD-integrated K8s RBAC is a meaningfully larger scope
addition (Azure AD group-to-Kubernetes-Role bindings, a real access review
process) than Phase 10's stated deployment-mechanics goal. Deferring it
here mirrors this project's existing precedent of naming a real gap
rather than silently working around it (see item 23's identical framing
for gateway-level Azure AD token verification).

**What full version requires**: a real AAD-integrated RBAC design, plus a
decision on which real Azure AD groups map to which Kubernetes Roles —
not yet started. `tests/security/cloud/test_rbac_least_privilege.py`
proves and documents this exact gap against the real live cluster rather
than assuming it, and is EXPECTED to keep passing (i.e. keep finding
`cluster-admin`-equivalent access) until this is addressed — a future
passing-differently result there is the correct signal to revisit this
item, not a test regression to chase.

### 52. Trino excluded from the cloud deployment; domain/TLS and node sizing left as placeholders

**What's deferred**: Trino is fully built, unit-tested, and still the
non-default execution route (see items 3/19) — Phase 10's real AKS
deployment deliberately excludes it entirely (confirmed with the user)
to avoid real Azure cost/attack-surface for a route nothing actually uses
yet. It remains fully available in local `docker-compose` for continued
dev/testing of the route itself.

**Also still open, flagged rather than guessed**: no real domain name has
been decided yet — `overlays/dev/ingress-patch.yaml` uses a
`REPLACE_AFTER_APPLY_DOMAIN` placeholder, and cert-manager/Let's Encrypt
setup is deferred until a real, DNS-resolvable hostname exists (Let's
Encrypt's HTTP01 challenge cannot validate a placeholder domain).

**UPDATE 2026-07-30**: AKS node sizing/region are no longer the original
defaults — see item 53 for what changed and why, discovered during the
real Phase 10b `terraform apply`.

**What full version requires**: the user supplying a real domain (or
confirming a temporary `nip.io`-style scheme is acceptable) before Phase
10b's cluster bootstrap step.

### 53. Real Phase 10b `terraform apply` required several subscription-specific fixes not knowable from `plan` alone

**What happened**: the first real subscription (`navikenz.com`'s "Dev
subscription") turned out to lack the Contributor role needed for
`terraform apply` at all (`az login` and `plan` don't require it, so this
only surfaced at `apply` time) — resolved by switching to a different,
real Azure subscription (a personal account, auto-Owner on its own
subscription) the user provided, rather than waiting on an org admin
grant. A fresh `navigraph-cd` app registration + service principal had to
be recreated in the new tenant (the one created in navikenz.com's tenant
is now orphaned there — harmless, zero cost, not cleaned up since this
session has no reason to delete resources in a tenant we've moved away
from).

Once pointed at the new (freshly created, "Azure subscription 1")
subscription, three more real, subscription-specific restrictions
surfaced only during `apply`, none visible from `plan` or `validate`:

1. **AKS**: `Standard_D2s_v5` (the original default) is not in this
   subscription's allowed VM size list for `eastus` — Azure returned the
   real allowed list in the error; `Standard_D2s_v7` (closest general-
   purpose equivalent) is on it. `terraform/environments/dev/main.tf`'s
   `vm_size` was changed accordingly.
2. **Postgres Flexible Server**: this subscription is offer-restricted
   from provisioning that service in `eastus` *and* `eastus2` (both
   confirmed via real `LocationIsOfferRestricted` errors). A real,
   immediately-cleaned-up probe deployment across 7 candidate regions
   confirmed `centralus`/`northeurope`/`uksouth`/`australiaeast` all work
   on this subscription; `centralus` was chosen (closest to the rest of
   the environment's `eastus` resources) via a new `postgres_region`
   Terraform variable, separate from the shared `region` variable — a
   resource group is just a management container, so this is a
   structurally normal split, not a workaround.
3. **AKS OIDC issuer**: Azure enables the OIDC issuer by default on new
   AKS clusters regardless of what's requested, and its API rejects any
   attempt to disable it once on. The module never declared
   `oidc_issuer_enabled` at all, so every subsequent `plan` tried (and
   the first retry attempt actually failed while trying) to turn it off.
   Fixed by declaring `oidc_issuer_enabled = true` explicitly in
   `terraform/modules/aks/main.tf`, matching the real cluster's actual
   state.

Additionally, the azurerm provider's default behavior of trying to
auto-register ~200+ resource providers (including ones this config never
uses, e.g. `Microsoft.DataMigration`) timed out on this fresh subscription
mid-`plan`. Fixed by setting `skip_provider_registration = true` on the
provider block and registering only the 8 providers this config's
modules actually reference (`az provider register`, done once, out of
band) — see `terraform/environments/dev/providers.tf`'s comment for the
full list and rationale.

**Why this matters going forward**: none of these four issues were
visible in `terraform validate`, `terraform fmt`, or even `terraform
plan` — they only surfaced when `apply` actually tried to create
resources against this specific subscription's real, non-obvious
restrictions. A different Azure subscription (a paid enterprise
subscription, for instance) may not hit any of these and may have
entirely different restrictions of its own. This is not something to
generalize into "the Terraform is now portable to any subscription" —
it's evidence that `plan`'s cleanliness does not guarantee `apply`
succeeds unmodified on a fresh subscription, and any future subscription
change should expect to re-discover a similar set of subscription-
specific quirks.

**Real, live infrastructure now exists** as of 2026-07-30 in Azure
subscription `1ddb263f-0966-4ffa-9ce5-1b4aa7b01598` ("Azure subscription
1"): resource group, VNet/subnet, ACR, AKS (2 nodes, confirmed `Ready`
via a real `kubectl get nodes`), Key Vault, Postgres Flexible Server +
database, an Entra app registration + service principal, and 3 role
assignments. This is genuinely billable infrastructure, not a plan
preview.

### 54. RESOLVED: `terraform output -json` briefly exposed a real AKS cluster credential

**What happened**: while fetching non-sensitive Terraform outputs during
cluster bootstrap, `terraform output -json` was run instead of querying a
specific output by name. Unlike the plain table view, `-json` output does
not respect the `sensitive` flag -- it printed the full real
`clusterUser_navigraph-dev-rg_navigraph-dev-aks` kubeconfig (client
certificate, private key, and bearer token) into this session. Since this
cluster has no AAD-integrated RBAC yet (item 51), that credential was
cluster-admin-equivalent.

**Resolution**: the file the value had been written to was deleted
immediately, and `az aks rotate-certs` was run (with the user's explicit
confirmation) to invalidate the exposed certificate/key/token before any
further cluster work continued. Going forward, only `terraform output
-raw <specific-output-name>` is used for non-sensitive values -- never
`-json` or a bare `terraform output` against this environment.

### 55. RESOLVED: Key Vault had RBAC authorization disabled, silently nullifying its own role assignments

**What was found**: `terraform/modules/key-vault/main.tf` never set
`enable_rbac_authorization`, which defaults to `false` (the legacy
access-policy model). This was discovered live, via `az keyvault show`,
while trying to populate real secrets: the vault had zero access
policies AND `enableRbacAuthorization: false`, meaning the
`azurerm_role_assignment.aks_key_vault_secrets_user` grant (Key Vault
Secrets User, RBAC role) created in `environments/dev/main.tf` had been
silently granting **nothing** -- in access-policy mode, Azure RBAC role
assignments on a vault's data plane are simply ignored. Had this gone
unnoticed, the AKS Secrets Store CSI driver would have failed to sync
any real secret once deployed, likely surfacing as a confusing pod-level
error far from the actual root cause.

**Resolution**: added `enable_rbac_authorization = true` to the module,
applied via a real, reviewed `terraform plan`/`apply` (2 resources
changed: the Key Vault flag itself, plus an unrelated cosmetic
`kube_config` drift from the cert rotation above). Also discovered that
subscription-level Owner does **not** automatically resolve as Key Vault
data-plane access even once RBAC mode is on (a real `ForbiddenByRbac`
persisted); a direct `Key Vault Secrets Officer` role assignment scoped
to the vault was required for the human operator to populate secrets.

### 56. RESOLVED: AKS had no ACR pull access, only the CI principal had push access

**What was found**: every application pod (`gateway`, `agent-runtime`,
`web`, all canary tracks) failed with `ImagePullBackOff` on the real
first deploy. Terraform's `ci_acr_push` role assignment only grants the
`navigraph-cd` CI service principal push rights -- it says nothing about
the AKS cluster's own kubelet identity actually being able to *pull*
images at runtime, which is a separate, required grant.

**Resolution**: `az aks update --attach-acr navigraphdevacr` (the
standard AKS/ACR integration command, which grants the cluster's kubelet
identity `AcrPull` on the registry). Not yet ported into Terraform as a
declarative `azurerm_role_assignment` -- currently a manual, imperative
step; a future pass should add this to `environments/dev/main.tf` so a
fresh `terraform apply` doesn't silently omit it again.

### 57. RESOLVED: Grafana and Prometheus crashed on real Azure Disk-backed PVCs (missing `fsGroup`)

**What was found**: both crashed on first real deploy --
`GF_PATHS_DATA='/var/lib/grafana' is not writable` (Grafana) and `open
/prometheus/queries.active: permission denied` (Prometheus). Neither
`infra/k8s/base/grafana/deployment.yaml` nor
`infra/k8s/base/prometheus/deployment.yaml` set a Pod `securityContext`,
so Kubernetes never chowned the freshly attached Azure Disk volume to
match each image's non-root container user (Grafana: UID/GID 472;
Prometheus: UID/GID 65534). This went undetected in every prior local
`kind` validation because `kind`'s local-path storage class behaves more
permissively than a real formatted block device.

**Resolution**: added `securityContext.fsGroup` (472 for Grafana, 65534
for Prometheus) to both base Deployments -- a base-manifest fix, so it
applies to any future environment using real (non-`kind`) persistent
storage, not just this one.

### 58. RESOLVED: `ingress-patch.yaml`'s strategic-merge patch silently deleted every Ingress's backend

**What was found**: after the two fixes above, applying the dev overlay
still failed -- nginx's admission webhook panicked (nil pointer in
`mergeAlternativeBackends`) on every Ingress create/update, taking down
the whole `kubectl apply`. Inspecting the actual rendered manifest (not
just the patch source) revealed the real cause:
`Ingress.spec.rules` has no Kubernetes-defined patch merge key, so
`ingress-patch.yaml`'s partial patch (only supplying `host`) replaced
the ENTIRE `rules` array rather than merging into it -- silently
deleting each base Ingress's `http.paths.backend` entirely. The result
was a structurally invalid Ingress (a host with no backend at all),
which crashed nginx's canary-merge logic rather than failing with a
clear validation error. This is the exact same list-replacement gotcha
already hit once in this project for `StatefulSet.spec.volumeClaimTemplates`
(item 48 #6) -- now confirmed to generalize to any Kubernetes list field
without a merge key, not just that one case.

This went undetected through all of Phase 10a's local `kind` validation
because that runbook only ever exercised the `kind` overlay, which has
no `ingress-patch.yaml` of its own -- this patch is dev-overlay-only and
had never actually been applied to a real cluster until this session.

**Resolution**: rewrote `ingress-patch.yaml` to repeat the FULL `rules`
block (host + complete `http.paths.backend`) for all four Ingress
objects, not just the changed `host` field. **Generalized takeaway for
this codebase**: any strategic-merge Kustomize patch touching a list
field must repeat the full list item, never just the changed field --
true for `volumeClaimTemplates`, `rules`, and likely any other bare list
in this manifest tree that isn't explicitly reviewed against this
pattern.

### 59. RESOLVED: a `%` in a real password broke ConfigParser-based Alembic migrations

**What was found**: running `alembic upgrade head` against the real
cloud Postgres for the first time crashed with `ValueError: invalid
interpolation syntax`. `alembic`'s `Config` object is backed by Python's
`ConfigParser`, which treats a bare `%` as the start of an interpolation
token -- a real, valid, randomly-generated password containing `%` broke
`config.set_main_option("sqlalchemy.url", ...)` in both
`packages/metadata_catalog/migrations/env.py` and
`packages/lineage/migrations/env.py`. Neither had ever been run against
a real Postgres server with a password containing `%` before (local
`docker-compose` and prior phases' passwords happened not to).

**Resolution**: both `env.py` files now call `.replace("%", "%%")` on
the built URL before passing it to `set_main_option`, escaping any
literal percent signs. A real, generalizable lesson: any future
`ConfigParser`-based settings loading in this codebase should assume
password values may contain `%` and escape accordingly.

**Also found in the same investigation**: this triggering error message
itself printed the real Postgres password in plaintext (embedded in the
connection URL inside the traceback) directly into the working session
-- caught immediately, and the password was rotated a second time as a
result (see the real, live incident record in `DECISIONS.md`).

### 60. RESOLVED: no NetworkPolicy actually allowed agent-runtime to reach the real external Postgres

**What was found**: `infra/k8s/base/networkpolicy-allow.yaml`'s
`allow-agent-runtime-to-datastores` policy's own comment claimed real
Postgres egress was "covered by `allow-agent-runtime-external-https`
below instead" -- but that policy only opens port 443 (HTTPS, for
Snowflake/Anthropic), not port 5432. No policy in the manifest tree
actually allowed egress to Postgres's real external endpoint at all;
every real connection attempt from `agent-runtime` timed out silently
(the same symptom a firewall-rule gap would produce, which delayed
finding this -- see item 61's Terraform fix, applied first and found
insufficient on its own).

**Resolution**: added a new, correctly-scoped
`allow-agent-runtime-external-postgres` NetworkPolicy (same
broad-CIDR-minus-private-ranges pattern as the HTTPS one, port 5432
instead of 443) and corrected the misleading comment on the
in-cluster-only `postgres` podSelector rule it had wrongly assumed
covered this case.

### 61. RESOLVED: Postgres Flexible Server had zero firewall rules

**What was found**: `terraform/modules/postgres-flexible-server` set
`public_network_access_enabled` implicitly but never created any
`azurerm_postgresql_flexible_server_firewall_rule` -- Azure Postgres
Flexible Server requires an explicit firewall rule before any
connection succeeds regardless of that setting. Every real connection
attempt (including from AKS pods on the same VNet, since this module
has no VNet integration/private endpoint) timed out.

**Resolution**: added an `azurerm_postgresql_flexible_server_firewall_rule`
resource using Azure's documented `"0.0.0.0"`-`"0.0.0.0"` special
convention ("allow public access from any Azure service"), applied via
a real, reviewed `terraform plan`/`apply`. Combined with item 60's
NetworkPolicy fix, real Postgres connectivity from AKS now works end to
end (confirmed via real Alembic migrations reaching revision head).

### 62. RESOLVED: Snowflake OCSP checks retried for ~90s per connection (blocked by NetworkPolicy)

**What was found**: real Snowflake connections from the cloud
agent-runtime pods took ~90 seconds each -- not a slow first connection
as initially assumed, but the Snowflake Python connector retrying OCSP
(certificate revocation) checks against `ocsp.snowflakecomputing.com`/
`ocsp.digicert.com` over plain HTTP (port 80) on *every* connection, each
attempt failing with "Network is unreachable" since only ports 443/5432
were allowed. OCSP is fail-open (a blocked check doesn't fail the
connection), so real crawls/queries always eventually succeeded, just
slowly -- multiplied across ~50+ Snowflake connections in a full
eval-harness run, this would have added 20-40+ minutes of pure waste.

**Resolution**: added port 80 to `allow-agent-runtime-external-https`
(see the updated comment in `infra/k8s/base/networkpolicy-allow.yaml`).
This is a genuine security *improvement*, not a tradeoff: it lets
certificate-revocation checking actually succeed instead of silently
failing closed-by-network-block, unlike disabling OCSP outright (e.g.
via `insecure_mode=True`) would have been -- that option was deliberately
not used specifically because it would trade away a real security
control under time pressure without adversarial verification.

### 63. NOT YET FIXED: large (10k-row) result sets cause LLM-backed Insight steps to fail or malform

**What was found**: the first real eval-harness run against the cloud
environment (with the real, full `fidelity_poc_snowflake_v2` dataset
crawled in Phase 10b) surfaced a real, reproducible pattern that no
prior phase's smaller worked examples ever exercised: every golden
question whose `final_row_count` was small (16, 1 rows) scored cleanly
(5/5/5, 5/5/5); every question with `final_row_count=10000` degraded --
two got `correctness=groundedness=narrative_quality=1` with rationale
`"judge response could not be parsed"` (`ops.evaluation_judge`), and one
(`gq_004`, an `anomaly_investigation` question) got a completely empty
narrative string with `narrative_llm_response_malformed`
(`insight.grounded_narrative_generation`).

**Root cause identified (not yet fixed)**: `grounded_narrative_generation/agent.py`
already caps `final_rows` at 200 before rendering into the LLM prompt
(`_MAX_ROWS_IN_PROMPT`), but serializes `payload.anomalies` **uncapped**
(`f"Anomalies: {json.dumps([a.model_dump() for a in payload.anomalies])}"`)
-- a 10,000-row anomaly-investigation query can produce hundreds of
z-score>2.0 findings (statistically ~2.5% of a large enough population),
producing a prompt large enough to plausibly exceed a sane limit and
provoke a malformed/truncated model response. `ops.evaluation_judge`'s
own prompt construction likely has the same class of gap (not yet
inspected in equal depth).

**Why this is logged, not fixed, right now**: the real Anthropic API key
used for this run hit its own usage limit mid-run (`gq_006`-`gq_010` all
failed with a real `400 invalid_request_error`, "You have reached your
specified API usage limits. You will regain access on 2026-08-01 at
00:00 UTC") -- a genuine external constraint, not a NaviGraph bug. This
project's own discipline requires a real, adversarial test before
marking any fix done; attempting a blind patch to the anomalies-capping
logic without being able to verify it against a real LLM call until the
quota resets would violate that. **What full version requires**: cap
`payload.anomalies` the same way `final_rows` already is (e.g. the top-N
anomalies by `|z_score|`), apply the same review to
`ops.evaluation_judge`'s prompt construction, then re-run the full
10-question eval harness for real once Anthropic API access is
available again to confirm the fix.

### 64. RESOLVED: gateway had no NetworkPolicy egress to agent-runtime -- the real `/ask` path was broken on real AKS

**What was found**: the very first real run of
`tests/security/cloud/test_network_policy_isolation.py`'s positive
control (`test_positive_control_gateway_can_still_reach_agent_runtime`)
against the real cluster failed. Manual reproduction confirmed it was
real, not a test artifact: `kubectl exec` into `gateway-stable` and
attempting `http://agent-runtime.navigraph.svc.cluster.local:8001/healthz`
(or the raw pod IP, same-node, bypassing DNS/Service routing entirely)
both timed out consistently, while `agent-runtime`'s own kubelet
readiness/liveness probes and localhost-within-the-pod checks succeeded
continuously -- initially indistinguishable from item 49's `kindnet`
quirk, except this time on real, NetworkPolicy-enforcing Azure CNI.

**Root cause**: `allow-gateway-to-agent-runtime` in
`infra/k8s/base/networkpolicy-allow.yaml` only ever declared the
*ingress* half of this traffic (an Ingress-type policy attached to
`agent-runtime` pods, allowing traffic in from `gateway`). `default-deny-all`
denies Egress by default for every pod in the namespace including
`gateway`, and no policy anywhere granted `gateway` pods the matching
*egress* half. Kubernetes NetworkPolicy requires both the sender's
egress and the receiver's ingress to independently permit a connection
-- one-sided declaration is a real, silent gap, not a redundant
belt-and-suspenders. Confirmed this wasn't a broader network failure by
testing `gateway` -> `web-stable` (also blocked, same missing-egress
pattern) before concluding the fix, and confirmed `allow-web-to-gateway`
(the reverse pair) was already correctly declared as an Egress-type
policy on `web`, so this asymmetry was specific to the gateway/
agent-runtime pair, not systemic.

**Why this matters**: this is precisely the real, load-bearing bug this
project's whole cloud-security-test investment exists to catch. It went
completely undetected through every phase of local `kind` validation
because `kindnet` never enforces NetworkPolicy at all (confirmed in item
49) -- meaning the real, public-facing `/ask` endpoint's actual backend
call (gateway -> agent-runtime) would have been silently broken on any
real AKS deployment until the first real user request failed, had this
adversarial test not existed and been run for real against this cluster.

**Resolution**: added `allow-gateway-egress-to-agent-runtime`, a
correctly-scoped Egress-type policy on `gateway` pods. Verified twice,
independently: (1) the positive-control test now passes, and (2) a real
`POST /ask` against the live public `https://api.navigraph.51-8-46-125.nip.io/ask`
endpoint now reaches agent-runtime and returns a real, structured
response (failing only on the separate, already-diagnosed Anthropic API
quota exhaustion in item 63/eval harness -- not a network error).

### 65. RESOLVED: CI had never actually run once, real since this repo's first-ever GitHub push -- six independent real bugs found and fixed

**What was found**: this repository was only pushed to GitHub for the
first time during Phase 10b -- every workflow in `.github/workflows/` had
literally zero real executions before that (confirmed via
`gh run list`, which showed every single historical run across all four
workflows as `failure`). Root-causing each one for real (`gh run view
--log-failed`, and, for the two that produced zero jobs at all, comparing
the YAML against GitHub's actual context-availability rules) surfaced six
distinct, independent bugs, found across three separate rounds of pushing
a fix and re-checking the real run -- none of which any local
`pytest packages/`/`npm test` run, `terraform validate`, or `kustomize
build` had ever been positioned to catch, since none of those run against
a clean CI checkout the way a real GitHub Actions runner does:

1. **`ci.yml`'s Python job never installed `agent_runtime`'s real
   dependencies.** The "Install workspace packages" step only ever listed
   Phase 1's original three packages (`shared`, `gateway`,
   `agent_runtime`) -- Phases 4/5/8 added real `agent_runtime` dependencies
   on `connector_sdk`/`metadata_catalog`/`knowledge_graph`/`federation`/
   `lineage`, none of which were ever added to this list, so
   `pip install -e packages/agent_runtime` always failed to resolve on a
   clean install. Fixed by installing all 8 packages in the exact
   dependency order already proven correct in
   `packages/agent_runtime/Dockerfile`.
2. **`ci.yml`'s Node job never installed the Playwright browser
   binary.** `npx playwright install` is a separate download from `npm
   ci`, never run anywhere in the workflow, so `npm run test` (`playwright
   test`) failed immediately with "Executable doesn't exist ... 
   chrome-headless-shell". Fixed by adding an explicit
   `npx playwright install --with-deps chromium` step before `npm run
   test`.
3. **`terraform-plan.yml`'s `plan` job and `cloud-security-tests.yml`'s
   `adversarial-tests` job both referenced `secrets.AZURE_CLIENT_ID`
   directly inside a job-level `if:`.** GitHub does not allow the
   `secrets` context in `jobs.<job_id>.if` at all (only `github`/`needs`/
   `vars`/`inputs` are permitted there -- `secrets` is only readable inside
   a step's own `env`/`run`/`with`); this made both workflow FILES
   themselves invalid, so GitHub rejected them outright with zero jobs
   ever scheduled on ANY trigger (`gh run view` reported "This run likely
   failed because of a workflow file issue" and 0 jobs, confirmed via
   `gh api .../actions/runs/{id}/jobs` returning `{"jobs": []}`) --
   completely independent of whichever event actually triggered the
   attempt. Fixed in both files by moving the secret check into a new,
   preliminary `check-azure-creds` job (a step reads
   `secrets.AZURE_CLIENT_ID` into an output there, where `secrets` IS
   allowed), and having the downstream job's `if:` reference
   `needs.check-azure-creds.outputs.configured` instead (`needs` IS
   allowed at the job level).
4. **`ruff check .` failed on 3 real `EXE001` violations**
   (`tools/scripts/canary_gate.py`, `tools/scripts/new-agent.py`,
   `tools/scripts/tag_pii_columns.py` -- each has a real `#!/usr/bin/env
   python3` shebang but was never marked executable in git). This was
   invisible on this Windows dev machine (NTFS has no POSIX executable
   bit for `ruff` to check locally in the same way), only surfacing on a
   real Linux CI runner. Fixed via `git update-index --chmod=+x` on all
   three files (a real, git-tracked mode change, not a filesystem-only
   `chmod` that Windows would just silently drop again).
5. **`web/playwright.config.ts` had no `webServer` block at all**, so
   `playwright test` on a clean CI runner (nothing else started) failed
   immediately with `ERR_CONNECTION_REFUSED` at `localhost:3000`. This
   test had only ever been run locally against an already-running
   docker-compose `web` service or a manually-started `next dev` --
   never against a genuinely clean environment. Fixed by adding a real
   `webServer: { command: "npm run dev", url: baseURL, reuseExistingServer:
   !process.env.CI, timeout: 120_000 }` block, confirmed safe against
   `web/src/lib/env.ts` (every env var Next.js needs at build/runtime
   already has a permissive fallback, so a bare `next dev` boots cleanly
   with zero configuration).
6. **`mypy packages/` failed with a real `Duplicate module named "tests"`
   error**, only visible once every package that has its own
   `tests/__init__.py` (`connector_sdk`, `federation`, `knowledge_graph`,
   `lineage`, `metadata_catalog`) was swept in one `mypy` invocation --
   none of them has a root `__init__.py`, so mypy's module-name inference
   (walk up to the nearest ancestor lacking `__init__.py`) collapses every
   one of those `tests/` packages to the bare, colliding module name
   `"tests"`. Fixing that one collision surfaced a second, identically-
   shaped one: `metadata_catalog` and `lineage` each have their own
   `migrations/versions/0001_initial_schema.py` (same revision filename,
   independent Alembic chains -- see `DECISIONS.md`), which collide the
   same way. Reproduced and fixed locally first (`mypy packages/`, then
   confirmed clean with the fix) before touching CI. Fixed by excluding
   both directory patterns from this one repo-wide sweep
   (`mypy --exclude '(^|[\\/])(tests|migrations)([\\/]|$)' packages/`) --
   each package's own tests are still fully exercised by the very next
   step (`pytest packages/`), and Alembic migration scripts are
   standalone, alembic-run files never meant to be cross-checked against
   sibling packages' revisions.

**Why this is logged as a single item**: all six were discovered by the
same event (this repo's first real push to GitHub, and the resulting
first real CI executions) and root-caused together across one
investigation -- logging them individually would fragment one coherent
finding, the same reasoning already used for items 38/44.

**What full version requires**: nothing further planned -- all six are
real, fixed bugs, not deferred scope. The one remaining open action is
confirming (not yet done as of this writing) that the fixes actually
turn CI/`terraform-plan`/`cloud-security-tests` green on a real run,
since a fix that looks correct by inspection is not the same as one
proven against the real GitHub Actions runner -- exactly the standard
this whole item's discovery already demonstrated is necessary.
