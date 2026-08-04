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

### 63. RESOLVED: large (10k-row) result sets cause LLM-backed Insight steps to fail or malform

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

**Why this took until now to fix**: the real Anthropic API key used for
the original run hit its own usage limit mid-run ("You have reached your
specified API usage limits. You will regain access on 2026-08-01 at
00:00 UTC") -- a genuine external constraint, not a NaviGraph bug. This
project's own discipline requires a real, adversarial test before marking
any fix done, so the anomalies-capping logic was deliberately left
unpatched until the quota reset and a real re-run could confirm it. Once
the quota reset (confirmed live via a real `client.messages.create` call
from inside the `agent-runtime` pod), the fix was implemented and
verified for real -- see the full root-cause and resolution recorded as
two commits: "Cap uncapped anomalies/rows in LLM prompts" and "Retry once
on a genuine empty-text LLM response."

**Resolution, part 1 (the originally-diagnosed bug)**: capped
`payload.anomalies` to the top-20 by `|z_score|` in
`insight.grounded_narrative_generation`, `insight.follow_up_suggestion`,
and `ops.evaluation_judge` (which also had `final_rows` uncapped -- a
more serious gap, since an oversized *judge* prompt degrades the eval
harness's own signal, not just a user-facing narrative). Citation
validation in `grounded_narrative_generation` still checks the FULL
`anomalies` list, never just the capped prompt view -- verified with a
real unit test proving an out-of-top-20 citation still validates
correctly. **Directly confirmed against the real model**: `gq_008`
(320 rows, previously an empty/malformed narrative) produced a full,
correctly-cited real narrative in every post-fix run; a direct synthetic
reproduction of `gq_004`'s original data shape (10,000 rows, a few
dominant spikes) also succeeded once capped, whereas the same data
uncapped was never tested pre-fix (no raw-response capture existed yet).

**Resolution, part 2 (a second, real, independent bug found while
re-verifying part 1)**: even after capping, `gq_004` kept failing.
Temporary raw-response debug logging (hot-patched directly into the live
pod for diagnosis only, never committed) revealed the real cause: the
live Anthropic API was occasionally returning a genuine HTTP 200 response
-- real `usage`, no error -- with **zero text content blocks**, i.e. a
truly empty completion, unrelated to prompt size (a direct synthetic
reproduction with an identical data shape succeeded cleanly, and a bare
re-run of the exact same real `gq_004` question, no code change,
immediately after scored a perfect 5/5/5). Fixed by retrying the
identical request exactly once in `AnthropicLLMClient.complete()` when
`text` comes back empty, verified with 3 new unit tests against a real
`httpx.MockTransport` (normal case, retry-succeeds, retry-still-empty).

**Final real re-verification (full 10-question harness, both fixes
deployed via the real, now-fully-proven `cd-deploy.yml` pipeline)**:
`gq_004` scored `correctness=4 groundedness=5 narrative_quality=4` -- a
complete recovery from its original `1/1/1` empty-narrative failure.
**Honest residual, not glossed over**: `gq_008` scored `1/1/1` in this
same final run, again with `narrative_llm_response_malformed` and an
empty narrative -- the retry does not GUARANTEE recovery, only reduce
the odds of a double failure, since this is inherent, non-deterministic
live-model behavior (matching the already-logged item 38 finding that
"the judge model's own occasional malformed-JSON response rate isn't
zero either," now confirmed to generalize to any LLM-backed agent call,
not just the judge). This residual is deliberately left as-is: a second
retry would only narrow the window further at real additional cost/
latency, and this project's discipline is to report real, honest
results rather than engineer away every last occurrence of inherent model
non-determinism.

**What full version requires**: nothing further planned for the
originally-diagnosed bug (fully resolved and verified). If the residual
occasional-empty-completion rate proves too high in real practice, a
second bounded retry (or a shorter, more targeted prompt for
anomaly-heavy questions specifically) would be the next lever -- not
implemented speculatively here.

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

**What full version requires**: nothing further -- all six are real,
fixed bugs, not deferred scope, and the fix is now proven for real:
`gh run view` on commit `099650c`'s `CI` run
(`https://github.com/Ritesh34347/NaviGraphMVP/actions/runs/30560655356`)
shows both jobs (`Python lint, type-check, and test`,
`Node lint, type-check, and test`) as real `success` -- the first fully
green CI run in this repository's history. `terraform-plan.yml` and
`cloud-security-tests.yml` also confirmed correct post-fix: neither
produced a spurious "invalid workflow file" run on this same push,
proving bug 3's fix holds (they correctly only trigger on
`pull_request`/`workflow_run` now, not on an ordinary push to `main`).

### 66. RESOLVED: `cd-deploy.yml`'s first real trigger failed at Azure login -- the AZURE_CLIENT_ID/AZURE_TENANT_ID/AZURE_SUBSCRIPTION_ID GitHub secrets were not actually configured on this repo

**What was found**: the same push that finally turned CI green also gave
`cd-deploy.yml` its first-ever real trigger (it runs on every push to
`main`). It failed immediately at the `Azure login (OIDC)` step with
`Login failed with Error: Using auth-type: SERVICE_PRINCIPAL. Not all
values are present. Ensure 'client-id' and 'tenant-id' are supplied.` --
i.e. `secrets.AZURE_CLIENT_ID`/`AZURE_TENANT_ID`/`AZURE_SUBSCRIPTION_ID`
resolved empty at the real Azure/login@v2 step. `gh api
repos/.../environments` confirmed there are zero GitHub Environments on
this repo, ruling out "they're environment-scoped, not repo-scoped
secrets" as the explanation -- the secrets simply are not set at all.

