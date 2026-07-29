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

### 28. Chart Selection's column-role linkage across SQL Generation's aliasing is manually threaded, not structurally carried by any contract

**What's deferred**: No contract between SQL Generation and Data
Federation (`OptimizedSql`, `ExecutionPlan`, `SourceQueryResult`,
`DataFederationResult`) preserves a resolved column's measure/dimension
role or its real result-set header. `DataFederationResult.final_columns`
is a bare `list[str]`.

**Why**: SQL Generation's own aggregation aliasing
(`sql_generation.agent._generate_statements`/`_aggregation_function`: a
`role="measure"` column becomes `{column_name}_TOTAL` in the real SELECT
list, e.g. `UNITS` → `UNITS_TOTAL`) means a measure's catalog
`column_name` and its real result-set header diverge — so Chart Selection
needs both the role AND the real alias to pick sensible x/y columns.
`ChartColumnRef.result_alias` exists specifically to carry this, but today
the CALLER (a human-written test, absent a real Orchestrator) populates
it by hand, replicating SQL Generation's alias rule — demonstrated
concretely in `tests/integration/insight_pipeline/test_pipeline_chain.py`
rather than glossed over.

**What full version requires**: A real Coordinator (Phase 9,
Orchestrator domain) threading this structurally — either a new field on
`GeneratedSql`/`OptimizedSql` carrying the alias mapping forward, or the
Coordinator itself building `ChartColumnRef` from data it already holds
across agent calls. No prior phase has gone back to modify an
already-shipped upstream agent's contract for a downstream phase's
convenience; this is the first real case where that tradeoff was
consciously made (see DECISIONS.md).

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