**Why this is surfaced as a limitation, not silently fixed**: this
directly contradicts an earlier record of the federated-credential/
secrets wiring being complete. Setting these secrets requires looking up
the real `navigraph-cd` app registration's client ID (via `az ad app
list` against the real tenant) and running `gh secret set` against the
real GitHub repo -- a standing, security-relevant configuration change to
a shared system, not a local file edit, so it needs the user's explicit
go-ahead rather than being assumed as in-scope for "confirm CI passes."

**Resolution**: confirmed with the user, then looked up the real
`navigraph-cd` app registration's `appId` (`cbc3e5d0-dbf0-423f-ac76-c553c939b1a2`,
distinct from `terraform.tfvars`'s `ci_service_principal_object_id`, which
is the *object* ID -- `azure/login@v2` needs the `appId`/client ID
instead) via a fresh `az ad app list` lookup, confirmed the existing
federated credential's subject (`repo:Ritesh34347/NaviGraphMVP:ref:refs/heads/main`)
was already correctly scoped for `cd-deploy.yml`'s `push: main` trigger,
then set all three secrets. This also surfaced two real PAT-scope gaps in
the process (the fine-grained token used for `gh` this session lacked
both "Secrets" and "Actions" repository permissions) -- both required the
user to edit the token's permissions before `gh secret set` and
`gh workflow run` would succeed. Verified for real: re-triggering
`cd-deploy.yml` got past the `Azure login (OIDC)` step this time (it
progressed to the actual image build/push steps) -- see item 67 for the
next real bug this surfaced.

### 67. RESOLVED: `cd-deploy.yml`, `terraform-plan.yml`, and `cloud-security-tests.yml` were all missing `permissions: id-token: write`, required for Azure OIDC login

**What was found**: with item 66's secrets finally in place,
`cd-deploy.yml`'s real, manually-dispatched run (`gh workflow run
cd-deploy.yml`) got past the "not all values are present" error but
failed at the exact same `Azure login (OIDC)` step with a different,
real error: `Failed to fetch federated token from GitHub. Please make
sure to give write permissions to id-token in the workflow.` --
`azure/login@v2`'s OIDC (`auth-type: SERVICE_PRINCIPAL` with no client
secret) flow requires GitHub to mint a short-lived `id-token` for the
job, which only happens when the job's *effective* permissions grant
`id-token: write` -- the default is read-only, and nothing in the file
declared it. All 3 real matrix jobs (`navigraph-gateway`,
`navigraph-agent-runtime`, `navigraph-web`) hit this identically;
`navigraph-web` surfaced it first (fastest job), which cancelled the
other two via the matrix's default fail-fast, not because they succeeded.

**Why this wasn't caught before**: `cd-deploy.yml` had never actually run
for real until this same investigation (item 66); `terraform-plan.yml`'s
`plan` job and `cloud-security-tests.yml`'s `adversarial-tests` job have
the exact same gap but had *also* never actually run for real yet
(`plan` only triggers on a PR touching `terraform/**`;
`adversarial-tests` only after a successful `cd-deploy.yml` run, which
had never happened) -- found and fixed proactively in both, by
inspection, rather than waiting to rediscover the identical failure live
a second and third time.

**Resolution**: added a workflow-level `permissions: { contents: read,
id-token: write }` block to `cd-deploy.yml` (covering
`build-and-push`/`deploy-canary`/`canary-bake`/`rollback`), and updated
the `promote` job's own job-level `permissions:` block to include both
`contents: write` (already there, needed for its bot-commit step) AND
`id-token: write` -- job-level `permissions:` blocks REPLACE the
workflow-level default for that job rather than merging with it, so
`promote` would have silently kept failing even with the new
workflow-level block added, had its own block not also been updated.
Added the equivalent `permissions: { contents: read, id-token: write }`
directly to the single job that needs it in both `terraform-plan.yml`
and `cloud-security-tests.yml`. `k8s-manifests-ci.yml` was checked and
confirmed to use no Azure OIDC login at all (it only ever touches a
local, ephemeral `kind` cluster), so it needed no change.

**What full version requires**: nothing further -- fully verified. After
this fix, re-dispatching `cd-deploy.yml` got past Azure login and into
the real build/push steps, but surfaced a second, distinct real bug (a
mismatched OIDC subject format) before it could succeed -- see item 68.

### 68. RESOLVED: the federated credentials' `subject` used the plain `owner/repo` name format, but this repo's real OIDC tokens include immutable owner/repo IDs

**What was found**: with item 67's fix in place, the real, re-dispatched
`cd-deploy.yml` run got all the way to a real token exchange attempt and
failed with a new, different error: `AADSTS700213: No matching federated
identity record found for presented assertion subject
'repo:Ritesh34347@19557415/NaviGraphMVP@1317223914:ref:refs/heads/main'`.
The actual OIDC token GitHub issued for this repo embeds immutable
numeric owner/repo IDs in the subject claim
(`repo:OWNER@ownerId/REPO@repoId:...`), but the `navigraph-cd` app
registration's two federated credentials (`navigraph-github-push-main`,
`navigraph-github-pull-request`) were both created with the plain
`repo:Ritesh34347/NaviGraphMVP:...` name-based subject -- confirmed via
`az ad app federated-credential list`, which showed exactly that
mismatch.

**Why this wasn't caught when the federated credentials were first
created**: nothing had actually exercised a real token exchange against
them until this same investigation (`cd-deploy.yml` had never gotten
past item 66's missing-secrets error, and `terraform-plan.yml`'s `plan`
job -- sharing the pull_request credential -- had never actually run
against real Azure credentials either). The credential *existing* said
nothing about whether its subject actually matched what GitHub would
really present.

**Resolution**: `az ad app federated-credential update` on both
credentials, changing `subject` to the real, ID-based format
(`repo:Ritesh34347@19557415/NaviGraphMVP@1317223914:ref:refs/heads/main`
and `...:pull_request` respectively) -- `workflow_dispatch` runs on
`main` present the identical `ref:refs/heads/main` subject as a real
push, confirmed by the fact this fix immediately unblocked the manually
re-dispatched run too, with no third federated credential needed.

**Verified**: the next re-dispatch of `cd-deploy.yml`
(`https://github.com/Ritesh34347/NaviGraphMVP/actions/runs/30562940216`)
completed with every job `success` -- real image builds/pushes for all 3
services, a real canary deploy at 0% weight, real bake windows at
10%/50%/100% (each polling the real live ingress-nginx Prometheus
metrics via `tools/scripts/canary_gate.py`), and a real promotion to
`gateway-stable`/`web-stable` with a real bot-commit
(`0168080`) updating `overlays/dev/kustomization.yaml`'s `newTag` fields
-- the first fully successful real `cd-deploy.yml` run in this project's
history.

### 69. RESOLVED: `agent-runtime`'s Deployment was never actually updated with a new image during a CD run -- found live via the first fully successful run

**What was found**: real, post-run verification (`kubectl get deployment
agent-runtime -n navigraph -o jsonpath='{...image}'`) showed
`navigraphdevacr.azurecr.io/navigraph-agent-runtime:unreleased` still
running, and the pods' `AGE` was unchanged (3h13m old) despite item 68's
run having just built and pushed a fresh `navigraph-agent-runtime:<sha>`
image moments earlier -- i.e. the real cluster's `agent-runtime` never
actually got the new code.

**Root cause**: `agent-runtime` has no `*-stable`/`*-canary` split (by
design -- it's a plain rolling-update, internal-only service, see
DECISIONS.md), so `deploy-canary`'s "Apply base manifests" kustomize step
was its ONLY path to a new image, driven entirely by
`overlays/dev/kustomization.yaml`'s `newTag` field for
`navigraph-agent-runtime`. But that field is only ever bumped by the
`promote` job's bot-commit, which runs at the very END of a successful
CD run -- one full cycle *after* the run that actually built the image --
and `promote` itself only ever calls `kubectl set image` for
`gateway-stable`/`web-stable`, never for `agent-runtime`. Net effect: a
`kubectl apply` where the manifest's image field is textually unchanged
from what's already deployed creates no new ReplicaSet, so the following
`kubectl rollout status deployment/agent-runtime` step trivially reports
success on a rollout that never happened -- silently masking the gap
rather than erroring.

**Why this wasn't caught in Phase 10a's local `kind` validation**: that
validation applies a fixed, hand-picked tag once and checks the pod comes
up `Ready` -- it never exercises two sequential CD runs to notice that a
*second* run's newly-built image never actually lands.

**Resolution**: added an explicit `kubectl set image deployment/agent-runtime
agent-runtime=...:${{ github.sha }}` call in `deploy-canary`'s "Point the
canary tracks at the new SHA" step, alongside the existing
gateway-canary/web-canary calls -- `agent-runtime` has no bake/promotion
gate of its own, so updating it immediately (same run it was built in) is
correct, matching how any other plain rolling-update service should
behave. Not yet re-verified with a fresh CD run as of this writing (the
fix was applied and reasoned through against the real, observed gap, but
the next real `cd-deploy.yml` run should be checked to confirm
`agent-runtime`'s pods actually roll to the new SHA and are no longer
stuck on `:unreleased`).

**Verified, with a real complication**: the next re-dispatched run
(`https://github.com/Ritesh34347/NaviGraphMVP/actions/runs/30564905124`)
confirmed the fix -- `deployment.apps/agent-runtime image updated` fired
this time, unlike before -- but the rollout itself then hung and the job
failed on the 180s timeout. Investigated live: `kubectl describe pod`
showed the real cause was a NEW, distinct bug, item 70 below.

### 70. RESOLVED: `agent-runtime`'s `maxSurge: 1, maxUnavailable: 0` rollout strategy needs more spare CPU than the real 2-node dev cluster has

**What was found**: with item 69's image-update fix in place,
`kubectl rollout status deployment/agent-runtime` hung for the full 180s
timeout. `kubectl describe pod` on the new, stuck replica showed
`PodScheduled: False` with the real scheduler event
`0/2 nodes are available: 2 Insufficient cpu`. `agent-runtime`'s
`maxSurge: 1, maxUnavailable: 0` strategy requires a 3rd replica to be
scheduled and become Ready before any of the 2 existing ones are torn
down -- but by this point in Phase 10b, the real cluster is also running
`gateway`/`web`'s permanent `*-stable`+`*-canary` pairs (4 pods, not 2)
plus every observability/datastore service, on only 2 real
`Standard_D2s_v7` nodes (1900m allocatable CPU each, confirmed via
`kubectl get nodes -o custom-columns=...allocatable.cpu`) -- there was
simply no spare capacity for a genuinely extra (not replacing) pod.

**Why this wasn't caught in Phase 10a's local `kind` validation**: `kind`
runs a single-node, resource-unconstrained local cluster with far fewer
concurrently-running services (no permanent canary tracks, no real
observability stack pressure) -- a capacity-driven scheduling failure
like this is specific to the real, resource-limited dev AKS environment,
not reproducible locally.

**Resolution**: changed `agent-runtime`'s rollout strategy in
`infra/k8s/base/agent-runtime/deployment.yaml` to `maxSurge: 0,
maxUnavailable: 1` -- rolls out one-at-a-time by tearing down an old
replica before starting its replacement, so it only ever needs
`agent-runtime`'s existing footprint (never a 3rd pod), while keeping 1
of 2 replicas serving throughout the rollout. No Azure cost or node-size
change needed. Applied directly to the live, already-stuck Deployment via
`kubectl patch` to unblock the in-progress rollout immediately (confirmed:
`kubectl rollout status` completed successfully within seconds after the
patch, both pods `Running` on the new SHA, the stuck `Pending` replica
gone), and committed the same change to the source manifest so every
future `kustomize build`/CD run inherits the fix.

**Verified end to end**: `kubectl get deployment agent-runtime -n
navigraph -o jsonpath='{...image}'` confirmed the live cluster is running
`navigraph-agent-runtime:1637375aea5a4019c2c623df72b2871630f89e2a` --
the exact SHA this investigation's own commits produced -- closing the
loop on items 66 through 70 with real, observed proof at every step
rather than an assumption that the fixes "should" work.

### 71. `cd-deploy.yml` -- the first genuinely clean, fully automatic, zero-manual-intervention real run

**What was found**: pushing item 70's fix (`e101524`, which touches
`infra/k8s/**`, one of `cd-deploy.yml`'s `push` path filters)
auto-triggered a real `push`-event CD run
(`https://github.com/Ritesh34347/NaviGraphMVP/actions/runs/30565723435`)
moments before a manual `workflow_dispatch` re-verification run was also
started on the same commit. Both share the `cd-deploy-dev` concurrency
group (correctly serialized, never running jobs in parallel), but since
both were building/deploying the *same* commit, the manually-dispatched
run's own `promote` job later tried to push an identical
"cd: promote e101524..." commit and was rejected (`! [rejected] ...
fetch first`) because the auto-triggered run's `promote` had already
pushed the equivalent commit moments earlier. This is a benign
redundant-trigger artifact of manually re-verifying right after a
push-triggering change, not a deployment-correctness bug -- the
concurrency group did its real job (no two rollouts ever touched the
cluster simultaneously), and the underlying push-triggered run itself
completed with every single job `success`, zero manual intervention,
confirmed both via the GitHub Actions run and directly against the live
cluster (`kubectl get deployment .../{gateway-stable,web-stable,
agent-runtime}` all showing the identical, correct
`e1015240b396bdfe7b937759ac7a0a1cc5790f1e` image).

**What full version requires**: nothing blocking -- if a real,
non-testing scenario ever manually re-dispatches a commit that a push
already triggered a deploy for, `promote`'s bot-commit step could
optionally `git pull --rebase` immediately before pushing to absorb a
concurrent identical promotion gracefully instead of failing loudly.
Not implemented here since the underlying scenario is a testing
artifact of this investigation, not a real operational pattern.

**This closes the full arc of Phase 10b's `cd-deploy.yml` verification**
(items 66-71): from zero real GitHub secrets, through a missing OIDC
permission, a stale federated-credential subject format, a
never-actually-redeployed `agent-runtime`, and a real node-capacity
rollout hang -- to a fully real, fully automatic, fully verified CD
pipeline, proven end to end against live Azure infrastructure.

### 72. RESOLVED: `promote`'s bot-commit push failed whenever any other real commit landed on `main` during a CD run's bake window

**What was found**: this happened for real, twice, for two different
reasons. The first time (item 71) was a benign artifact of manually
re-dispatching a commit a push had already triggered a deploy for. The
second time was a genuinely ordinary scenario: an unrelated, real
`LIMITATIONS.md` documentation commit was pushed to `main` while a
separate, real CD run (deploying the empty-response retry fix) was still
in its ~20-minute build+bake window -- `promote`'s bot-commit step
checked out `main` at the *start* of that window, so by the time it tried
to push its own commit at the *end*, the remote had moved and the push
was rejected (`! [rejected] main -> main (fetch first)`). Confirmed the
live cluster was unaffected both times: `kubectl set image`/scale/patch
all run in earlier steps of the same job and had already succeeded --
only the git-tracked `newTag` bookkeeping was left stale.

**Why this was worth fixing properly this time**: item 71's original
note treated this as a testing artifact not worth fixing. The second
occurrence proved that framing wrong -- any ordinary push to `main`
during a live CD run's real ~20-minute duration will reproduce this, and
that's a realistic, recurring pattern for a repo with more than one
person (or one person doing more than one thing) pushing to `main`.

**Resolution**: `promote`'s commit step now retries the push up to 5
times, `git fetch origin main && git rebase origin/main` between
attempts, before giving up. The commit's own edit (a deterministic
"set `newTag` to the promoted SHA" regex substitution on one file) rebases
cleanly against unrelated changes elsewhere in the repo; a real
conflict here would mean two promotions racing for the same file, bounded
at 5 attempts rather than retried forever. The stale `newTag` values left
by this specific failure were also corrected manually to match the
already-correct live cluster state, keeping git and the cluster in sync
without waiting for the next real deploy.

**What full version requires**: nothing further planned -- verify on the
next real CD run that a concurrent push no longer breaks `promote`.

### 73. RESOLVED: SQL Generation's `SUM`-vs-`COUNT` aggregation gap (item 38's `gq_002` finding)

**What was found** (originally, Phase 8): `gq_002` ("How many
transactions has each customer made?") resolved only a dimension column
(`CUSTOMERID`, no numeric measure column at all, confirmed against the
real golden question's own `expected_columns: [CUSTOMERID]`), yet SQL
Generation's `_aggregation_function` had no way to express "count the
rows" independent of a resolved measure column -- `_generate_statements`
only ever emitted an aggregate when at least one `role="measure"` column
existed, and even then only ever chose `SUM`/`COUNT` keyed on that
column's data type, never a bare `COUNT(*)`. The real, live result was a
nonsensical per-customer total like "1,229,737,256 transactions."

**Resolution**: added a small, documented phrase-trigger heuristic,
`_is_count_question` (mirroring `_needs_predicate_resolution`'s existing
style) -- `"how many"`, `"number of"`, `"count of"` -- that, when
matched, always emits `COUNT(*) AS RECORD_COUNT` and intentionally
ignores any `role="measure"` columns for aggregation purposes (a "how
many" question summing a resolved numeric field would be just as wrong
as summing the wrong one). Also fixed the `GROUP BY` condition, which
previously only fired when a measure column existed -- a count-only
query with dimension columns but no measure needs `GROUP BY` too, or it
would return one row per record instead of one row per group. Verified
with 2 new unit tests: the exact `gq_002` shape (dimension-only,
produces `COUNT(*)`), and a case proving a spuriously-resolved measure
column is still correctly ignored when the question is count-shaped.

**What full version requires**: nothing further planned -- this specific,
real finding from item 38 is fully resolved. The rest of item 38's
findings (the judge's own occasional malformed-response rate, the
gq_007/gq_010 schema-resolution misses already addressed by Phase 9's
join-inference fix and the Clarification Coordinator respectively) remain
as originally logged.

### 74. RESOLVED: cert-manager's HTTP-01 solver pods had no NetworkPolicy allowing ingress -- both real TLS certificates sat unissued for 16+ hours

**What was found**: `gateway-tls`/`web-tls` had been stuck
`Ready: False` since cluster bootstrap, with the real Challenge objects
reporting "Waiting for HTTP-01 challenge propagation ... context
deadline exceeded" for over 16 hours. Root-caused with real evidence, not
assumption: a direct in-cluster `curl` to the exact real challenge token
path timed out identically whether routed through the public hostname or
straight to the solver Service's ClusterIP (bypassing ingress-nginx
entirely) -- ruling out an ingress-routing problem and confirming the
network layer itself was the block. `cm-acme-http-solver-*` pods (one
dynamically created per in-flight ACME challenge, always labeled
`acme.cert-manager.io/http01-solver: "true"`) were never covered by any
of this namespace's existing NetworkPolicies -- `default-deny-all`'s
empty `podSelector: {}` silently denied all ingress to them, including
from ingress-nginx itself. This class of gap (a new pod type introduced
without its own explicit allow-rule under a default-deny scheme) is the
same shape as item 64's gateway→agent-runtime finding, just for a
dynamically-created pod type nobody had written a static manifest for.

**Resolution**: added `allow-ingress-nginx-to-acme-solver`, a real
NetworkPolicy allowing ingress from the `ingress-nginx` namespace to any
pod labeled `acme.cert-manager.io/http01-solver: "true"` on port 8089
(cert-manager's own fixed, internal solver-container port, confirmed via
`kubectl get svc -l acme.cert-manager.io/http01-solver=true`). Applied
directly to the live cluster first to unblock the already-stuck
certificates immediately, then committed to source so every future
deploy inherits it. **Verified end to end, twice**: both certificates
issued successfully within seconds of the fix ("The certificate has been
successfully issued"), first against `letsencrypt-staging` (confirming
the real fix), then -- since this was exactly the "one verified staging
issuance" `DECISIONS.md` names as the trigger to promote -- against
`letsencrypt-prod` for real, genuinely browser-trusted certificates,
confirmed via a real `curl` with full TLS verification (no `-k` bypass)
returning `HTTP 200` on both public hostnames.

**What full version requires**: nothing further planned -- both real
certificates are issued, trusted, and auto-renewing (`Renewal Time` set
by cert-manager). The one remaining, already-logged limitation (item 52)
is that this is still `nip.io`, not a real registered domain -- unrelated
to this fix.

### 75. RESOLVED: gateway's HTTP client to agent-runtime (and ingress-nginx's own proxy timeout) were both shorter than the real Request Orchestrator's actual latency

**What was found**: the first real end-to-end `/ask` call ever made
through the actual public gateway path (every prior real verification
this project ran -- the eval harness -- called the Request Orchestrator
directly inside the `agent-runtime` pod, bypassing this HTTP hop
entirely) returned a real `502 {"detail":"agent-runtime is unavailable
or returned an error"}`. Root-caused with real evidence: `agent-runtime`'s
own logs showed the exact same `trace_id` still genuinely
processing -- real `Anthropic`/`Snowflake`/`OPA` calls -- 45+ seconds
after `gateway`'s own log recorded "agent-runtime call failed." Gateway's
`httpx.AsyncClient` used a flat `timeout=30.0`, and `ingress-nginx`'s
default `proxy-read-timeout` (60s, no override annotation existed) was
also shorter than the real pipeline's actual worst-case latency -- a
question requiring several sequential LLM calls (Conversation, Intent
Understanding, Semantic Retrieval predicate resolution, Narrative
Generation, Follow-up Suggestion, now potentially plus one retry each
from item 63's empty-completion fix) can genuinely take well over a
minute for real.

**Why this was never caught before**: nothing in this entire project's
extensive real verification history had ever actually exercised the real
public HTTP path end to end -- the eval harness, `tests/integration/orchestrator_pipeline/`,
and every other pipeline-chain test all call `RequestOrchestratorAgent`
directly in-process or via a pod-local invocation, never through
`gateway`'s own `httpx` client and never through the real
internet-facing ingress.

**Resolution**: bumped `gateway`'s `httpx.AsyncClient` timeout from 30s
to 120s, and added matching `nginx.ingress.kubernetes.io/proxy-read-timeout`/
`proxy-send-timeout: "120"` annotations to both the `gateway` and
`gateway-canary` Ingress objects (both share the same real hostname's
server block, so both need the same value -- same reasoning as the
existing shared `ssl-redirect` annotation). Raising only one of the two
layers would have just moved the bottleneck to the other. Applied
directly to the live cluster's Ingress objects first to unblock real
usage immediately, then committed to source.

**What full version requires**: nothing further planned for this
specific gap. A more robust long-term design (e.g. a real async
job/polling pattern instead of one long-held HTTP connection) is a
reasonable future improvement if real question latency grows further,
but is out of scope for this fix -- 120s comfortably covers every real
latency this project has ever observed.

### 76. RESOLVED: item 72's `promote` retry-and-rebase fix couldn't handle a genuine same-line merge conflict, when two concurrent CD runs' promote commits raced

**What was found**: item 72's bounded rebase-and-retry loop was built for
the *ordinary* case (an unrelated commit lands on `main` during the bake
window, and `promote`'s own edit rebases cleanly on top of it). This time
was different: two CD runs were genuinely concurrent -- run1 (promoting
`289ff11`, the SUM-vs-COUNT fix) and run2 (promoting `fb55ec1`, the
NetworkPolicy+timeout fix) -- and BOTH runs' `promote` jobs were editing
the exact same `newTag:` lines in
`infra/k8s/overlays/dev/kustomization.yaml` at the same time. Run1's bot
commit (`7d4ed88`, setting `newTag` to `289ff11`) landed on `origin/main`
first, during run2's own bake window. When run2's `promote` tried to
rebase its own bot commit (setting `newTag` to `fb55ec1`) on top of
`origin/main`, git correctly reported a genuine content conflict on the
identical lines -- `CONFLICT (content): Merge conflict in
infra/k8s/overlays/dev/kustomization.yaml` -- which no amount of retrying
a plain `git rebase` can auto-resolve, since both sides are real,
different, non-mergeable edits to the same field. Run2's job failed after
exhausting its 5 retry attempts.

**Confirmed the live cluster was unaffected**: `kubectl get deployment
gateway-stable web-stable agent-runtime -n navigraph -o jsonpath=...`
showed all three genuinely running run2's image
(`fb55ec1924238ea90f1b833a41e78a72948b06b8`) -- the actual `kubectl set
image`/rollout steps in run2's job had already succeeded before the
conflicting bot-commit step ran. Only the git-tracked `newTag`
bookkeeping (now stuck at run1's older `289ff11`, one promotion behind
the real, live state) was left wrong.

**Resolution**: manually corrected `infra/k8s/overlays/dev/kustomization.yaml`'s
three `newTag` fields on `main` from `289ff11...` to
`fb55ec1924238ea90f1b833a41e78a72948b06b8`, matching the confirmed-correct
live cluster state -- the same resolution pattern item 72 already used
for the first occurrence of this bug class.

**What full version requires**: item 72's retry-and-rebase loop still
correctly handles the common case (an unrelated commit landing mid-bake);
it does not and cannot auto-resolve two promotions racing to set the same
field to two different values -- that's a genuine conflict, not a
transient rejection, and auto-resolving it would mean silently picking
one promotion's SHA over the other with no principled way to know which
one is actually newest/correct. A more robust fix would serialize
`promote` jobs across concurrent CD runs (e.g. a GitHub Actions
concurrency group keyed on the `promote` job specifically, queuing rather
than racing) -- logged as a real, unimplemented improvement rather than
solved here, since this project's CD workflow has otherwise never needed
run-to-run serialization before now.

### 77. `web` had no real chat interface until now -- was still Phase 1's placeholder status page, never updated by any later phase

**What was found**: asked "how do I demo this," the honest answer was
"curl or Postman" -- `web/src/app/page.tsx` was still exactly Phase 1's
scaffolding (`getGatewayStatus()`, a single server-rendered "gateway
reachable: yes/no" line). Every later phase's real verification
(`eval/run_harness.py`, every `tests/integration/*_pipeline/` suite, every
live smoke test) called the gateway's `/ask` directly via `httpx`/`curl`,
so nothing ever needed a real browser-facing UI, and nothing ever built
one. This is the same class of drift already named in item 32 (docs
falling behind what's actually built) but in the opposite direction --
here, a real feature was simply never added, not a doc going stale.

**What was added**: a real, working client-side chat UI (`web/src/app/ChatDemo.tsx`)
-- text input, real narrative/chart/data-table/follow-up-suggestion
rendering per `RequestOrchestratorResult`'s actual outcome variants
(`answered`/`needs_clarification`/`failed`), real `session_id` threading
across turns for genuine multi-turn conversation. It calls the gateway's
`/ask` directly from the browser (not through a Next.js API route), which
required adding real CORS middleware to the gateway (`packages/gateway/navigraph_gateway/main.py`)
-- the first time any browser-origin call had ever been made against
`/ask` in this project's history; every prior real call used `curl`/`httpx`,
neither of which is subject to a browser's same-origin policy, so this
gap was invisible until now. Two real tests
(`packages/gateway/tests/test_cors.py`) prove the real allow/deny
behavior against the actual configured `web_origin`.

**Known, deliberate scope limits of this addition** (not oversights):
- **No real sign-in.** `tenant_id`/`user_id`/`roles`/`claims` are fixed
  demo constants in the client component (`navikenz-poc`/`demo-user`/
  `["analyst"]`) -- identical trust model to every prior real API call
  this project has made (see item 23's Azure AD deferral), just now also
  true of the browser path. A real login screen is future work, not a
  regression introduced here.
- **No SQL preview.** `RequestOrchestratorResult` (the real contract the
  gateway returns) never carries the generated SQL -- only
  `final_columns`/`final_rows`/`chart`/`narrative` -- so the UI can't show
  it without a contract change. Logged rather than silently working
  around it by reaching into an unrelated agent's output.
- **No real progress indicator.** A real question can take up to ~2
  minutes (see item 75) but the UI shows one static "Thinking..." message
  the whole time, not real incremental progress -- there is no
  streaming/SSE mechanism anywhere in the real pipeline to drive one.
- **Charts are plain HTML/CSS/SVG, not a charting library.** `web/package.json`
  never actually had Recharts installed (Phase 1's own decision named it,
  but no later phase added the dependency) -- adding a real new npm
  dependency wasn't necessary for a demo-quality bar/line/single-value
  rendering, and was skipped to avoid an unreviewed new dependency this
  close to a live deploy.

**What full version requires**: a real sign-in flow (blocked on the
already-logged Azure AD gap, item 23); a real SQL-preview field added to
`RequestOrchestratorResult` if that transparency is wanted; a real
async/streaming answer mechanism if question latency grows further past
what a static "thinking" message can reasonably cover.

### 78. RESOLVED: `NEXT_PUBLIC_GATEWAY_URL` silently fell back to `localhost:8000` in production because `page.tsx`'s own health check makes the route dynamic

**What was found**: the first real click of the new chat UI (item 77)
sent its `/ask` call to `http://localhost:8000/ask` instead of the real
public gateway -- confirmed live via the actual browser's network panel
(`OPTIONS http://localhost:8000/ask -> 405`, then `POST ... [FAILED:
net::ERR_FAILED]`). Root cause: the existing assumption (written into
`infra/k8s/base/web/deployment-stable.yaml`'s own comment) that
`NEXT_PUBLIC_GATEWAY_URL` is purely inlined into the client bundle at
`next build` time is only true for a **statically generated** route.
`src/app/page.tsx` performs its own `cache: "no-store"` fetch (the
gateway-reachability health check), which makes Next.js treat `/` as
dynamic (`ƒ /` in the real `next build` output) -- so
`env.NEXT_PUBLIC_GATEWAY_URL`, read inside that Server Component and
passed as a prop to the new `ChatDemo` client component, is evaluated
fresh from `process.env` on the actual running container at every real
request, not baked into a prerendered page at build time. `web/Dockerfile`'s
`runner` stage never re-declares `ENV NEXT_PUBLIC_GATEWAY_URL` (Docker
`ENV`/`ARG` do not cross a `FROM ... AS runner` stage boundary from the
earlier `builder` stage), so the variable was genuinely undefined at
runtime, and `src/lib/env.ts`'s own `optional()` fallback
(`http://localhost:8000`) silently took over -- with no error anywhere in
the build or deploy pipeline, since the build-arg mechanism itself
(`--build-arg NEXT_PUBLIC_GATEWAY_URL=...`) worked exactly as designed for
the `builder` stage; the gap was specifically the `runner` stage's
separate, empty environment. A second, independent real finding
surfaced investigating this: the `NAVIGRAPH_DOMAIN` GitHub Actions
repository variable that `cd-deploy.yml`'s build-arg reads
(`https://api.navigraph.${{ vars.NAVIGRAPH_DOMAIN }}`) was never actually
set, confirmed live in the real build log
(`BUILD_ARGS="--build-arg NEXT_PUBLIC_GATEWAY_URL=https://api.navigraph."`
-- note the trailing dot, no real domain) -- a second, compounding gap
that would have produced a malformed URL even if the `runner`-stage issue
were the only problem.

**This gap was invisible until now for the same reason as item 77
itself**: nothing before the real chat UI ever made a browser-side
fetch that depended on this specific value -- the server-side
`GATEWAY_URL` health check (a different variable, correctly runtime-
configured via the Deployment's own `env:` block all along) was the only
thing `page.tsx` ever used.

**Resolution**: added `NEXT_PUBLIC_GATEWAY_URL` as a real Kubernetes
Deployment-level runtime env var to both `web-stable` and `web-canary`
(`infra/k8s/base/web/deployment-{stable,canary}.yaml`), set to the real
public gateway hostname, matching exactly how `GATEWAY_URL` itself was
already wired -- this fixes the dynamic-route runtime read directly and
remains correct even if the route is ever made static again (Next.js
would then just also inline the same correct value at build time from
the existing build-arg). Applied live via `kubectl set env
deployment/web-stable` first to unblock the demo immediately, verified
via a real browser round-trip (see item 77), then committed to source.
The `NAVIGRAPH_DOMAIN` repo-variable gap is logged separately below
rather than fixed here, since the Deployment-level env var now makes it
unnecessary for this specific route regardless.

**What full version requires**: setting the real `NAVIGRAPH_DOMAIN`
GitHub Actions repository variable properly (currently unset) would
still be worthwhile defense-in-depth for any future genuinely-static page
that references `NEXT_PUBLIC_GATEWAY_URL` directly in client code (which
IS correctly build-time-inlined, unaffected by this specific bug) -- not
done here since no such page exists yet.

### 79. RESOLVED: Semantic Retrieval's LLM call silently truncated (empty response) once the real candidate list + unresolved-term count got large enough, causing whole-batch clarification failures on real, legitimate questions

**What was found**: reported by the user directly testing the new chat
UI -- a real question ("How does the transaction count and value on
2018-01-02 compare to the same day in prior weeks or the prior month
average?") got a clarification response claiming no tables matched
"transaction count" or "value", even though the real
`STAGING_TRANSACTIONS` table and its `TOTALVALUE`/`TRANSACTIONID` columns
obviously cover exactly this. Root-caused by calling each real
Understanding-domain agent directly (via a `kubectl port-forward` to
`agent-runtime`, bypassing the gateway) with the real question's actual
extracted entities and the real 114-column candidate list from
`Metadata Discovery`:
- Intent Understanding correctly extracted 5 real entities: `"transaction
  count"`, `"transaction value"`, `"2018-01-02"`, `"prior weeks"`, `"prior
  month average"`.
- Calling Semantic Retrieval with all 5 terms + the real 114-candidate
  list returned `matched: false` for every single term, with the agent's
  own `errors` showing `llm_response_not_json: "Expecting value: line 1
  column 1 (char 0)"` -- the LLM's response text was completely empty --
  and `metadata.tokens_output` exactly equal to the agent's hardcoded
  `max_tokens=1536`.
- Calling Semantic Retrieval again with the SAME real 114-candidate list
  but just `"transaction value"` alone, or `"transaction count"` alone, or
  both together (no date/relative-time terms), succeeded cleanly every
  time with sensible real matches (`TOTALVALUE`, `TRANSACTIONID` /
  `CUSTOMER_ASSET_AGG.TXN_COUNT`) -- proving the matching logic and
  prompt are fine; only the token budget was the problem.
- `AnthropicLLMClient`'s existing retry-once-on-empty-completion logic
  (added for a different, transient bug -- see item 63/the Grounded
  Narrative Generation entry) already ran here too and still came back
  empty, confirming this is a structural under-budgeting, not the
  transient single-call glitch that retry was designed to catch --
  retrying with the same too-small `max_tokens` just truncates again.

**Why this matters beyond this one question**: `Metadata Discovery`
returns the ENTIRE real catalog's columns as candidates (114 for this
tenant's one data source), dumped verbatim into the prompt
(`tokens_input: 17483` on the failing call) -- this scales with the
real customer's schema size, not a fixed small number, and any question
whose `Ontology` pass leaves multiple unresolved terms (very common for
genuinely complex, multi-clause real questions, as opposed to the 10
golden-set questions which were all written/tested with clean single-
concept phrasing) exercises exactly this path.

**Resolution**: raised `SemanticRetrievalAgent`'s hardcoded `max_tokens`
from 1536 to 4096 (`understanding/semantic_retrieval/agent.py`). Added a
new unit test (`test_max_tokens_budget_is_large_enough_for_a_real_size_candidate_list`)
asserting the real budget requested is comfortably above the confirmed
failure point, so a future edit can't silently shrink it back down
unnoticed.

**What full version requires**: 4096 is a reasoned, evidence-based
increase (comfortably above the exact 1536-token failure point observed),
not a value re-derived from a real worst-case analysis of catalog size ×
term count -- if a future tenant's real catalog grows substantially
larger than 114 columns, or a real question routinely produces
significantly more than 5 unresolved terms, this budget may need
revisiting again. A more scalable long-term fix (e.g., pre-filtering the
candidate list to a smaller relevant subset before this LLM call, rather
than always sending the entire catalog) is a reasonable future
improvement, logged here rather than built now.

**Item 79 verified live (2026-08-01, after the Anthropic account's usage
limit reset)**: re-ran the exact same real question through both the
direct agent endpoint and the real chat UI. Semantic Retrieval now
returns `matched=5/5`, zero errors, `tokens_output=3133` (comfortably
under the new 4096 cap, vs. the old exact-1536 truncation) -- confirmed
fixed.

### 80. RESOLVED: SUM-vs-COUNT gap (item 38/73) resurfaces under phrasing that doesn't trip the `_is_count_question` phrase-trigger

**What was found**: verifying item 79's fix live, the same real question
("How does the transaction count and value on 2018-01-02 compare...")
now produces a real `answered` outcome, but with a nonsensical narrative
value: `"a transaction count total of 3,063,258,983,525"` -- clearly a
`SUM`, not a real row count, for a single day's data. Root cause is very
likely the same class of bug items 38/73 already fixed once: Semantic
Retrieval matched the entity "transaction count" to
`STAGING_TRANSACTIONS.TRANSACTIONID` (a real, reasonable column match --
see item 79 above) as a **measure** column, and SQL Generation's
`_is_count_question` heuristic only trips on the literal phrases `"how
many"`, `"number of"`, `"count of"` -- none of which appear in this
question's actual phrasing ("transaction count" as a noun phrase, not
"how many transactions"). So `_aggregation_function` fell through to its
normal SUM-based measure aggregation on `TRANSACTIONID`, summing ID
values instead of counting rows.

**Resolution (2026-08-02)**: rather than broadening `_is_count_question`'s
phrase list (which would have been the narrower fix, and would have
actively broken this exact question -- it needs BOTH a real `COUNT` on
`TRANSACTIONID` AND a real `SUM` on `TOTALVALUE` in the same query;
`_is_count_question` intentionally overrides ALL measure aggregation with
a bare `COUNT(*)`, so triggering it here would have silently dropped the
`TOTALVALUE` sum entirely), fixed the actual general root cause instead:
`_aggregation_function` (`sql_generation/agent.py`) now checks whether a
resolved measure column's name is identifier-shaped
(`_is_identifier_column`: ends in `"ID"`, matching this schema's real,
consistent naming convention -- `CUSTOMERID`, `TRANSACTIONID`,
`MARKETID`, `EXCHANGEID`) and always returns `COUNT` for it, regardless
of phrasing or intent -- summing a surrogate/natural key is never
semantically valid. A real additive measure (e.g. `TOTALVALUE`) resolved
in the same query is unaffected and still gets a real `SUM`. This is
strictly more general than a phrase-list expansion: it also protects
against any *future* question phrasing that resolves an ID-shaped column
as a measure, not just this one repro.

Added a new regression test
(`test_identifier_shaped_measure_column_is_counted_not_summed`) asserting
both halves of the real, live-reproduced scenario: `TRANSACTIONID` is
counted, `TOTALVALUE` is still summed, in the same generated statement.
All 12 `sql_generation` tests (11 pre-existing + 1 new) pass; full
`packages/` unit suite: 333 passed, 6 skipped (unchanged pre-existing
local-venv gaps only).

### 81. TEMPORARY, OPT-IN: real Snowflake trial account went unreachable (billing suspension, then an MFA policy requirement) -- a demo-only cached-replay fallback was added

**What was found**: the one real, registered Snowflake data source
(`FIDELITY_POC`, via the `FIDELITY_ANALYST_ROLE` service user
`SHUBHSNFLK`) became genuinely unreachable for reasons entirely outside
this project's own code, in two sequential real incidents: (1) the free
trial's warehouses were suspended pending billing (`"Your free trial has
ended and all of your virtual warehouses have been suspended"`), and
after that was addressed, (2) the account began enforcing MFA on this
service user (`"Multi-factor authentication is required for this
account"`, later `"none of your current MFA methods are supported for
programmatic authentication"` once an authentication policy exempting
the user from MFA *enrollment* was applied but an already-enrolled MFA
method was still being demanded for password-based login) -- confirmed
live via `query.data_source_discovery`'s own connectivity check, which
reports these as a real `data_source_unreachable` `AgentError` with the
verbatim Snowflake error message. Resolving the MFA policy fully is an
in-progress, real Snowflake-account-configuration fix on the product
owner's side (not something this codebase can fix), tracked separately
from this item.

**What was added, as a stopgap so demos can proceed regardless of
Snowflake account status**: a real, deliberately narrow, opt-in-only
fallback in `orchestrator.request_orchestrator` (`demo_fallback.py` +
`demo_fallback_data.json`). When `query.data_source_discovery` reports
the resolved data source unreachable, and only if the
`NAVIGRAPH_DEMO_FALLBACK` environment variable is explicitly set to
`"true"` (default: unset/off), the orchestrator checks whether the
resolved question exactly (case-insensitively) matches one of 8 real
golden-set questions this fallback has a genuine, previously-captured
real answer for -- pulled verbatim from two real `eval/run_harness.py`
runs against the live stack while Snowflake was still reachable
(`eval/results/20260730T225222Z.json` primary, `.../20260730T221839Z.json`
for the one question -- `gq_009` -- the primary run didn't succeed on).
If matched, the orchestrator returns a real `outcome="answered"` using
that cached `narrative`/`follow_up_suggestions`/`final_row_count`, with
the narrative always prefixed `"[Cached demo replay -- live data source
unreachable; showing a real result captured from run <run_id>]"` so it
is never mistaken for a live answer, in the API response and therefore
in the chat UI. Any question without a real cached answer (including 2
of the 10 golden questions that never succeeded in any real captured
run, and every non-golden-set question) still gets the honest,
unmodified `data_source_unreachable` failure -- this mechanism never
fabricates an answer it doesn't have a genuine prior real result for.

**Verified**: 6 new unit tests (`test_demo_fallback.py`) covering
default-off behavior, exact-string env-var matching, real cached-question
matching (including the real, honest case where `gq_002`'s captured
narrative is itself empty -- a real, already-documented behavior for a
10,000-row result, not a test bug), no-match behavior for any
non-cached question, and the narrative's cached-replay marker.

**What full version requires**: this is explicitly temporary and
demo-only -- remove `demo_fallback.py`/`demo_fallback_data.json` and this
item's wiring in `request_orchestrator/agent.py` once the real Snowflake
account access issue is fully resolved and confirmed stable, rather than
letting a "demo mode" mechanism linger in the real production code path
indefinitely. `NAVIGRAPH_DEMO_FALLBACK` must stay unset (off) in any
context other than a deliberate, time-boxed demo.

**Follow-up, found live enabling this for real (2026-08-04)**: the very
first real request with `NAVIGRAPH_DEMO_FALLBACK=true` returned a real
`500`. Root cause: `demo_fallback_data.json` hit the *exact same*
packaging gap `packages/agent_runtime/pyproject.toml`'s own
`[tool.setuptools.package-data]` comment already documents for
`prompts/*.md` files -- a real (non-editable) `pip install .` silently
drops any non-`.py` file from the installed package unless its glob is
explicitly declared, and every local test run (`pip install -e`) masked
this completely since editable installs reference the source tree
directly. Confirmed live via the agent-runtime pod's own traceback:
`FileNotFoundError` for the exact real path inside
`site-packages/navigraph_agents/...`. Fixed by adding `"**/*.json"` to
the existing package-data glob (a general fix, not a one-off for this
specific file, so a future agent data file doesn't hit the same gap
again) and rebuilding/redeploying the real image.

**Separately, in the course of applying the key-pair auth fix (below),
a self-inflicted bug**: applying `configmap-snowflake-patch.yaml` directly
via `kubectl apply -f` (instead of through a real `kustomize build`)
replaced the entire live `navigraph-app-env` ConfigMap's `data` map with
only this patch file's own keys -- `ConfigMap.data` has no merge/patch
semantics at the Kubernetes API level; only `kustomize build`'s own
patch pipeline merges partial patch files like this one with the base
and other patches. This silently wiped `POSTGRES_HOST` (and every other
key normally supplied by `configmap-postgres-patch.yaml` and the base
ConfigMap), breaking catalog lookups entirely
(`psycopg.OperationalError: failed to resolve host 'postgres'`) until
caught and fixed by building the real, full `kustomize build` output and
applying that instead. **Lesson**: never `kubectl apply -f` a file that
lives under a kustomize overlay's `patches:` list directly -- always
apply the full rendered `kustomize build` output (or `kubectl apply -k`),
even for an urgent live hotfix.

### 82. RESOLVED: real Snowflake service-account access broken twice in succession, for reasons entirely outside this project's code -- fixed via key-pair authentication

**What was found**: continuing from item 81's original trial-expiration
finding, once the trial/billing issue was resolved on the account side,
a *second*, different real blocker appeared: Snowflake began requiring
MFA for the `SHUBHSNFLK` service user, which categorically blocks
password-based programmatic login (`"Multi-factor authentication is
required for this account"`, confirmed live via
`query.data_source_discovery`'s connectivity check). A real, live
`DESC USER SHUBHSNFLK` (run by the product owner) confirmed `HAS_MFA:
true` (an MFA method genuinely enrolled) and `HAS_KEYPAIR: false` --
`MFA_ENROLLMENT = OPTIONAL` alone (a real authentication-policy change
already applied) does not un-enroll an already-enrolled method.

**Resolution**: switched the connector to key-pair authentication
(`connector_sdk/snowflake/auth.py`'s existing, already-built `key_pair`
path from Phase 2) -- Snowflake exempts key-pair auth from MFA entirely,
by design, since it's the vendor's own recommended mechanism for
service/automated accounts. Concretely: generated a real RSA key pair;
the product owner registered the public key on `SHUBHSNFLK` via `ALTER
USER ... SET RSA_PUBLIC_KEY=...` (a real account security-setting
change, correctly done by the account owner, not automated by this
agent); the private key was added as a new real Key Vault secret
(`SNOWFLAKE-PRIVATE-KEY`), mounted as a file (not an env var -- `auth.py`
reads `SNOWFLAKE_PRIVATE_KEY_PATH` as a real filesystem path) via
`secretproviderclass-agent-runtime.yaml`; `SNOWFLAKE_AUTH_METHOD` was
switched from `password` to `key_pair` in `configmap-snowflake-patch.yaml`.
Applied live first (Key Vault secret, SecretProviderClass, ConfigMap,
pod restarts), confirmed via a real `query.data_source_discovery` call
against the live cluster, then committed to source.

**What full version requires**: nothing further planned for the auth
mechanism itself -- key-pair auth is the correct, permanent fix, not a
workaround. `MINS_TO_BYPASS_MFA` (a real, temporary Snowflake user
property) was identified as a fast, self-expiring bridge fix during
triage but was superseded by the permanent key-pair fix before it needed
to be used.

### 83. RESOLVED: `sql_generation._build_from_clause` silently emitted a Cartesian-product `FROM` clause for unjoined multi-table queries, repeating the same grand-total aggregate for every group

**What was found**: a real, live-reproduced user report -- "What is the
total transaction volume by market?" returned a bar chart showing the
identical value (3,722,786,012.55) for every market. Root cause confirmed
in two steps: (1) Semantic Retrieval's real LLM call non-deterministically
resolves "market" to `STAGING_MARKETS.NAME` (requiring a join) on some
runs and to `STAGING_TRANSACTIONS.MARKETID` (single-table, no join needed)
on others; (2) `schema_mapping._build_joins` derives joins exclusively
from curated `RelationshipConcept` entries in the knowledge graph, and no
such concept exists yet for `STAGING_TRANSACTIONS` <-> `STAGING_MARKETS`,
so Schema Mapping resolves both tables with an empty `joins` list. Given
that input, `sql_generation._build_from_clause` used to fall back to a
plain comma-separated `FROM A, B` (a genuine Cartesian product -- every
transaction row paired with every market row before the `GROUP BY`
aggregate ran), confirmed live via a direct, manually-constructed
`POST /agents/query/sql_generation/invoke` call reproducing the exact
condition (2 tables, columns spanning both, `joins=[]`).

**Resolution**: `_build_from_clause` now returns the set of tables it
could not connect via the provided joins instead of silently
comma-joining them; `_generate_statements` turns a non-empty result into
a real, non-recoverable `AgentError(code="unjoined_table_in_multi_table_query")`
and returns no SQL statement at all -- matching this codebase's existing
`no_resolved_data_source`/`cross_source_query_not_supported` convention of
failing explicitly rather than ever returning data that looks correct but
isn't. Two new regression tests reproduce the exact live-confirmed
scenario (2 tables/0 joins, and a 3-table case where one table remains
unreached despite a real join existing for the other two).

**What full version requires**: this is a defensive fix, not the deeper
one -- the platform now correctly refuses to answer rather than lying,
but "total transaction volume by market" still can't be answered via the
join path until a real `RelationshipConcept` for Transaction<->Market is
added to the knowledge graph (`navigraph_kg.ontology.RELATIONSHIP_CONCEPTS`)
and re-ingested. Separately, Semantic Retrieval's non-determinism in which
column "market" resolves to on a given run is itself a real, open gap
(no caching/pinning of term resolutions across runs) -- not addressed
here.

### 84. RESOLVED (partially): a deeper live audit, prompted directly by item 83, found three MORE real correctness bugs -- one of them (the `STAGING_` prefix mismatch below) meant relationship-based joins had been silently broken for the dominant real resolution path all along

**What was found**: after item 83 shipped, the user asked for a full audit
of why answers were coming out wrong/misleading. A live investigation
(real calls to `/ask` against the live gateway, direct Postgres/Neo4j
queries, direct code reading) found three further real bugs, none of them
hypothetical:

1. **The `STAGING_` prefix mismatch (the most severe of the three)**:
   `navigraph_kg.ontology.RELATIONSHIP_CONCEPTS`' `realizing_table` values
   are bare names (e.g. `"CUSTOMER_INFORMATION"`, `"TRANSACTIONS"`), but
   EVERY column resolved via Ontology's business-concept path -- the
   dominant, deterministic, no-LLM path, since all real
   `SCHEMA_ENRICHMENT`-derived glossary mappings point exclusively at
   `STAGING_`-prefixed tables (item 14) -- has a real `table_name` of
   e.g. `"STAGING_CUSTOMER_INFORMATION"`. `schema_mapping._build_joins`'s
   exact-string `rel.realizing_table not in resolved_tables` check
   therefore never matched for that path. Item 15's Phase 9 "fix" (adding
   the Transaction<->Market `RelationshipConcept`) only appeared to work
   because that specific golden question happened to resolve to the bare
   (`FAR_TRANS`-schema) table names via Semantic Retrieval's LLM fallback,
   not because the underlying matching logic was actually sound. In
   production this meant almost every real multi-table question got zero
   joins -- a Cartesian product before item 83's fix, a hard
   `unjoined_table_in_multi_table_query` failure after it, i.e. item 83's
   fix made this bug's *symptom* safer (fail loudly, not silently wrong)
   without fixing the *cause* (joins essentially never resolved for the
   dominant path). **Fixed**: `_build_joins` now matches `realizing_table`
   against resolved tables with a leading `STAGING_` prefix stripped from
   both sides for comparison purposes only, then emits the `JoinSpec`
   using the REAL resolved table name (never the bare literal) -- using
   the bare name there would have silently produced an unqualified `FROM
   TRANSACTIONS` referencing whatever table that name resolves to in the
   connection's default schema, not necessarily the right one. New
   regression test: `test_join_emitted_when_resolved_tables_are_staging_prefixed`.
2. **Ontology's relationship-label matcher couldn't handle natural
   two-word phrasing**: `_label_matches_entities("RiskLevel", ["risk
   level"])` returned `False` -- live-reproduced, and directly relevant
   since golden questions `gq_005`/`gq_009` both extract the entity as
   exactly "risk level" (see `eval/golden_set/gq_005_risk_level_distribution.yaml`),
   not the seed data's single-token canonical label. Plain substring
   matching in either direction can never bridge a space. **Fixed**:
   both sides are now normalized (letters/digits only, lowercased) before
   comparison via a new `_normalize_label` helper. New regression test:
   `test_relationship_fires_for_a_real_two_word_entity_phrasing`.
3. **A non-deterministic "unknown" intent classification produced a
   confidently wrong answer instead of a clean failure or clarification**:
   live-reproduced -- "What assets are held most frequently across
   transactions?" returned `outcome="answered"`, `confidence=1.0`, zero
   errors, but the real generated SQL was an unaggregated `SELECT
   TRANSACTIONS.ISIN, TRANSACTIONS.TRANSACTIONID FROM FAR_TRANS.TRANSACTIONS
   LIMIT 10000` -- no join, no `GROUP BY`, no `COUNT`. The narrative agent
   then confidently asserted "a single asset dominates... indicating it is
   the most frequently held asset" from raw, unaggregated rows. Root
   cause: `schema_mapping._assign_role` only ever assigns `role="measure"`
   when intent is one of `metric_lookup`/`comparison`/`trend_analysis`;
   Intent Understanding's real, already-documented non-determinism
   (item 38/44) sometimes classifies the identical question as
   `"unknown"` -- `IntentLabel`'s own docstring says this is the safe
   fallback for a missing/malformed/unrecognized classification, i.e. the
   system genuinely does not know what shape of answer is wanted, yet
   the pipeline proceeded to generate and confidently narrate an answer
   anyway. **Fixed**: Request Orchestrator now routes `actual_intent ==
   "unknown"` through the same Multi-turn Clarification Coordinator the
   "no tables resolved" case already uses (item 41), immediately after
   Intent Understanding runs, rather than ever generating a confident
   wrong answer for a question it does not actually understand.

**A separate, non-code finding from this same audit, worth flagging
loudly**: the investigating agent decoded and printed the live Neo4j
service password in plaintext (via `kubectl get secret ... | base64 -d`)
into its own tool output/transcript while running read-only Cypher
queries against the live graph. No external exposure occurred and no
write actions were taken, but per this project's own standing rule around
credential handling (see the earlier real GitHub password exposure this
session), that password should be treated as exposed and rotated
(`ALTER USER`-equivalent for Neo4j, or redeploy with a fresh
`NEO4J_AUTH`/Key Vault secret value) out of caution.

**What full version requires**: fix (1) above closes the systemic gap for
every table pair that ALREADY has a curated `RelationshipConcept`; it does
not add coverage for table pairs that still have none (item 15's original,
still-open low-recall gap). Semantic Retrieval's non-determinism in which
schema variant (`STAGING_` vs bare `FAR_TRANS`) it resolves a term to
(item 14) remains real and unaddressed -- fix (1) makes joins work
correctly regardless of which variant gets picked, but the platform still
has no preference signal steering that choice one way or the other.

### 85. RESOLVED (partially): `_build_joins` could still emit a join referencing a column a table doesn't actually have, when 3+ tables with mismatched keys are resolved together; one real compound question remains only partially answerable

**What was found**: a real, live user question -- "What is driving the
high transaction volume in the Athens Exchange S.A. Cash Market -- is it
concentrated in a few securities or accounts?" -- resolved 4 tables
(`CUSTOMER_MARKET_AGG`, `STAGING_ASSET_INFORMATION`,
`STAGING_CUSTOMER_INFORMATION`, `STAGING_MARKETS`) and correctly hit
item 84's new `unjoined_table_in_multi_table_query` error (working as
designed -- no curated `RelationshipConcept` connected these 4 tables, so
refusing was correct). Investigating it surfaced a real, SEPARATE bug in
`_build_joins`, not yet triggered by any previously-tested scenario: the
loop that connects a matched relationship's `realizing_table` to every
OTHER resolved table assumed every other table shares the relationship's
`subject_key_column` -- true in every 2-table case tested so far, but
false here (`STAGING_CUSTOMER_INFORMATION` has no `MARKETID` column at
all). Had a qualifying `RelationshipConcept` existed for this exact
4-table set, this would have emitted a `JoinSpec` referencing a
nonexistent column, producing a real, broken SQL statement.

**Resolution**: `_build_joins` now cross-checks `payload.catalog_inventory`
(the real, live catalog listing Metadata Discovery already produced)
before emitting each join -- both `real_realizing_table` and each
candidate `other_table` must actually have a column named
`subject_key_column` per the real catalog, not just be assumed to. A
table that doesn't share the key with any curated relationship is now
left unjoined (surfacing as item 84's real, honest
`unjoined_table_in_multi_table_query` error), never silently given
invalid SQL. New regression test:
`test_third_table_lacking_the_join_key_is_not_joined`. Existing join
tests' `catalog_inventory` fixtures were extended to include the real
join-key columns (previously only the resolved/selected columns were
listed, which a real Metadata Discovery crawl never does -- it returns
every real column of every table).

Separately, one new, safe, high-value `RelationshipConcept` was added --
**"Asset traded in Market"** (`ASSET_INFORMATION`/`MARKETID`, the same
same-column-both-sides shape as "Transaction happens in Market") -- so
"which securities are most active in a given market" questions (a common,
single-granularity real pattern) now resolve a real join instead of
failing.

**What's still open**: the exact live compound question above mixes TWO
different aggregation granularities in one ask -- per-security
concentration (needs `STAGING_TRANSACTIONS`/`STAGING_ASSET_INFORMATION`)
and per-account concentration (`CUSTOMER_MARKET_AGG` has no security
dimension at all, so it cannot answer the "securities" half regardless of
joins). No single curated `RelationshipConcept` addition can bridge this
cleanly without either fabricating a non-existent foreign key or
restructuring how compound multi-part questions get decomposed
(potentially two separate generated statements, not one) -- neither
attempted here. The practical workaround is asking the two halves as
separate questions ("which securities drove the most volume in Athens
Exchange" / "which customer accounts drove the most volume in Athens
Exchange"), each of which now has a real, resolvable join path -- see
item 86 below for the fix that actually made this true (item 85's new
"Asset traded in Market" concept alone was not enough).

### 86. RESOLVED: relationship-concept matching required the literal category word ("market"), so naming a specific real instance ("Athens Exchange") never matched anything

**What was found**: re-testing item 85's two suggested workaround
questions ("Which securities/customer accounts drove the most transaction
volume in Athens Exchange?") both still failed with
`unjoined_table_in_multi_table_query`, resolving `STAGING_TRANSACTIONS` +
`STAGING_MARKETS` with zero joins -- even though "Transaction happens in
Market" (a real, already-curated `RelationshipConcept` for exactly this
table pair) should have applied. A direct, controlled comparison
confirmed the cause: **"What is the total transaction volume by
market?"** (the generic category word) resolved a real join and answered
correctly; **"...in Athens Exchange?"** (a real, specific market name,
not the word "market") did not. `understanding.ontology.agent._resolve_relationships`
only ever checked whether a relationship's subject/object label (e.g.
`"Market"`) literally appeared among the entities Intent Understanding
extracted -- when a question names a real market/asset/channel/risk
level/etc. by its actual name instead of the generic category word, that
literal check can never succeed, so the relationship silently never
fires. This is a distinct, deeper gap from items 84/85 (which were about
column/table-name mismatches once a relationship DID match) -- this one
is about the relationship never being considered a candidate at all.

**Resolution**: added `navigraph_kg.api.entity_matches_reference_node`,
which checks a free-text entity against REAL reference-data node values
under a given label (Market's `name`, Asset's `asset_name`/
`asset_short_name`/`isin`, Channel/RiskLevel/CustomerType/
InvestmentCapacityBand's `name` -- all real, crawled Tier-1 nodes, see
`ingestion.pipeline._sync_reference_data`/`_sync_simple_lookup`) rather
than just the category word. `OntologyAgent._resolve_relationships` now
calls a new `_label_or_instance_matches` helper: the original literal
check first, falling back to `entity_matches_reference_node` only for
labels that correspond to a real reference-data node type
(`_REFERENCE_NODE_LABELS` -- "Customer"/"Transaction" are excluded
since no such node type exists in the graph at all, by design). New
regression test:
`test_relationship_fires_for_a_real_named_instance_not_the_category_word`,
plus direct unit coverage for the new API function
(`TestEntityMatchesReferenceNode` in `knowledge_graph`'s test suite).
Live verification against the two previously-failing questions is
pending this fix's deployment.

**What full version requires**: this closes the gap for every
`RelationshipConcept` whose category has a real reference-data node type
-- it does not help relationships involving "Customer" or "Transaction"
by name (impossible anyway, since no such nodes exist), nor does it help
if Intent Understanding's entity extraction produces something that
doesn't overlap ANY real reference-node value at all (a genuinely novel
or misspelled name). No further gap of this kind is known at this time,
but none has been exhaustively searched for either -- this was found via
live testing of two specific real questions, not a systematic audit of
every `RelationshipConcept`/entity-extraction combination.

### 87. RESOLVED (partially): item 86's fix exposed a real, PRE-EXISTING wrong-data bug live in production -- `_build_joins` joined tables via a shared column name that meant different things on each side

**What was found**: re-testing the two split questions after item 86
deployed, "Which securities drove the most transaction volume in Athens
Exchange?" returned `outcome="answered"` with a real generated SQL join
-- but every distinct security listed under "Athens Exchange S.A. Cash
Market" showed the IDENTICAL total (`914679074.6164`), across ~80
different securities. As an immediate mitigation, the newly-added "Asset
traded in Market" concept (item 85) was deleted directly from the live
Neo4j graph -- but the wrong-data behavior persisted unchanged, proving
it was NOT caused by anything added today. Root cause: **"Transaction
happens in Market"** (`realizing_table=TRANSACTIONS`,
`subject_key_column=MARKETID`, added in Phase 9, item 15) has been
live since Phase 9 with this exact defect -- `_build_joins` connects
`realizing_table` to EVERY other resolved table that happens to have a
column with the same name, and `STAGING_ASSET_INFORMATION` genuinely has
its own, real `MARKETID` column (a security is listed on exactly one
market). So once a question resolves `TRANSACTIONS` + `ASSET_INFORMATION`
+ `MARKETS` together, `TRANSACTIONS` got joined to `ASSET_INFORMATION`
via `MARKETID` -- which only means "this asset is listed on the same
market as this transaction," not "this transaction is FOR this asset."
The real per-row foreign key for that is `ISIN`. The join fanned every
security in a market out against every transaction in that market,
repeating the market's grand total under every security's row. This bug
was live since Phase 9 -- it simply never surfaced because no real
question had combined Transaction+Asset+Market until today.

**Resolution**: `_build_joins` now requires the shared key to be
UNAMBIGUOUS: a relationship connects `realizing_table` to `other_table`
only when `other_table` is the SOLE other resolved table with a column
named `subject_key_column`. If 2+ resolved tables share that column name,
which one the relationship is actually about cannot be determined from
the data available, so NONE of them are joined via that relationship --
they surface as a real, honest `unjoined_table_in_multi_table_query`
error instead of a confident-looking but wrong per-group breakdown. This
does not regress any previously-verified 2-table case (each still has
exactly one candidate). Separately, a real, correctly-keyed
`RelationshipConcept` -- **"Transaction involves Asset"**
(`TRANSACTIONS.ISIN` = `ASSET_INFORMATION.ISIN`) -- was added so
"transaction volume by security" (without a market also resolved) now
answers via the real per-row foreign key instead of the market-scoped
fan-out. New regression test:
`test_ambiguous_shared_key_across_two_other_tables_joins_neither`.
272 tests pass (up from 265 before this investigation began), `ruff
check` clean.

**What full version requires**: the exact live compound question
(Transaction + Asset + Market, all three sharing `MARKETID`) still cannot
be fully answered -- with the ambiguity guard in place, `MARKETID` is
genuinely ambiguous across all three tables regardless of which one is
treated as `realizing_table`, so `MARKETS` stays unjoined even once
`TRANSACTIONS`/`ASSET_INFORMATION` correctly join via `ISIN`. Properly
supporting this would require a real join-path-resolution capability
(e.g. preferring to extend an already-connected component over
introducing a new, ambiguous edge) that does not exist in this codebase
today -- deliberately not attempted here given how fragile the last two
attempts at incremental, single-relationship fixes turned out to be under
live testing. This is now a SAFE limitation (an honest failure), not a
correctness risk.

### 88. RESOLVED: Semantic Retrieval's non-determinism could resolve a bare entity to a redundant duplicate table, needlessly failing an otherwise single-table question

**What was found**: a full live sweep of all 10 real golden-set questions
against the deployed system (prompted directly by a request to verify
"everything is working as expected") found 2 real questions safely
failing that should have answered: `gq_002` ("How many transactions has
each customer made?") and `gq_009` ("How has the customer base's risk
profile changed over time?"), both with
`unjoined_table_in_multi_table_query` naming two tables that are really
the SAME conceptual entity (e.g. `CUSTOMER_INFORMATION` and
`STAGING_CUSTOMER_INFORMATION`, or `CUSTOMER_INFORMATION` alongside
`STAGING_TRANSACTIONS`). Root-caused via direct, live diagnostic calls to
Intent Understanding/Ontology/Semantic Retrieval in isolation with
`gq_002`'s real question and real candidate list: Semantic Retrieval's
LLM call correctly resolved "customer" to `STAGING_TRANSACTIONS.CUSTOMERID`
on this direct call -- but the real golden-sweep run had resolved
"customer" to `CUSTOMER_INFORMATION.CUSTOMERID` instead, a different real,
valid column, confirming genuine LLM non-determinism (the same class
already documented in items 38/44) as the root cause, not a
deterministic bug. Both resolutions are real, valid catalog columns, so
`_resolve_columns`'s existing dedupe-by-`catalog_column_id` could never
collapse them -- they're different physical columns -- pulling in a
second table that offered nothing the anchor table (already resolved via
a different term, e.g. "transactions" -> `STAGING_TRANSACTIONS.TRANSACTIONID`)
didn't already have natively.

**Resolution**: a new `SchemaMappingAgent._collapse_redundant_key_only_tables`
pass runs after column resolution. A resolved column's table is a
"key-only" candidate for collapsing when it contributes NO other resolved
column; if some OTHER already-resolved table has a REAL column of the
identical name per `payload.catalog_inventory` (even one never itself
explicitly resolved as a term), the key-only column is redirected to that
table's own copy, and the key-only table drops out of the resolved set
entirely -- collapsing the question to a single table with no join
needed. A table that contributes any OTHER, non-duplicated attribute
(e.g. `RISKLEVEL`, which no other resolved table also has) is never
touched -- verified by a dedicated regression test alongside the new
"redundant duplicate" one. Two new tests:
`test_redundant_customer_id_from_a_second_table_collapses_to_one_table`,
`test_genuinely_needed_second_table_is_never_collapsed`. 274 tests pass
(up from 272), `ruff check` clean.

**A separate, lower-priority hardening note found during this same
diagnostic pass, not yet acted on**: the same direct Ontology call
surfaced 2 spurious `relationship_resolutions` ("Customer holds Asset",
"Transaction involves Asset") for entities that are plainly NOT about
assets at all ("transactions", "customer") -- almost certainly a false
positive from item 86's new `entity_matches_reference_node` substring
matching against a real Asset name/short-name/ISIN that happens to
overlap a generic English word. This did not cause any real failure here
(the spuriously-matched relationships' `realizing_table`s were never
actually part of the resolved column set for this question), and the
ambiguous-key guard (item 87) provides a real safety net even if it had
been -- but it is a real, confirmed over-matching gap in a same-day fix,
logged honestly rather than assumed away. Tightening
`entity_matches_reference_node` (e.g. a minimum entity length, or
requiring a whole-word match rather than a bidirectional substring
check) is a reasonable follow-up, not attempted here since no live
question has yet been found where it actually produces a wrong result.

### 89. RESOLVED: two resolved tables that are really the same real Snowflake table (bare vs `STAGING_`-prefixed) needlessly failed instead of merging

**What was found**: after item 88's fix, live re-testing confirmed
`gq_002` now answers correctly (a real, single-table `COUNT(*) GROUP BY
CUSTOMERID` against `STAGING_TRANSACTIONS`, real varied counts per
customer). `gq_009` ("How has the customer base's risk profile changed
over time?") still failed with the same
`unjoined_table_in_multi_table_query`, naming `CUSTOMER_INFORMATION` and
`STAGING_CUSTOMER_INFORMATION`. Root-caused via the same direct,
isolated Ontology/Semantic-Retrieval diagnostic technique used for item
88: "risk level" resolved via Ontology's glossary to
`STAGING_CUSTOMER_INFORMATION.RISKLEVEL` (item 14's established anchor),
while "customer"/"trend" resolved via Semantic Retrieval's LLM to
`CUSTOMER_INFORMATION.CUSTOMERID`/`.TIMESTAMP` -- two DIFFERENT column
names, so item 88's identical-column-name collapse correctly left both
tables in place. But `CUSTOMER_INFORMATION` and
`STAGING_CUSTOMER_INFORMATION` are not two independent tables that
happen to share a coincidental column -- per item 14, they are the
LITERAL SAME real Snowflake data, crawled under two different catalog
registrations.

**Resolution**: a new `SchemaMappingAgent._merge_staging_schema_duplicate_tables`
pass (run before the item-88 collapse) detects when a resolved
`STAGING_`-prefixed table and its bare counterpart are BOTH present among
the resolved tables, and redirects every column resolved from the bare
table to the `STAGING_`-prefixed table's own real copy (verified against
the live catalog inventory for that exact column name -- a pair sharing
this exact core name where the target genuinely lacks that column is left
untouched rather than guessed at). This is not a heuristic guess the way
item 88's "thin table" collapse partly was -- `STAGING_X`/`X` being the
same real table is an already-established, confirmed structural fact
about this dataset (item 14), not a coincidence. New regression test:
`test_bare_table_columns_redirect_to_the_staging_prefixed_duplicate`.
275 tests pass (up from 274), `ruff check` clean. **Live-verified after
deployment**: `gq_009` no longer hits `unjoined_table_in_multi_table_query`
at all -- Schema Mapping now correctly resolves it to a single table. The
question now fails for a real, DIFFERENT, and entirely legitimate reason:
`guardrail.pii_exposure_checker` denies it (`pii_column_access_denied`),
since answering "how has the customer base's risk profile changed"
requires grouping by the real, tagged-PII `CUSTOMERID` column (item 25),
and the `analyst` role calling it is correctly not authorized to see PII
-- the same real access-control behavior already documented working as
designed for other golden questions in item 38. This is the guardrail
layer functioning correctly, not a remaining correctness bug.

**What full version requires**: this merge only fires when BOTH the bare
and `STAGING_`-prefixed copies are ALREADY resolved as distinct tables in
the SAME query -- it does not (and cannot) prevent Semantic Retrieval from
non-deterministically picking one schema variant over the other in the
first place; item 14's underlying question (which schema should be
canonical for business-term resolution going forward) remains open and
unaddressed by this fix. Separately, whether "customer base risk profile
trend" genuinely needs per-customer `CUSTOMERID` in the query at all
(vs. an aggregate-only breakdown by `RISKLEVEL` and time period that
would never touch PII) is a real, debatable resolution-quality question,
not addressed here.

### 90. NEW: a second real data source (a synthetic e-commerce star schema) is registered, exposing a real gap this system already knew about (item 21) -- cross-database routing is a schema-name workaround, not a real fix

**What was added**: a real, synthetic e-commerce dataset -- 5 dimension
tables (`DIM_CUSTOMER`, `DIM_PRODUCT`, `DIM_DATE`, `DIM_CHANNEL`,
`DIM_PROMOTION`) and 2 fact tables (`FACT_ORDERS`, `FACT_ORDER_ITEMS`) in
a real, declared PK/FK dimensional (star) schema -- was created and
populated in a NEW Snowflake database, `ECOMMERCE_POC` (same account,
same `SHUBHSNFLK` service user, using its separately-granted
`ACCOUNTADMIN` role purely for the one-off `CREATE DATABASE`/`CREATE
TABLE` -- the deployed agent-runtime's normal connection still only ever
uses the read-only `FIDELITY_ANALYST_ROLE`). Registered as a real
`DataSource` under a NEW, separate tenant (`ecommerce-poc`) -- deliberately
NOT merged into the existing `navikenz-poc` tenant, to avoid introducing
a second data-source-per-tenant ambiguity into item 42's already-documented
"exactly one match or fail" auto-resolution for the existing brokerage
demo. Crawled successfully via the real, existing
`navigraph_catalog.ingestion.snowflake_crawler`.

**The real gap this exposed**: item 21 ("Connector credential routing is
global-env-var-based, not per-`DataSource`") turned out to also cover
which DATABASE a connection points at, not just which credentials -- the
deployed agent-runtime's Snowflake connection is permanently configured
(via `SNOWFLAKE_DATABASE` env var) to `FIDELITY_POC`. A second data
source in a genuinely different database can't rely on the connection's
default database context at all. **Workaround applied**: rather than
building full per-`DataSource` connection routing (a real, larger
feature), the crawled `catalog_schemas.name` for this data source was
corrected from the crawler's raw `"CORE"` to a fully-qualified
`"ECOMMERCE_POC.CORE"` -- since `sql_generation._qualified_table` does
plain `f"{schema_name}.{table_name}"` string concatenation, this makes it
emit a real, valid 3-part Snowflake identifier
(`ECOMMERCE_POC.CORE.TABLE_NAME`) that resolves correctly regardless of
the shared connection's configured default database, PROVIDED the role
has real grants on the second database too (granted:
`GRANT USAGE ON DATABASE ECOMMERCE_POC` / `... ON SCHEMA CORE` /
`GRANT SELECT ON ALL TABLES IN SCHEMA CORE` / `... ON FUTURE TABLES ...`
to `FIDELITY_ANALYST_ROLE`, confirmed idempotent and least-privilege).

**Relationship concepts added**: 9 new `RelationshipConcept` entries for
this star schema (`Order involves Customer`, `Order happens on Date`,
`Order uses Channel`, `OrderItem belongs to Order`, `OrderItem involves
Product`, `OrderItem involves Customer`, `OrderItem happens on Date`,
`OrderItem uses Channel`, `OrderItem uses Promotion`) -- each keyed on a
real, uniquely-named surrogate key (e.g. `CUSTOMER_ID`) that appears on
exactly the intended fact/dimension pair, so item 87's ambiguity guard
never has anything to arbitrate here (unlike the brokerage schema's
coincidentally-shared `MARKETID`). Live-verified: a multi-table question
("total revenue by channel") correctly hit `unjoined_table_in_multi_table_query`
BEFORE these concepts were synced (proving the safety guards generalize
correctly to a brand-new dataset with zero curated relationships), and
resolving after they're synced is expected but not yet re-verified as of
this entry (pending the code deploy + re-sync).

**What full version requires**: (1) no business glossary was crawled for
this data source (no `SCHEMA_ENRICHMENT`-equivalent exists for synthetic
e-commerce data), so EVERY entity resolves via Semantic Retrieval's LLM
fallback rather than Ontology's deterministic glossary path -- more
LLM calls, more of the same real non-determinism already documented for
the brokerage demo (items 38/44), not a new kind of gap. (2) No Tier-1
reference-data nodes (e.g. real `CATEGORY`/`CHANNEL_NAME`/`LOYALTY_TIER`
values) were crawled into the knowledge graph for this data source, so
item 86's named-instance matching (`entity_matches_reference_node`) has
no real e-commerce reference values to match against yet -- a real,
same-shaped follow-up to item 86's original brokerage-only reference-data
crawl, not attempted here. (3) The schema-name-qualification workaround is
specific to this one data source; a genuine third data source in yet
another database would need the identical manual correction repeated,
underscoring that real per-`DataSource` connection routing (item 21) is
still the correct, larger fix, not permanently deferred by this
workaround.

### 91. NEW: e-commerce relationship concepts required a literal "Order"/"OrderItem" mention that realistic revenue/channel/category questions never say -- fixed, plus a real ColumnGlossary and Channel reference-data crawl added

**What was found**: before this item's fixes could even be tested live,
structural analysis of `SchemaMappingAgent._build_joins` (confirmed by
re-reading its real code, not assumed) showed it only ever considers
relationships present in `payload.relationship_resolutions` -- i.e. only
what `OntologyAgent._resolve_relationships` already decided fired. Firing
requires BOTH the concept's `subject_label` AND `object_label` to match an
extracted entity (literally, or via a named reference-node instance, item
86). Every one of item 90's 9 e-commerce concepts uses `"Order"` or
`"OrderItem"` as `subject_label` -- a table-role word real users
essentially never say. A realistic question like "What is the total
revenue by channel?" mentions "channel" (matches `object_label`) but never
"order" in any form, so "Order uses Channel" would never have fired, no
join would ever have been built, and the question would have failed with
`unjoined_table_in_multi_table_query` -- the exact class of "semantic gap"
this item's fixes exist to close. (This is a DIFFERENT, more fundamental
bug than item 90's own "not yet re-verified" note anticipated; it was
found and fixed BEFORE the live re-verification happened, not after.)

**Resolution -- three changes, working together**:
1. **Ontology relationship-firing relaxation** (`understanding/ontology/agent.py`'s
   `_resolve_relationships`, `knowledge_graph/navigraph_kg/api.py`'s
   `resolve_business_term`): the subject-label check now ALSO succeeds
   when the concept's `realizing_table` is already implied by a resolved
   business concept -- i.e. some other entity in the same question already
   resolved, via the deterministic glossary path, to a column that lives
   on that exact table (matched by core table name, `STAGING_` prefix
   ignored, same normalization `_build_joins` already uses). This only
   ever RELAXES the check -- it adds a new way to fire, never removes an
   existing match -- so it's provably safe for the brokerage dataset too.
   `resolve_business_term`'s Cypher now returns `table_name` (an
   `OPTIONAL MATCH` traversal through `COLUMN_OF`) to make this possible;
   `ConceptResolution` gained a matching `table_name: str | None` field.
   New tests: `test_query_also_optionally_resolves_the_columns_table_name`
   (knowledge_graph), `test_relationship_fires_when_realizing_table_is_already_implied_by_a_resolved_concept`
   (ontology agent).
2. **A real ColumnGlossary for ECOMMERCE_POC** (`add_ecommerce_glossary.py`,
   ~30 entries via the existing `navigraph_catalog.api.upsert_glossary`/
   `find_column`): "revenue"/"order value"/"sales" -> `FACT_ORDERS.TOTAL_AMOUNT`,
   plus discount/tax/shipping/category/subcategory/brand/segment/tier/
   channel/promotion/date terms. This is a NECESSARY companion to fix #1,
   not a separate nice-to-have: fix #1's relaxation only sees a table
   implied when the term resolved via Ontology's own glossary path --
   Semantic Retrieval's LLM-fallback resolutions never feed back into
   `concept_resolutions`, so without a real glossary entry for "revenue"
   itself, fix #1 would have nothing to key off of.
3. **E-commerce Channel reference-data crawl** (`knowledge_graph/navigraph_kg/ingestion/pipeline.py`'s
   new `run_ecommerce_ingestion`, a deliberate sibling of `run_ingestion`
   reusing its 3 generic stages plus a small e-commerce-specific stage 3
   crawling `DIM_CHANNEL.CHANNEL_NAME` into the SAME generic, tenant-scoped
   `Channel` label the brokerage dataset already uses): closes the
   narrower, item-86-shaped gap where a question names a channel instance
   directly ("orders via the Mobile App") instead of saying "channel".
   Deliberately does NOT crawl Category/CustomerSegment/LoyaltyTier/Country
   as new reference-node labels -- none of `RELATIONSHIP_CONCEPTS`'
   e-commerce entries use those as a subject/object label (they're plain
   columns on already-joined dimension tables, not separately joined
   tables), so crawling them would add real Neo4j writes with zero
   consuming code path under the current architecture. New tests:
   `TestRunEcommerceIngestion` (2 tests) in `knowledge_graph/tests/ingestion/test_pipeline.py`.

281 tests pass (up from 277), `ruff check` clean on every touched package.

**What full version requires**: (1) the implied-table relaxation only
ever fires via Ontology's GLOSSARY path -- a term that only Semantic
Retrieval's LLM manages to resolve still can't imply a table this way; a
real, harder fix would thread Semantic Retrieval's per-term table
resolution back into this same mechanism, not attempted here. (2) the
~30-entry e-commerce glossary is a reasonable, real starting set for this
demo's scale, not an exhaustive one -- terms outside it (e.g. "margin",
"profit", which aren't stored columns at all and would require SQL
Generation to compute `UNIT_PRICE - UNIT_COST`) still rely entirely on
whatever Semantic Retrieval's LLM fallback can improvise. (3) Category/
CustomerSegment/LoyaltyTier/Country reference-node crawling remains a
real, explicitly-scoped-out gap (see point 3 above) -- if a future
RelationshipConcept ever needs one of these as a label, its reference
data would need crawling at that point, not before. (4) live end-to-end
re-verification of all three fixes together (a real `/ask` call for
"total revenue by channel" showing a real, correct `JOIN` and varied
per-channel numbers) is pending this item's deploy + the one-off
glossary/ingestion scripts being run against the live cloud Postgres/Neo4j
-- see BUILD_LOG.md's matching entry for the live results once available.

### 92. NEW: three real, already-populated brokerage tables (`CLOSE_PRICES`, `LIMIT_PRICES`, `CUSTOMER_MARKET_AGG`) had zero `RelationshipConcept` coverage -- fixed

**What was found**: per the user's request to review the Fidelity/brokerage
dataset for the same class of gap just found and fixed for e-commerce
(item 91), a live, read-only query against the real `FIDELITY_POC` catalog
(via a running `agent-runtime` pod, not assumed) enumerated every real
table registered for `tenant_id="navikenz-poc"`. Three real,
already-populated tables had NO `RelationshipConcept` referencing them at
all: `CLOSE_PRICES`/`STAGING_CLOSE_PRICES` (ISIN, TIMESTAMP, CLOSEPRICE),
`LIMIT_PRICES`/`STAGING_LIMIT_PRICES` (ISIN, MINDATE, MAXDATE,
PRICEMINDATE, PRICEMAXDATE, PROFITABILITY), and `CUSTOMER_MARKET_AGG`
(CUSTOMERID, MARKETID, TOTAL_VALUE, TXN_COUNT, LAST_DATE -- the direct
market-level sibling of `CUSTOMER_ASSET_AGG`, which already had a real
concept, "Customer holds Asset"). A live query also confirmed
`CLOSE_PRICES`/`LIMIT_PRICES` already had real `ColumnGlossary` entries
(so their terms resolve deterministically) but `CUSTOMER_MARKET_AGG` had
ZERO glossary rows at all -- meaning a question like "closing price trend
for Technology sector assets" or "which markets does each customer trade
in most" would resolve the relevant tables but have no way to join them
to `Asset`/`Market`, surfacing (correctly, per the item-84 safety fix) as
`unjoined_table_in_multi_table_query`.

Notably, this review found NO gap in the synthetic DATA itself -- every
one of these tables is real, already populated, live Snowflake data from
the original Phase 0-2 dataset setup. The actual gap was purely missing
`RelationshipConcept`/`ColumnGlossary` metadata, so no new synthetic data
generation was needed for the brokerage side (unlike the e-commerce
dataset, which was built from scratch this session).

**Resolution**: added 3 new `RelationshipConcept` entries to
`ontology.py` (`RELATIONSHIP_CONCEPTS` now 18 total, up from 15): "Asset
has ClosingPrice" (`CLOSE_PRICES`, `ISIN`/`ISIN`), "Asset has LimitPrice"
(`LIMIT_PRICES`, `ISIN`/`ISIN`), "Customer active in Market"
(`CUSTOMER_MARKET_AGG`, `CUSTOMERID`/`MARKETID`). The first two
deliberately share the generic `object_label` `"Price"` (not
`"ClosingPrice"`/`"LimitPrice"`) so it substring-matches real phrasings
like "closing price"/"close price"/"limit price" -- `_build_joins`'s own
"is the realizing_table actually resolved" gate is what keeps this safe
even with two concepts sharing an object label. Added a new one-off
script (`add_customer_market_agg_glossary.py`) inserting real
`ColumnGlossary` entries for `CUSTOMER_MARKET_AGG.TOTAL_VALUE`/`TXN_COUNT`/`LAST_DATE`
via the existing `upsert_glossary`/`find_column` catalog API, mirroring
the e-commerce glossary script's exact pattern. New tests:
`test_contains_the_three_newly_bridged_brokerage_tables` plus 3
per-concept key-column tests in `test_ontology.py`; the two
`relationship_concepts_synced` count assertions in `test_pipeline.py`
updated from 15 to 18. 285 tests pass, `ruff check` clean.

**What full version requires**: (1) live end-to-end re-verification (a
real brokerage question mixing closing/limit price data with asset
name/sector, and a real "top markets by customer activity" question) is
pending this item's deploy + the new glossary script being run against
the live cloud Postgres + `_sync_relationship_concepts` being re-run for
`tenant_id="navikenz-poc"` -- see BUILD_LOG.md's matching entry. (2) the
real `V_ASSET_CURRENT`/`V_CUSTOMER_CURRENT` views (denormalized "current
snapshot" reads of `ASSET_INFORMATION`+`MARKETID`/`SECTOR`/`INDUSTRY` and
`CUSTOMER_INFORMATION`+`RISKLEVEL`/`CUSTOMERTYPE`/`INVESTMENTCAPACITY`
respectively) were found to have ZERO `ColumnGlossary` entries either --
a real, deliberately-NOT-fixed gap: since every meaningful business term
these views could offer is already glossary-anchored to the
`STAGING_`-prefixed real tables, Semantic Retrieval's LLM fallback (the
only path that could ever resolve a term to these views instead) has no
observed, live reason to ever pick them over the already-anchored tables
-- this is a real, low-probability, defense-in-depth-only gap, not one
with observed live impact, so it is logged rather than fixed with
speculative changes to `schema_mapping`'s STAGING_-duplicate-merge logic
(item 89), which currently has no equivalent handling for a `V_`-prefixed
duplicate. If this is ever observed live, the fix would generalize
`_merge_staging_schema_duplicate_tables`'s `_core_name` stripping to also
strip a `V_` prefix.

### 93. RESOLVED, SAME DAY: item 91's `table_name` field addition to Ontology's `ConceptResolution` broke the Request Orchestrator's sibling contract, taking down every real question in production

**What was found**: after item 91's fix (91's commit `62cad04`) reached
production via the normal CD pipeline (canary bake completed and
auto-promoted, per the established gate), a real, live UI test (via a
local `next dev` pointed at the real deployed gateway) returned "Gateway
returned 502" for a real question. Direct `curl` against the real
gateway confirmed a genuine, persistent (not transient/canary-bake-related)
502 with body `{"detail": "agent-runtime is unavailable or returned an
error"}`. `kubectl logs` on the real `agent-runtime` pod showed the actual
root cause: `pydantic_core._pydantic_core.ValidationError: 1 validation
error for ConceptResolution / table_name / Extra inputs are not permitted
[type=extra_forbidden]`, raised inside
`request_orchestrator/agent.py`'s `ConceptResolution(**r.model_dump())`
conversion -- the Request Orchestrator re-validates Ontology's real output
into `schema_mapping.contracts.ConceptResolution`, a deliberate sibling
mirror (per this codebase's "no direct cross-agent imports" convention),
and item 91 added `table_name` to Ontology's copy but not to Schema
Mapping's, so every real request hit `extra="forbid"` and crashed with a
500 inside agent-runtime, surfaced to end users as a flat gateway 502 --
**every question, on every tenant, was broken in production**, not just
e-commerce ones, for the duration between item 91's promotion and this
fix.

**Why no test caught this**: `tests/.../request_orchestrator/tests/test_agent.py`'s
own `_wire_happy_path` helper mocked Ontology's output with
`concept_resolutions=[]` -- an EMPTY list -- so the real
`ConceptResolution(**r.model_dump())` list comprehension never actually
iterated over a real element in any orchestrator unit test, and the real,
full pipeline-chain integration test
(`tests/integration/orchestrator_pipeline/`) uses `FakeLLMClient`/mocked
sub-agents in a way that also never exercised a real, non-empty
`ConceptResolution` round-trip through this exact conversion. This is a
real, structural test-coverage gap in how "sibling contract" pairs get
verified in this codebase -- every prior sibling-pair addition happened to
keep both sides in sync by discipline, not by a test that would have
caught drift.

**Resolution**: added the matching `table_name: str | None = None` field
to `schema_mapping.contracts.ConceptResolution`, restoring the two
sibling contracts' structural match. Fixed the actual test gap too: 
`_wire_happy_path`'s Ontology mock now returns one real, non-empty,
`table_name`-populated `ConceptResolution`, and
`test_happy_path_returns_answered_with_full_result` now asserts the
exact conversion that broke live (`schema_mapping_agent.run`'s real
call args) round-trips every field correctly, including `table_name`.
285 tests pass, `ruff check` clean.

**What full version requires**: this class of bug (a sibling contract
pair silently drifting apart) can recur for any future field added to
either `ConceptResolution` copy, or to `RelationshipResolution`/`TermMatch`/
`CatalogInventoryEntry` (the other three `**r.model_dump()` conversions
in the same orchestrator method, none of which currently have a
non-empty-input regression test either) -- a real, more durable fix would
be a single shared test helper (or a lint/CI check comparing sibling
contract field sets) that fails whenever any of these pairs' fields
diverge, rather than relying on each individual test author remembering
to use non-empty mock data. Not built here; logged as a real follow-up
given the severity of what a silent drift on any of these four pairs can
do (they are all on the hot path of every real question).

### 94. RESOLVED, SAME DAY: item 91's fix only relaxed the SUBJECT side of relationship matching; the OBJECT side had the identical gap -- plus a glossary miscalibration that made "revenue" unjoinable to Product

**What was found**: after items 91-93 deployed and the e-commerce
`ColumnGlossary`/Channel crawl were run live, `"What is the total revenue
by channel?"` answered correctly (real join, real varied per-channel
numbers). But `"What are the top 5 categories by revenue?"` still failed
with `unjoined_table_in_multi_table_query` on `['DIM_PRODUCT',
'FACT_ORDER_ITEMS']`(then, before the second fix below, on
`['DIM_PRODUCT', 'FACT_ORDERS']`). Two distinct, compounding real gaps:

1. My original e-commerce `ColumnGlossary` mapped "revenue"/"sales" to
   `FACT_ORDERS.TOTAL_AMOUNT` -- but `FACT_ORDERS` has NO join path to
   `DIM_PRODUCT` at all (no `PRODUCT_ID` column; only `FACT_ORDER_ITEMS`
   has one, via "OrderItem involves Product"). Any revenue question
   combined with a product-level dimension (category/subcategory/brand)
   was structurally unanswerable through that column. Fixed by
   re-pointing "revenue"/"total revenue"/"sales"/"total sales" to
   `FACT_ORDER_ITEMS.LINE_TOTAL` instead (net merchandise revenue,
   excluding order-level tax/shipping -- a real, common e-commerce
   definition), since `LINE_TOTAL` is joinable to EVERY dimension
   (Customer, Date, Channel, Product, Promotion) via the existing
   "OrderItem ..." relationship concepts. `FACT_ORDERS.TOTAL_AMOUNT` keeps
   only genuinely order-level synonyms ("order total"/"order value"),
   which correctly still mean the order-level amount for AOV-style
   questions.
2. Even after (1), the join still failed: item 91's fix only relaxed the
   SUBJECT-label check (`OntologyAgent._resolve_relationships`) when the
   concept's `realizing_table` is already implied by a resolved concept --
   the OBJECT-label check was untouched. "top 5 categories by revenue"
   implies `FACT_ORDER_ITEMS` via "revenue" (subject side now passes) but
   never says "product" (only "categories"), so "OrderItem involves
   Product" (`object_label="Product"`) still never fired. This is the
   exact same class of bug as item 91, just on the other side of the
   relationship.

**Resolution**: generalized the relaxation to apply symmetrically --
once a concept's `realizing_table` is implied by a resolved business
concept, BOTH the subject and object literal/instance checks are skipped
entirely and the relationship fires unconditionally. This is safe because
`SchemaMappingAgent._build_joins` (not `_resolve_relationships`) is the
actual correctness gate: it independently re-verifies, against the real
live catalog, that the relationship's join key exists on both the
realizing table and exactly one other resolved table (its own item-87
ambiguity guard) before ever emitting a `JoinSpec` -- a
relationship_resolution that turns out irrelevant is just a skipped
no-op there, never a wrong join. New test:
`test_relationship_fires_when_the_object_side_table_is_implied_not_the_subject`;
updated the existing implied-table test to assert against the (now
larger, correctly so) set of fired relationships rather than exactly one.
286 tests pass (up from 285), `ruff check` clean. Live-verified: "top 5
categories by revenue" now needs re-testing after this deploy (pending as
of this entry -- see BUILD_LOG.md).

**Live-verified after deploy + re-sync** (both KG syncs re-run against
this fix): "top 5 categories by revenue" and "total revenue by loyalty
tier" both now answer correctly with real, varied per-group numbers via
`FACT_ORDER_ITEMS.LINE_TOTAL`, joined through "OrderItem involves
Product"/via `DIM_CUSTOMER`. Comparison ("Website vs. Mobile App
revenue") and trend ("revenue by month") questions also verified
live-correct. On the brokerage side, the 2 new price/market-activity
relationship concepts (item 92) are also live-confirmed: "average closing
price by asset sector" (real join, `CLOSE_PRICES` + `ASSET_INFORMATION`)
and "which markets have the most customer trading activity" (real join,
`CUSTOMER_MARKET_AGG` + real `TXN_COUNT` per market) both answer
correctly, and the original golden-set question ("total transaction
volume by market") still answers correctly -- no regression.

**What full version requires**: (1) a separate, smaller gap found in the
SAME live-testing pass: "How much revenue came from the Mobile App?"
answered with a full, UNFILTERED per-channel breakdown rather than a
single Mobile-App-only total -- SQL Generation's LLM-based predicate
resolution did not recognize "Mobile App" as a filter value needing a
`WHERE CHANNEL_NAME = 'Mobile App'` clause, reproduced consistently (not
just once, ruling out ordinary LLM non-determinism as the sole
explanation). This is a real gap in a DIFFERENT agent
(`query.sql_generation`'s predicate-resolution step) than anything
touched by items 90-94 -- **RESOLVED, see item 95**. (2) the
"revenue = net merchandise, excludes tax/shipping" business definition
chosen in fix (1) above is a real, debatable choice -- a user who expects
"total revenue" to mean the full billed amount (including tax/shipping)
would get a smaller number than expected for pure order-level questions;
this tradeoff was accepted specifically because it's what makes
universal joinability possible, not because it's the only valid
definition.

### 95. RESOLVED: SQL Generation's predicate-resolution LLM call never fired for named-value filters (only relative-date/comparison phrases), so "revenue from the Mobile App" silently returned every channel instead of one

**What was found**: root-caused item 94's "Mobile App" gap via direct,
isolated diagnostic calls to each real Understanding-domain agent in
order (Intent Understanding, Ontology, Semantic Retrieval), not
assumption. Intent Understanding correctly extracted `entities=["revenue",
"Mobile App"]`. Ontology correctly left "Mobile App" unresolved (it is a
value, not a business concept). **Semantic Retrieval correctly resolved
"Mobile App" to `DIM_CHANNEL.CHANNEL_NAME`** -- a real, correct column
match, proving that agent was never the problem. The actual bug: once
Schema Mapping turns that match into a `ResolvedColumnRef(term="Mobile
App", column_name="CHANNEL_NAME", role="dimension")`, SQL Generation
treats it EXACTLY like a plain "channel" reference destined for `GROUP
BY` -- `_needs_predicate_resolution` (`sql_generation/agent.py`) only
ever fires its predicate-resolution LLM call when the question contains
one of a fixed set of relative-date/comparison trigger words ("last",
"quarter", "since", "vs", ...). "Mobile App" contains none of them, so
the LLM was never even asked whether a filter was needed -- the gap was
never in the LLM's judgment, only in whether it was ever consulted at all.

**Resolution**: `ResolvedColumnRef.term` -- an existing field that
already carries the original free-text phrase that resolved each column
-- is compared against the column's own `column_name` via the same
normalize-and-substring heuristic `understanding.ontology.agent
._normalize_label`/`_label_matches_entities` already use for the
identical class of judgment call (free-text phrasing vs. a canonical
identifier). A new `_resolved_via_named_value` check: "channel" vs
`CHANNEL_NAME` normalizes to "channel"/"channelname" -- a real substring
match, so a genuine dimension reference correctly does NOT trigger
predicate resolution. "Mobile App" vs `CHANNEL_NAME` normalizes to
"mobileapp"/"channelname" -- no overlap, so this correctly flags it.
`_needs_predicate_resolution` now fires on EITHER the existing temporal
trigger OR any resolved dimension column matching this new check.
Also broadened `predicate_resolution.md`'s system prompt (previously
framed almost entirely around relative dates, with only one date-shaped
worked example) to explicitly cover named-value filters, with a second,
non-date worked example.

Deliberately favors false positives over false negatives, same
philosophy as `_label_matches_entities`'s own documented tradeoff: an
irregular plural like "categories" vs `CATEGORY` also won't
substring-match, triggering an unnecessary but harmless extra LLM call
(which itself correctly returns no predicates) -- confirmed via 2
existing tests (`_UNJOINED_MARKET_COLUMNS`'s real "market" -> `NAME`
resolution, a genuine false positive since `ResolvedColumnRef` has no
visibility into the real glossary synonym linking them) needing their
canned LLM response updated from a placeholder string to a valid empty
`{"predicates": []}`. New tests:
`test_named_value_dimension_triggers_predicate_resolution_with_no_temporal_words`,
`test_generic_dimension_reference_does_not_trigger_predicate_resolution`.
288 tests pass (up from 286), `ruff check` clean.

**What full version requires**: (1) the false-positive rate for generic
dimension words whose real column name doesn't textually resemble them
(item 95's own "market"/`NAME` example) could be reduced by giving
`_resolved_via_named_value` visibility into the column's real
`business_name`/`synonyms` (already crawled and available in the
catalog) -- deliberately not done here, since `ResolvedColumnRef` was
kept minimal by design (see its module docstring's "known contract gap"
section) and adding fields risks the exact kind of sibling-contract
drift that caused item 93's production incident; the cost of the
false positive (one extra, harmless LLM call) was judged lower than that
risk. (2) live end-to-end re-verification (a real "revenue from the
Mobile App" question showing a real, single-row, correctly-filtered
answer) is pending this fix's deploy -- see BUILD_LOG.md's matching entry.

**Live-verified after deploy**: "How much revenue came from the Mobile
App?" now produces a real `WHERE DIM_CHANNEL.CHANNEL_NAME = %(predicate_0)s`
clause, bound to `'Mobile App'`, returning exactly one correct row
(`$511,654.77`) with a correctly grounded narrative.

Continued testing immediately surfaced a SECOND, related bug with the
same live-test-then-fix rigor: "How much revenue came from customers in
the Gold loyalty tier?" and "What is the total revenue from the
Electronics category?" BOTH still returned the full, unfiltered
breakdown. A direct diagnostic call to Intent Understanding showed why:
it extracts the COMPOUND phrase (`entities=["revenue", "Gold loyalty
tier"]`, not the bare value `"Gold"`) -- and `_resolved_via_named_value`'s
original BIDIRECTIONAL substring check (safe if either term contains the
column name or vice versa) was wrongly satisfied, since `"loyaltytier"`
(the column) IS a real suffix of `"goldloyaltytier"` (the term). **Fixed
by making the check one-directional**: safe only when the (normalized)
term is fully CONTAINED WITHIN the column name -- i.e. the term adds
nothing beyond what the column's own name already says. A term with
extra content beyond the column name (the "gold"/"electronics" prefix)
can only be a value modifier, so it now always triggers. This actually
simplifies the check (one substring test instead of two) while fixing
both compound-phrase cases. One additional existing test
(`test_multi_table_join_produces_correct_join_clause`, whose
"customer risk level" -> `RISKLEVEL` fixture is the same class of
accepted false positive) needed its canned response updated the same
way as item 95's earlier two. New test:
`test_compound_named_value_phrase_triggers_predicate_resolution`. 289
tests pass (up from 288), `ruff check` clean. Live re-verification of
"Gold loyalty tier" and "Electronics category" is pending this second
fix's deploy -- see BUILD_LOG.md's matching entry.
