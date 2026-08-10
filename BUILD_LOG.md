# Build Log

A running, dated log of what was built in this repository, and by which workstream.
This is a factual log, not a design document — see `DECISIONS.md` for the reasoning
behind major calls and `LIMITATIONS.md` for what remains deliberately unbuilt.

---

## 2026-07-28 — Phase 1 scaffold

This was an autonomous scaffolding pass, run per the project's approved working
method (an agent executes a pre-approved phase plan without pausing for
per-file confirmation, then reports back what changed). Scope was infra
scaffolding only, as agreed in the Phase 1 plan.

At a high level, this pass scaffolded:

- **Root project documents**: `README.md`, `LIMITATIONS.md`, `DECISIONS.md`,
  `CONTRIBUTING.md`, `SECURITY.md`, `CODEOWNERS`, `LICENSE`, plus repo hygiene
  files (`.gitignore`, `.gitattributes`, `.editorconfig`).
- **CI workflows** under `.github/workflows/`: general lint/test
  (`ci.yml`), dependency and static-analysis security scanning
  (`security-scan.yml`), Terraform validation (`terraform-plan.yml`), and a
  required adversarial security-test gate (`adversarial-tests.yml`).
- **Architecture and process docs** under `docs/`: system overview and agent map,
  the formal agent contract, a data-flow walkthrough, a local-dev smoke-test
  runbook, and an ADR for the agent-runtime language choice.
- **The local-first infra stack** under `infra/`: a full `docker-compose.yml`
  wiring postgres, neo4j, redis, an OpenTelemetry collector, Prometheus, Grafana,
  OPA, a Trino coordinator+worker, and build-context references to the
  gateway/agent-runtime/web services, plus supporting config for every one of
  those services (Postgres init SQL, Neo4j local config, OTel collector config,
  Prometheus scrape config, Grafana provisioning and starter dashboards, an OPA
  allow-all placeholder policy, Trino coordinator/worker configs, and a kind
  cluster config for future k8s experimentation).
- **A validated-but-never-applied Terraform skeleton** under `terraform/`: a
  `dev` environment wiring seven modules (resource-group, aks, acr, key-vault,
  postgres-flexible-server, networking, entra-app-registration), each with its
  own `main.tf`/`variables.tf`/`outputs.tf`.

**Explicitly not built in this pass** (owned by parallel workstreams, out of
scope for this repo's Phase 1 infra scaffold): `packages/gateway`,
`packages/agent_runtime`, `packages/shared`, and `web/` were **not** created here
— `infra/docker-compose.yml` references their eventual Dockerfiles by path on the
assumption they will exist by the time anyone runs `docker compose up`. Similarly,
`tests/security/` and `tools/scripts/smoke-test.sh` are referenced by the CI
workflows and docs above but are not created by this pass.

**Tooling note**: this machine had no Docker, Node.js, Terraform, or WSL installed
at the start of this session. Local tooling was installed via `winget` mid-session
to support later verification steps. The exact packages/versions installed via
that process are not visible to this log entry — see `LIMITATIONS.md` item 8.

## 2026-07-29 — Phase 1 end-to-end verification, and real bugs found and fixed

Installed Node.js v24.18.0, Terraform v1.15.8, and Docker Desktop 29.6.2
(Compose v5.3.1, WSL2 backend with Ubuntu) via `winget`, then ran `git init`
and the initial commit, and verified every Phase 1 deliverable for real
rather than assuming the scaffold was correct. This surfaced and fixed
several genuine bugs — logged here rather than silently corrected, per the
project's working method:

- **Python CI (`pytest packages/`)**: the shared rootdir config
  (`packages/pyproject.toml`) didn't set `asyncio_mode`, so pytest-asyncio
  silently defaulted to STRICT mode when the three packages' tests ran
  together, breaking every async test. Also added
  `--import-mode=importlib` to resolve a module-name collision between
  `gateway/tests/test_healthz.py` and `agent_runtime/tests/test_healthz.py`
  (identical basenames, no shared parent package). Fixed by adding
  `asyncio_mode = "auto"` and `addopts = "--import-mode=importlib"` to
  `packages/pyproject.toml`. Final state: `ruff check` clean, `mypy` clean
  (after two real type-narrowing fixes in `llm/client.py` and
  `intent_understanding/agent.py`), `pytest packages/` → 24 passed, 1
  correctly skipped (`llm_integration`, no API key present).
- **`npm audit`**: 16 high-severity vulnerabilities, all transitive
  (`brace-expansion` via the pinned ESLint 8.57 chain; `postcss`/`sharp`
  bundled inside Next.js 15.5.22 itself). Fixed via `package.json`
  `overrides` pinning patched versions (`postcss@8.5.24`,
  `sharp@0.35.3`, `brace-expansion@5.0.8`) without bumping Next or ESLint's
  major versions. Final state: `npm audit` → 0 vulnerabilities; lint,
  typecheck, and `next build` all clean.
- **`web/Dockerfile` build failure**: `COPY --from=builder /app/public
  ./public` failed because `web/public/` didn't exist in the scaffold.
  Fixed by adding `web/public/robots.txt` (a real, correct choice for an
  internal authenticated app — disallow all crawling).
- **`infra/trino/{coordinator,worker}/node.properties`**: `node.environment`
  was set to `navigraph-dev`, which fails Trino's
  `[a-z0-9][_a-z0-9]*` validation (hyphens aren't allowed, only
  underscores) — the coordinator crash-looped on every start. Fixed to
  `navigraph_dev`.
- **Nested read-only bind mount**: the coordinator/worker mounted
  `/etc/trino:ro` and then `/etc/trino/catalog:ro` as a second, separate
  mount nested inside the first — Docker can't create a mountpoint inside
  an already-read-only parent mount ("read-only file system" at container
  start). Restructured so `catalog/` is a real (currently empty)
  subdirectory of each node's own `infra/trino/{coordinator,worker}/`
  config tree instead of a second bind mount; moved the documentation
  example file to `infra/trino/coordinator/catalog/snowflake.properties.example`
  accordingly.
- **Broken Docker healthchecks, four separate causes**: `opa` and
  `otel-collector` are minimal/scratch images with no `/bin/sh` at all
  (exec healthchecks are structurally impossible there — removed them,
  documented why, and rely on `tools/scripts/smoke-test.sh` checking them
  from the host instead); `trino-coordinator`/`trino-worker` and the
  Python-based `gateway`/`agent-runtime` images have a shell but no
  `wget`/`curl` in the Trino case (switched to `curl`, confirmed present)
  and no `wget`/`curl` at all in the Python case (switched to a
  dependency-free `python3 -c "import urllib.request; ..."` check); `web`
  (Alpine, has `wget`) failed because this container's `/etc/hosts`
  resolves `localhost` to `::1` first but the Next.js standalone server
  only binds IPv4 `0.0.0.0`, so the healthcheck hit "connection refused"
  over IPv6 even though the app was serving fine over IPv4 — fixed by
  pointing the healthcheck at `127.0.0.1` explicitly. Also removed the
  now-unnecessary `otel-collector: condition: service_healthy` dependency
  from `agent-runtime` (changed to `service_started`, since this app's own
  OTel exporter is already designed to degrade gracefully if the collector
  is unreachable) and published port `13133` so the collector's
  `health_check` extension is reachable from the host.
- **Web page showed "gateway unreachable" even with everything healthy**:
  `page.tsx`'s server component correctly reads a server-side `GATEWAY_URL`
  env var, but `docker-compose.yml` only ever set the browser-facing
  `NEXT_PUBLIC_GATEWAY_URL` for the `web` service — the server-side code
  fell back to its `localhost:8000` default, which inside the `web`
  container refers to itself, not the `gateway` container. Fixed by adding
  `GATEWAY_URL: http://gateway:8000` (Docker-internal service DNS name) to
  `web`'s environment, alongside the existing browser-facing
  `NEXT_PUBLIC_GATEWAY_URL: http://localhost:8000`.

**Final verified state**: all 12 `docker compose` services report
healthy/running; `tools/scripts/smoke-test.sh` passes 7/7 (after fixing its
Grafana check to hit `/api/health` instead of `/`, since Grafana's root
path is a 302 redirect by design, not a failure); a real `POST /ask`
through the gateway reaches the real Intent Understanding agent and
returns a populated `lineage_events` array with correct `tenant_id`/
`trace_id`; the web UI's server-rendered page confirms "gateway reachable
at http://gateway:8000"; `terraform validate` passes for the `dev`
environment (`terraform plan` correctly stops asking for
`subscription_id`/`tenant_id` rather than touching a real Azure account,
since none were supplied).

## 2026-07-29 — Phase 2: Metadata catalog + connector SDK, verified against a real Snowflake account

Built `packages/connector_sdk` (`navigraph_connectors`) and
`packages/metadata_catalog` (`navigraph_catalog`), plus
`tests/integration/metadata_catalog/`, matching every existing package
convention. Real, working Snowflake credentials were provided this phase
(account `TKISXMB-JYB85836`, user `SHUBHSNFLK`) and used for genuine
end-to-end verification, not just mocked tests:

- Discovered the account's real structure via a live connection: databases
  (`FIDELITY_POC`, `TEST_DB`, plus Snowflake's built-in ones), warehouses,
  and roles. Confirmed `FIDELITY_POC` / `FIDELITY_WH` / `FIDELITY_ANALYST_ROLE`
  is the intended target (matched naming, confirmed with you), and switched
  off the account's overly-privileged `ACCOUNTADMIN` default to the
  least-privilege `FIDELITY_ANALYST_ROLE` for routine schema crawling.
- `pytest -m snowflake_integration` passes for real against this account.
- Ran the real crawler end-to-end: registered a `DataSource`, crawled
  `FIDELITY_POC`, and stored real rows in the catalog tables. First run
  returned 78 tables — caught a real bug: `introspect_schema()` didn't
  exclude Snowflake's own `INFORMATION_SCHEMA` metadata schema, pulling in
  ~60 system views alongside real business tables. Fixed in
  `navigraph_connectors/snowflake/connector.py`; re-ran and got the
  correct 17 real tables (`FAR_TRANS` schema: asset/customer/transaction/
  market data; `STAGING` schema: staging versions of the same) — a real
  financial trading/portfolio dataset. Worth noting for Phase 3: one table,
  `STAGING.SCHEMA_ENRICHMENT` (`COLUMN_NAME`, `BUSINESS_NAME`, `SYNONYMS`),
  already looks like a business-glossary seed that the ontology work should
  look at.
- Found and fixed a second real, unrelated environment bug while running
  the live migration: this dev machine has a separate native Postgres
  process already bound to host port 5432, which intercepts host-side
  connections meant for the docker-compose Postgres container and rejects
  them with a misleading password-auth error. Remapped the container to
  host port 5433 (`infra/docker-compose.yml`) rather than touch the
  unrelated system process — logged in `LIMITATIONS.md` item 11 and the
  local-dev runbook.
- `alembic upgrade head` and `alembic downgrade base` both run for real
  against the live docker-compose Postgres (via the new port 5433) and
  produce the expected schema exactly.

**Final verified state**: `ruff check`, `mypy`, and `pytest packages/`
(55 passed, 2 skipped as designed) all clean across all five Python
packages together; `pip-audit` clean; real Alembic up/down migration
against live Postgres; real Snowflake connector integration test passing;
real crawl of `FIDELITY_POC` producing 17 correctly-shaped catalog rows.

## 2026-07-29 — Phase 3: Knowledge graph / ontology, verified against real Neo4j + real Snowflake

You approved a set of 50 real business questions grounded in the
`FIDELITY_POC` schema (asked me to generate them from what the Phase 2
crawl revealed, rather than supplying your own). Used those questions plus
two live, read-only Snowflake queries (exchange/market grouping,
sector/industry cleanliness) to design the ontology for real rather than
generically, then built:

- Extended `packages/metadata_catalog` (a closed Phase 2 package, extended
  with your explicit sign-off) with a `ColumnGlossary` model, migration
  `0002`, `upsert_glossary`/`list_glossary` API, and a
  `schema_enrichment_crawler` that ingests the real `SCHEMA_ENRICHMENT`
  glossary.
- Built new `packages/knowledge_graph` (`navigraph_kg`): a two-tier Neo4j
  ontology (reference/dimension nodes + business-concept mapping layer),
  an idempotent four-stage ingestion pipeline with soft staleness
  (`active`/`last_synced_at`, never hard-delete), and a tenant-scoped
  read API.

**Two real bugs found and fixed running against live services (not just
mocks)**:
1. `schema_enrichment_crawler.py` assumed lowercase dict keys
   (`row["table_name"]`) matching the SQL as literally written, but
   Snowflake's cursor always reports column names in their actual stored
   case — uppercase by default for unquoted identifiers — regardless of
   query casing. Fixed by normalizing row keys to lowercase before use;
   also fixed the unit test's fake connector, which had been using
   lowercase keys and thus couldn't have caught this against a mock alone.
2. `AssetRecord.asset_name` was a required `str`, but the real
   `FIDELITY_POC.FAR_TRANS.ASSET_INFORMATION` table has at least one row
   with a NULL `asset_name` — the real ingestion run crashed with a
   Pydantic validation error until the field was made `str | None`.

**Real verification performed**: `alembic upgrade head` (now `0002`)
against the live docker-compose Postgres — real `column_glossary` table
confirmed; real glossary crawl — 41 real business-term rows landed in
Postgres; real knowledge-graph ingestion against live Neo4j + live
Snowflake reference data — 835 assets/38 markets/29 exchanges/15
sectors/119 industries/41 business concepts synced; idempotency proven
three ways (identical ingestion-summary counts across two runs, and an
exact total node/relationship count match — 1203 nodes, 1861
relationships — before and after a third run); four real questions
answered via the graph and shown correct: "order value" resolves to
`TRANSACTIONS.TOTALVALUE`; exchange `ATHEX` correctly groups its three
real markets (`EBB`, `XATH`, `ENAX`); 26 real assets returned for the
Technology sector (SAP SE, Western Digital, several Greek tech
companies); the `RelationshipConcept` metadata for "Customer has
RiskLevel" resolves to `CUSTOMER_INFORMATION.CUSTOMERID`/`RISKLEVEL`.
`ruff`, `mypy` (47 source files), and `pytest packages/` (99 passed, 2
skipped) all clean across all six Python packages together; `pip-audit`
clean.

## 2026-07-30 — Phase 4: 5 remaining Understanding-domain agents, verified end-to-end against live Postgres + Neo4j

Built the 5 remaining Understanding-domain agents (Conversation, Metadata
Discovery, Ontology, Semantic Retrieval, Schema Mapping) via three
parallel workstreams, following the exact contract pattern established by
Intent Understanding. Only 3 of the now-6 Understanding agents call an
LLM at all (Conversation, Intent Understanding, Semantic Retrieval); the
other 3 are pure deterministic lookups/assembly over `navigraph_catalog`/
`navigraph_kg` — no hallucination risk, no API cost, wherever a
deterministic answer is actually available.

**Integration work done directly (not by the parallel workstreams)**:
wired all 5 new agents into `agent_runtime`'s `main.py`/`registry.py`
(constructing a shared LLM client, a Postgres session factory, and a
`Neo4jClient` at startup; adding one `POST /agents/understanding/<name>/invoke`
route per agent via a new shared `_invoke_agent` helper, refactored out of
what was previously Intent Understanding's one-off inline route body);
added the missing `navigraph-metadata-catalog`/`navigraph-knowledge-graph`
dependencies to `agent_runtime/pyproject.toml` (flagged by one of the
parallel workstreams — it correctly didn't touch a file outside its scope);
built a new `tests/integration/understanding_pipeline/test_pipeline_chain.py`
chaining all 6 agents together for real.

**Three real bugs found and fixed running the real integration test (not
just unit tests)**:
1. My own test first assumed `MARKETID` had an underscore (`MARKET_ID`) —
   the real Snowflake column has none, matching `CUSTOMERID`'s naming.
2. **A genuinely important, real discovery, not just a test bug**: the
   real `STAGING.SCHEMA_ENRICHMENT` glossary only references
   `staging_`-prefixed table names, so every business-concept mapping in
   the graph resolves to the `STAGING` schema's copies (e.g.
   `STAGING.STAGING_TRANSACTIONS.UNITS`), never the equivalent `FAR_TRANS`
   column — even though both exist. The test's expectations were wrong
   (assumed `FAR_TRANS`); fixed to match real behavior, and logged as
   `LIMITATIONS.md` item 14 since whichever phase builds SQL Generation
   needs to make a real decision about this, not inherit it by accident.
3. `schema_mapping`'s independently-declared `TermMatch` contract (a
   deliberate duplication of `semantic_retrieval`'s, per the
   no-cross-package-imports design) was missing the `rationale` field the
   real `semantic_retrieval.TermMatch` has — caught only because the
   integration test actually wired the two agents' real outputs together,
   exactly the kind of drift the "verify with a real integration test, not
   just isolated unit tests" discipline exists to catch. Fixed by adding
   the missing field; the other three duplicated contract pairs
   (`ConceptResolution`, `RelationshipResolution`,
   `CatalogColumnEntry`/`CatalogInventoryEntry`) were checked field-by-field
   and found to already match exactly.

**Real verification performed**: `ruff`, `mypy` (74 source files), and
`pytest packages/` (134 passed, 4 skipped as designed) all clean across
all six packages from a fresh install; the real cross-agent integration
test passes end to end for the worked-example question ("What is the
total transaction volume by market?") against live Postgres + Neo4j —
Conversation short-circuits (no LLM call, first turn), Intent
Understanding classifies (canned), Metadata Discovery reads the real
catalog, Ontology resolves "units traded" for free via the real graph,
Semantic Retrieval resolves "market" against the real candidate list (its
hallucination-rejection path separately proven in unit tests), and Schema
Mapping produces the correct final structure: `STAGING_TRANSACTIONS.UNITS`
as a measure, `STAGING_TRANSACTIONS.MARKETID` as a dimension, zero
unmapped terms. No `ANTHROPIC_API_KEY` was available in this environment,
so the `llm_integration`-marked tests for Conversation and Semantic
Retrieval were verified to skip cleanly, not run for real against the
live Anthropic API — matches the existing graceful-degradation pattern and
isn't required to consider this phase done.

## 2026-07-29 — Phase 5: Query domain (6 agents) + Trino/Snowflake federation, real SQL executed against a live Snowflake account

Built the 6 Query-domain agents (Data Source Discovery, SQL Generation,
SQL Optimization, Execution Planning, Data Federation, Caching) plus a new
standalone `packages/federation` package, via three parallel workstreams
following the exact contract pattern established in Understanding. Two
real safety decisions were confirmed with the user before building: to
execute real SQL against the live Snowflake account this phase (with
structural compensating controls, ahead of the Guardrail domain), and to
run a live, read-only `SHOW GRANTS TO ROLE FIDELITY_ANALYST_ROLE` check
first — confirmed the role has zero write privileges (only
`USAGE`/`READ`/`SELECT`).

**Integration work done directly**: wired all 6 new agents into
`agent_runtime`'s `main.py` (a real `redis.Redis` client, a
`navigraph_federation.TrinoClient`, reusing the existing Postgres session
factory and LLM client); added `navigraph-connector-sdk`,
`navigraph-federation`, and `redis` to `agent_runtime/pyproject.toml`;
added a real Snowflake catalog (`infra/trino/{coordinator,worker}/catalog/snowflake.properties`,
gitignored) to Trino; built
`tests/integration/query_pipeline/test_pipeline_chain.py` chaining all 6
Query agents (plus the 6 Understanding agents feeding them) for real
against live Postgres, Neo4j, Redis, and the live Snowflake account.

**Real bugs found and fixed, in the order discovered**:
1. **Trino crash-loop** (`RestartCount=13`) the moment the real Snowflake
   catalog was registered: `ApplicationConfigurationException: Connector
   'snowflake' requires additional JVM argument(s) ...
   --add-opens=java.base/java.nio=ALL-UNNAMED`. Fixed by adding that line
   to both `infra/trino/coordinator/jvm.config` and
   `infra/trino/worker/jvm.config`; confirmed via `RestartCount=0` and a
   real `SHOW CATALOGS`/`SHOW SCHEMAS IN snowflake` returning
   `far_trans`/`staging`.
2. `schema_mapping.contracts.ResolvedColumnRef` was missing a `schema_name`
   field — SQL Generation's build workstream correctly flagged this as a
   real contract gap (dialect-neutral `SCHEMA.TABLE` SQL genuinely needs to
   know which schema a table lives in) rather than guessing a hardcoded
   schema name. Fixed directly by adding the field to the real, already-
   shipped Phase 4 contract and threading it through
   `schema_mapping/agent.py`'s `_resolve_columns`.
3. A `mypy` structural-typing mismatch between `CachingAgent`'s
   `CacheClientProtocol` and the real `redis.Redis` instance
   (`Redis.get`'s actual stub signature is broader than the protocol's,
   differing in both parameter name and return-type breadth). Fixed with
   an explicit, documented `typing.cast` at the one call site in
   `main.py`, rather than loosening the protocol itself (which exists
   specifically so `agent_runtime`'s own dependency, not the Caching
   agent package, decides to depend on `redis` — see that package's
   module docstring).
4. **The most significant real bug, caught only by testing the live
   HTTP endpoint directly, not by any test suite**: after rebuilding and
   restarting the `agent-runtime` container, a real `POST
   /agents/query/data_source_discovery/invoke` call returned
   `"No connector registered for source_type='snowflake'. Registered
   types: []"` — `main.py` had never imported
   `navigraph_connectors.snowflake` (the module whose import side effect
   registers `"snowflake"` in the connector registry), so the real running
   service's registry was empty despite every unit test passing (unit
   tests inject a fake connector directly; the pytest-based integration
   test imports the module itself). Fixed by adding that import to
   `main.py` with a comment explaining exactly why it's needed; re-verified
   live via the same HTTP call, which now returns `"reachable": true` with
   a real Snowflake connection.
5. The `agent_runtime` Dockerfile never copied/installed the new
   `packages/federation` package, so the image build failed with
   `No matching distribution found for navigraph-federation` — a real gap
   left by the parallel build workstream (out of its scope to edit).
   Fixed by adding the missing `COPY federation` / `RUN pip install
   .../federation` stage in the correct dependency position.

**Real verification performed**: `ruff check packages/` and `mypy`
(explicit per-package paths, 110 source files) both clean; `pytest
packages/` — 215 passed, 5 skipped as designed (the `llm_integration`/
`snowflake_integration`-marked tests, no `ANTHROPIC_API_KEY` in this
shell's env by default). The real `tests/integration/query_pipeline/`
test passed end-to-end on the first full run after the contract-gap fix:
real SQL (`SELECT MARKETID, SUM(UNITS) AS UNITS_TOTAL FROM
STAGING.STAGING_TRANSACTIONS GROUP BY MARKETID`, LIMIT-injected and audit-
commented by SQL Optimization) executed for real via the direct-connector
route, returning 16 real rows from Snowflake; a deliberately malicious
`SELECT 1; DROP TABLE STAGING.STAGING_TRANSACTIONS` statement, run through
the same Execution Planning call, was rejected (`"multiple SQL statements
detected (stacked/chained query)"`) and never reached Data Federation; a
real Redis lookup→store→lookup cycle showed a genuine miss then a genuine
hit with matching `final_row_count`. Separately confirmed via `docker exec
navigraph-trino-coordinator trino --execute "SHOW SCHEMAS IN snowflake"`
that Trino's real federation route is live (`far_trans`, `staging`,
`information_schema`, `public`), even though it isn't the default
execution route this phase. The `agent-runtime` container was rebuilt and
restarted twice (once for the new agents, once for the connector-
registration fix) — `RestartCount=0`, `healthy` both times — and a direct
`POST /agents/query/data_source_discovery/invoke` HTTP call against the
live container confirmed `"reachable": true` against the real Snowflake
account after the fix.

## 2026-07-29 — Phase 6: Guardrail domain (4 agents) + real OPA policy, closing the Phase 5 compensating-controls gap

Built the 4 Guardrail-domain agents named in `docs/architecture/overview.md`
(Schema Constraint Validator, Policy Authorization, Query Cost/Row-Limit
Estimator, PII Exposure Checker) via two parallel workstreams, plus a new
`packages/shared/navigraph_shared/opa/` client (mirroring `llm/client.py`'s
ABC/real/fake triad) and a real `infra/opa/policies/authz.rego` policy
replacing the Phase 1 allow-all placeholder. One real judgment call was
confirmed with the user via `AskUserQuestion` before building: the real
policy engine and Guardrail agents evaluate `RequestContext.roles`/`claims`
as-is, exactly like every other agent already trusts that field — real
Azure AD JWT verification populating those fields from a cryptographically
verified identity stays a separate, explicitly deferred gap (no Azure
Portal click-through needed this phase).

**A real environment failure needed fixing before any of this could be
verified**: Docker Desktop repeatedly crash-looped on startup with
`"listening on unix://.../dockerInference: ... The filename, directory
name, or volume label syntax is incorrect"` (and, on a later attempt, the
identical failure shape for `docker-secrets-engine/engine.sock`) — stale,
un-deletable reparse-point socket files left over from a prior session,
which even `Remove-Item`/`fsutil reparsepoint delete` could not clear
directly. Fixed by renaming the parent directories aside (Windows allowed
renaming the directory even though the individual locked file inside
couldn't be removed) and relaunching; Docker Desktop recreated fresh,
working socket files on the next start.

**Real bugs found and fixed, in the order discovered**:
1. **The most significant bug this phase**: both Schema Constraint
   Validator and PII Exposure Checker (built independently by two parallel
   workstreams) assumed `GeneratedSql.referenced_columns` was a flat list
   of bare column names, requiring a cross-product search against
   `referenced_tables`. The real value SQL Generation actually produces is
   `"TABLE.COLUMN"`-qualified (`sql_generation.agent._qualified_col`) —
   caught live via `tests/integration/guardrail_pipeline/`, where every
   real statement was rejected as `unknown_column` (Schema Constraint
   Validator) and, more seriously, PII Exposure Checker's fail-open-on-
   unresolvable design meant a qualified name that never matched a bare
   lookup silently `cleared` every real PII statement regardless of
   actual sensitivity. Fixed by parsing the qualified form directly in
   both agents (`_split_qualified_column`), with a documented fallback for
   unqualified names; added dedicated unit tests pinning the real shape
   down so this can't regress silently again.
2. `CatalogColumnEntry` (metadata_discovery's contracts) got a new
   `is_pii` field as planned, but its sibling-package mirror,
   `schema_mapping.contracts.CatalogInventoryEntry`, wasn't updated to
   match — a real contract-drift bug, caught immediately by
   `tests/integration/guardrail_pipeline/` (`extra_forbidden` validation
   error) the moment the two agents' real outputs were wired together.
   Fixed by adding the matching field to the sibling contract.
3. `metadata_discovery`'s own existing unit test used a `SimpleNamespace`
   stand-in for `CatalogColumn` that didn't have an `is_pii` attribute —
   broke the moment `agent.py` started reading that field. Fixed the test
   fixture, not the feature.
4. **A real PII-tagging/runtime-resolution mismatch**: the initial PII
   backfill tagged `CUSTOMER_INFORMATION.CUSTOMERID` only on the
   `fidelity_poc_snowflake_v2` data source, but
   `DataSourceDiscoveryAgent`'s table-owner resolution actually resolves
   `STAGING_TRANSACTIONS`/`CUSTOMER_INFORMATION` to the OLDER
   `fidelity_poc_snowflake` registration at runtime (no defined ordering
   between the two, confirmed via a live query) — caught by
   `tests/integration/guardrail_pipeline/` returning a false `cleared` for
   a real PII statement instead of the expected denial. Fixed by tagging
   both registrations; logged as `LIMITATIONS.md` item 26 (the underlying
   two-data-sources-for-one-tenant condition is a real, pre-existing
   inconsistency worth resolving later, not something this phase silently
   worked around).
5. Two real Rego policy bugs, found only by running the adversarial suite
   against the live OPA service, not by reading the policy: `input.claims`
   being `null` correctly denied (`allow=false`) but silently produced an
   EMPTY `deny_reasons` (an internal `object.get` type error dropped that
   rule instance) — fixed with `default claims := {}` null-coalescing; an
   empty-string `tenant_id` matching an equally empty-string claim was
   structurally `==` and therefore incorrectly **allowed** — fixed by
   requiring `input.tenant_id != ""` explicitly.

**Real PII classification, decided with the user, not assumed**: a live
discovery query of the real `FIDELITY_POC` catalog found NO traditional
PII fields at all (no name/email/phone/address columns) — customers are
identified only by an opaque `CUSTOMERID`. Confirmed with the user via
`AskUserQuestion` to tag `CUSTOMERID` itself as PII (a direct customer
identifier, personal data under GDPR-style definitions even without a
name attached) rather than proceed with no real PII data to test against.

**Real verification performed**: `ruff check packages/ tests/` and `mypy`
(explicit per-package paths, 134 source files) both clean; `pytest
packages/` — 257 passed, 5 skipped as designed. The real
`tests/integration/guardrail_pipeline/` test passed end-to-end: Schema
Constraint Validator rejected an unknown-column statement while validating
its real sibling in the same batch; PII Exposure Checker denied `analyst`
and cleared `pii_viewer` for the real, tagged `CUSTOMERID` column; Policy
Authorization authorized a matching-tenant request and denied a
mismatched-tenant one via the real, live OPA service; the resulting
`ExecutionPlan` was real and `read_only_verified`. The real
`tests/security/` suite (16 tests) passed against the live OPA service,
covering all three of `tests/security/README.md`'s required minimums plus
dedicated PII Exposure Checker coverage — including a control test
documenting the known, deliberately out-of-scope self-declared-role-
escalation gap (allowed today, closed only by future real Azure AD
verification). The `agent-runtime` container was rebuilt and restarted
twice (once after wiring the 4 new agents, once after the qualified-
column bugfix) — `RestartCount=0`, `healthy` both times — and real HTTP
calls to `POST /agents/guardrail/policy_authorization/invoke` against the
live container confirmed both a real `allow` decision (matching tenant)
and a real `deny` decision (mismatched tenant) from the live OPA service.

## 2026-07-29 — Phase 7: Insight domain (4 agents), the first fully real end-to-end chain from Understanding through a grounded narrative

Built the 4 Insight-domain agents named in `docs/architecture/overview.md`
(Chart Selection, Anomaly/Outlier Highlighter, Grounded Narrative
Generation, Follow-up Suggestion) via two parallel workstreams, following
the exact contract pattern established in every prior domain. Chart
Selection and Anomaly/Outlier Highlighter are fully deterministic (z-score
via stdlib `statistics`, no new dependency); Grounded Narrative Generation
and Follow-up Suggestion are LLM-backed via the shared `llm_client`.

**A real architectural gap surfaced during design, not silently patched**:
no contract between SQL Generation and Data Federation carries a resolved
column's measure/dimension role or SQL Generation's own real aggregation
aliasing (`UNITS` → `UNITS_TOTAL`) forward — `DataFederationResult.final_columns`
is a bare `list[str]`. Fixed by adding `ChartColumnRef.result_alias`,
populated by the caller (today: the integration test, absent a real
Orchestrator) rather than reaching back into an already-shipped upstream
contract — logged honestly as `LIMITATIONS.md` item 28, demonstrated
concretely in `tests/integration/insight_pipeline/` rather than glossed
over.

**Grounded Narrative Generation's real anti-hallucination mechanism**
(the most significant new piece of discipline this phase adds): the LLM
returns structured JSON with `citations` naming exact `(row_index,
column, cited_value)` triples; every citation is validated against a
closed candidate set built from the real result rows and anomaly data —
a fabricated or misattributed citation is dropped, never partially
trusted, mirroring `SemanticRetrievalAgent`'s "closed candidate list,
reject anything not in it" discipline exactly, applied here to real
result-set cells instead of catalog column IDs. A second, independent
whole-narrative numeric scan catches any number the LLM stated without
even citing it. Follow-up Suggestion is deliberately exempt from this
same discipline (a suggested question is a proposal, not a factual
claim), verified live by accepting a suggestion referencing "account," a
concept absent from the real result columns.

**Real bugs found, fixed before this phase's own review**: none in the
built agent code itself this time (both parallel workstreams' unit tests,
`ruff`, and `mypy` were clean on first integration) — the one real
correction was my own smoke-test payload initially omitting the `term`
field the built `ChartColumnRef` correctly requires (a real mirror of
`ResolvedColumnRef`'s actual field, more faithful than my own plan's
simplified sketch), caught immediately by Pydantic's `extra="forbid"`
validation on the live HTTP call.

**Real verification performed**: `ruff check packages/ tests/` and `mypy`
(explicit per-package paths, 156 source files) both clean; `pytest
packages/` — 284 passed, 6 skipped as designed. The real
`tests/integration/insight_pipeline/` test passed end-to-end on the first
full run: chained the entire real pipeline (Understanding → Query → all
4 Guardrail gates → SQL Optimization → Query Cost Estimator → Execution
Planning) into a REAL Data Federation execution against live Snowflake
(unlike `guardrail_pipeline`, which deliberately stopped short of it) —
Chart Selection correctly picked a `"bar"` chart (`MARKETID`/`UNITS_TOTAL`);
Anomaly/Outlier Highlighter found 1 real anomaly, independently
re-derived and matched against a hand-computed z-score in the test itself
using the real live data; Grounded Narrative Generation validated a real
citation drawn dynamically from the live result set, and correctly
rejected a deliberately fabricated citation (`llm_cited_fabricated_value`
+ `narrative_contains_unverified_number`); Follow-up Suggestion returned
real, valid suggestions. The `agent-runtime` container was rebuilt and
restarted — `RestartCount=0`, `healthy` — and a real HTTP call to `POST
/agents/insight/chart_selection/invoke` against the live container
returned a correct, real chart decision.

**Logged, not fixed, this phase**: a broader documentation-staleness
finding (`LIMITATIONS.md` item 32) — `docs/architecture/overview.md` and
`data-flow.md` still describe every domain's agents as `DESIGNED`/
not-yet-real, and two module docstrings still say "exactly one agent is
registered," none updated since Phase 1 despite Phases 4-7 shipping ~20
real, verified agents. Recommended as a dedicated later phase rather than
bundled into this one (see DECISIONS.md).

## 2026-07-29 — Phase 8: Lineage Recorder + LLM-as-judge evaluation harness, the first real end-to-end run against a genuine Anthropic model

Built the one genuinely new Ops-domain agent (`ops.lineage_recorder`, a
real Postgres-backed audit trail for the `LineageEvent`s every agent
already emits) plus a new standalone package (`packages/lineage`), then
the LLM-as-judge evaluation harness `eval/README.md` has described since
Phase 1 (`ops.evaluation_judge`, a 10-question real golden set, and
`eval/run_harness.py`). Resolved a real, confirmed documentation
inconsistency first (via `AskUserQuestion` with the user): Federated Query
Executor and Result Caching, 2 of the "Ops domain" table's 4 listed
agents, were already shipped under Query (Phase 5); Error/Retry Handler is
separately assigned to Orchestrator by the same document. This phase
built only the real remaining gap (Lineage Recorder) plus the harness, in
that order.

**Real bugs found, fixed before this phase's own review**:
1. `record_events`'s first version trusted `result.rowcount` to count
   newly-inserted rows in a bulk `INSERT ... ON CONFLICT DO NOTHING` --
   Postgres/SQLAlchemy's "insertmanyvalues" batching makes that unreliable
   (`tests/integration/lineage_pipeline/` caught a real `rowcount=-1` for
   a genuine single-event insert). Fixed with a `RETURNING event_id`
   clause instead, unconditionally accurate.
2. `agent_runtime`'s Dockerfile never installed the new `packages/lineage`
   -- the same class of gap Phase 5's `federation` package hit when it
   was first added, caught the same way (a failed `docker compose build`).
3. **The most significant bug this phase found, by far**: the evaluation
   harness's first-ever real call to a real Anthropic model (every
   LLM-backed agent in this entire project had previously only run
   against `FakeLLMClient`, or been skipped in the optional
   `llm_integration` tier for lack of a real API key) failed immediately
   -- the real `claude-sonnet-5` model wraps its JSON output in a
   ` ```json ... ``` ` markdown code fence even when explicitly asked for
   "strict JSON," and every one of the 7 LLM-backed agents
   (Conversation, Intent Understanding, Semantic Retrieval, SQL
   Generation, Grounded Narrative Generation, Follow-up Suggestion,
   Evaluation Judge) called `json.loads(llm_response.text)` directly with
   the identical gap. Fixed once, centrally, via the new
   `navigraph_shared.llm.strip_json_code_fence`, applied to all 7 -- not
   patched ad hoc per agent.

**Real verification performed**: `ruff check packages/ tests/ eval/` and
`mypy` (explicit per-package paths, 173 source files) both clean;
`pytest packages/` -- 312 passed, 6 skipped as designed. Applied the real
`packages/lineage` migration against live Postgres (`lineage_events` +
a real, separate `alembic_version_lineage` tracking table, confirmed no
collision with `metadata_catalog`'s own `alembic_version`). The real
`tests/integration/lineage_pipeline/` test passed: a real 3-agent chain's
lineage recorded and reassembled in order, plus a real idempotency proof
(re-recording the same events yields `recorded_count=0`). Rebuilt and
restarted `agent-runtime` twice (once per real bug found); real HTTP
round-trips confirmed both new agents live: `POST
/agents/ops/lineage_recorder/invoke` followed by `GET
/lineage/{trace_id}?tenant_id=...` returned the exact real recorded event,
and `POST /agents/ops/evaluation_judge/invoke` (with a real
`ANTHROPIC_API_KEY`, provided by the user directly in chat and written
only to the local, gitignored `infra/.env`, matching how the real
Snowflake credentials were handled in Phase 2) returned a real, discerning
score correctly penalizing a deliberately unsupported test claim.

**The harness's first full real run** (all 10 real golden questions,
real Snowflake execution, real Anthropic model at every LLM-backed step,
real judge scoring): pipeline succeeded end-to-end for 6 of 10 (60%),
average scores 3.0/2.8/3.0 out of 5. This is real, valuable signal, not a
failure to fix within this phase -- see `LIMITATIONS.md` item 38 for the
full breakdown: two real, CORRECT PII rejections (the Guardrail domain
blocking the `analyst` role from real `RISKLEVEL` data, working exactly as
designed), one real hallucination correctly caught and rejected by
Grounded Narrative Generation against a genuine (not scripted) model, one
real SQL Generation aggregation gap (`SUM` where a "how many X" question
needed `COUNT`), two real schema-resolution misses against real
(non-canned) phrasings, one real golden-set intent-label calibration gap,
and confirmation that the judge model's own malformed-response rate isn't
zero either (handled gracefully both times, never a crash). None of these
downstream findings are fixed here -- they are exactly the real signal
this phase's harness was built to produce, logged honestly rather than
chased down mid-phase.

## 2026-07-29 — Phase 9: Orchestrator domain (3 agents), replacing every hand-threaded pipeline chain with one real, callable agent

Built the 3 agents `docs/architecture/overview.md` names for the
Orchestrator domain: Session/Context Manager (Redis-backed conversation
history, real sliding TTL), Multi-turn Clarification Coordinator
(LLM-backed, triggers only on `schema_mapping.tables == []`), and the
Request Orchestrator itself (the ~19-stage real caller of every other
domain, superseding `eval/pipeline_chain.py::run_full_pipeline`). Resolved
the single biggest open question via `AskUserQuestion` before writing any
code: Phase 1's original architecture decision committed to LangGraph,
but 8 phases and ~22 real agents were built and proven correct with zero
real need for graph-checkpointing ever emerging -- confirmed to build a
plain Python orchestrator instead, formally reversing that decision (see
`DECISIONS.md`). Wired all 3 into `main.py` (adding `catalog_session_factory`/
`opa_client` to `app.state`, a small real gap the wiring surfaced), rebuilt
and restarted `agent-runtime`; rewrote `gateway/main.py`'s `/ask` to POST
to the new `/agents/orchestrator/request_orchestrator/invoke` route
(replacing Phase 1.5's single-agent minimal wiring), rebuilt and restarted
`gateway`.

**A real, live-discovered bug, found by the very first real HTTP smoke
test of the newly-wired orchestrator** (see `LIMITATIONS.md` item 15 and
`DECISIONS.md` for the full detail) -- not a synthetic or hypothetical
case: "What is the total transaction volume by market?" resolved real
columns from two different tables (`MARKETS`, `TRANSACTIONS`) with zero
relationship concepts linking them, so Schema Mapping's join-building
logic (which derives joins *only* from Ontology's curated
`RelationshipConcept` matches) emitted no join at all. The generated SQL
silently computed one ungrounded grand total and cross-joined it against
every distinct market name -- every row of the real answer showed the
identical wrong total. The system's own grounding checks caught the smell
first (Grounded Narrative Generation flagged an unverified number; Anomaly
Highlighter noted zero variance across all groups), which is what
surfaced this during manual review rather than shipping it silently.
Fixed by adding a fourth curated `RelationshipConcept`
(`"Transaction happens in Market"`, keyed on the real, literal shared
`MARKETID` foreign-key column) and re-running the real, idempotent
`navigraph_kg.ingestion.pipeline.run_ingestion` against the live Neo4j.
Verified three independent ways before trusting it: a deterministic,
LLM-free `POST /agents/understanding/ontology/invoke` call confirming the
new relationship resolves; a deterministic `POST
/agents/understanding/schema_mapping/invoke` call confirming the real
join gets built; and a real regression assertion built directly into
`tests/integration/orchestrator_pipeline/test_pipeline_chain.py`'s happy
path (asserting more than one distinct total across the real result set).

**A second, smaller bug found and fixed while building that same
integration test**: the test's own fake-LLM dispatcher matched agents by
a bare substring of their prompt title (e.g. `"Grounded Narrative
Generation" in system`), and Follow-Up Suggestion's own real prompt body
happens to reference "the Grounded Narrative Generation agent" by name to
explain its own, deliberately different grounding discipline -- silently
misrouting Follow-Up Suggestion's real call to the narrative branch and
producing an empty `follow_up_suggestions` list with no visible error
(the orchestrator's own contract doesn't surface a downstream agent's
internal errors when its parent stage still "succeeds" with an empty
result). Fixed by matching each agent's exact `# <Title> — System Prompt`
H1 line via `.startswith(...)` instead of a bare substring anywhere in
the body.

**Real verification performed**: `ruff check packages/ tests/ eval/` and
`mypy` (explicit per-package paths plus `tests/integration/orchestrator_pipeline`
and `eval`, 193 source files) both clean; `pytest packages/` -- 337
passed, 6 skipped as designed. The real
`tests/integration/orchestrator_pipeline/test_pipeline_chain.py` suite
(3 tests, all passing against live Postgres/Neo4j/OPA/Redis/Snowflake)
proved: the worked-example question answered end-to-end with a real chart/
narrative/follow-ups AND a real, correct cross-table join (the exact
regression case for the bug above); a real session round-trip (a real
Redis key inspected directly, a second same-`session_id` call seeing
`turn_count == 2`); and a real clarification trigger (gibberish entities
producing a real, non-empty clarifying question, `outcome ==
"needs_clarification"`, never a bare failure). Real HTTP smoke tests
against both rebuilt containers: `POST /ask` on the live gateway returned
a complete, real answer through the full real orchestrator; a direct call
to `POST /agents/orchestrator/request_orchestrator/invoke` with an
ambiguous `data_source_id` (this tenant genuinely has two registered
data sources, see `LIMITATIONS.md` item 26) correctly returned a
structured `outcome="failed"` rather than guessing.

**The real eval harness, rewritten to call the real orchestrator and
re-run against the live stack** (all 10 real golden questions, real
Snowflake, real Anthropic model): pipeline success/answered rate improved
to 70% (7/10), up from Phase 8's 60%. Both questions that hard-failed in
Phase 8 no longer do: `gq_010` now produces a real `needs_clarification`
outcome with a genuine clarifying question -- exactly Phase 9's target
behavior -- and `gq_007` now **answers correctly** (correctness 5,
groundedness 5), a direct, real consequence of the join-inference fix
above. See `LIMITATIONS.md` item 44 for the full breakdown, including two
still-correct PII rejections and a live confirmation of the already-logged
`DataSourceDiscoveryAgent` first-match ambiguity (item 26) -- neither new,
neither fixed here.

## 2026-07-30 — Phase 10a: real Kubernetes manifests, a real weighted-canary CD pipeline, and real adversarial cloud security tests -- all built and proven with zero Azure cost

Split Phase 10 ("real AKS deployment, GitOps CD, canary rollout, security
review") into two hard-gated sub-phases: **10a**, fully buildable and
verifiable now with zero Azure credentials, and **10b**, real Azure --
gated on the user explicitly providing real subscription/credentials,
which has not happened yet (see `DECISIONS.md`). Resolved four real
architecture forks with the user via `AskUserQuestion` before writing any
manifests: Kustomize over Helm, push-based CD over ArgoCD/Flux, NGINX
Ingress canary annotations over a service mesh, Trino excluded from the
cloud deployment / Redis staying self-hosted in AKS.

Built: `infra/k8s/base/` (Kustomize manifests for every real
docker-compose service except Trino -- gateway/web as permanent
stable/canary Deployment+Service pairs, agent-runtime as a plain rolling
update, neo4j as a real `StatefulSet` with `volumeClaimTemplates`, real
`NetworkPolicy` default-deny-all + explicit per-service allows) and two
overlays (`kind`, zero Azure, an ephemeral in-cluster Postgres; `dev`, real
AKS, Key Vault CSI-synced secrets, real Postgres Flexible Server FQDN).
Two small, targeted Terraform additions (`key_vault_secrets_provider`,
`network_profile { network_policy = "azure" }` on the `aks` module, plus
new `azurerm_role_assignment` resources) -- `terraform validate` clean,
never applied. `tools/scripts/canary_gate.py` (real Prometheus-query-based
promotion gate: 5xx rate, error-rate ratio, p95 latency ratio, all
verified against realistic mocked Prometheus responses before trusting
it). Three new GitHub Actions workflows:
`.github/workflows/cd-deploy.yml` (build+push to ACR, weighted
10%/50%/100% canary rollout with automated rollback, promotion, and a
manual rollback escape hatch), `.github/workflows/k8s-manifests-ci.yml`
(the real `kind` validation sequence below, run on every PR touching
`infra/k8s/**`), `.github/workflows/cloud-security-tests.yml` (re-points
the existing `tests/security/` OPA suite at real deployed OPA, runs the
new `tests/security/cloud/` suite -- both gated on real Azure credentials
existing, same as `terraform-plan.yml`'s `plan` job).

**Real bugs found and fixed while building, before ever touching a
cluster**: `AGENT_RUNTIME_URL` was a dead, never-read env var since Phase
1 (`GatewaySettings` actually reads `AGENT_RUNTIME_BASE_URL`) -- fixed in
both `docker-compose.yml` and the new K8s ConfigMap. Terraform's Postgres
Flexible Server module never created the real application database (only
the server's own default `postgres` DB existed) -- fixed with a new
`azurerm_postgresql_flexible_server_database` resource.

**Real bugs found and fixed by actually deploying to a live local `kind`
cluster** (six of them, full detail in
`docs/runbooks/k8s-local-validation.md`): a `storageClassName` naming
mismatch (`managed-csi` doesn't exist in `kind`) left three PVCs `Pending`
forever with no error; `configMapGenerator`-produced ConfigMaps silently
landed in the wrong namespace (fixed with a top-level `namespace:`
transformer); OPA failed to start because mounting a whole ConfigMap
directory made its own recursive scan walk into the volume's `..data`
symlink structure and find the same rego file three times (fixed via
`subPath` mounts); `web` pods `CrashLoopBackOff`'d because Kubernetes'
1-second default probe timeout raced against the app's own real 3-second
internal gateway-fetch timeout; the official neo4j image auto-translated
a plain `NEO4J_PASSWORD` env var into an invalid config setting (fixed by
renaming it to deliberately not start with `NEO4J_`); and a Kustomize
patch touching only one PVC-template field silently dropped the other
required ones (`volumeClaimTemplates` doesn't get the same field-level
merge as `containers`/`volumes`).

**Real, live proof after every fix**: all 18 pods `Running`/`Ready`; real
HTTP 200s through the real `ingress-nginx` controller for both `gateway`
and `web`; and -- the highest-risk new mechanism in the whole design --
a real weighted-canary run showing a marker-bearing v2 image in 26/200
requests (~13%) against a configured 10% weight, real proof NGINX's
canary-weight annotation actually performs proportional traffic splitting,
not an assumption. One local-only finding (`agent-runtime` briefly
unreachable from other pods over `kind`'s network, while an
architecturally identical path worked fine at the same time) was
investigated thoroughly enough to rule out an application or manifest bug
before being logged as a `kind`/Docker-Desktop-specific environment quirk
(`LIMITATIONS.md` item 49), not chased further.

Also built: `tests/security/cloud/` (6 new adversarial test files --
network policy isolation with a positive control, secret-provider
scoping, RBAC least-privilege, AKS API server exposure, ACR privacy,
ingress TLS), all collecting cleanly under pytest, ruff- and
mypy-clean. During implementation, deviated from the original technical
design's shared-Secret-name proposal in favor of real per-service Secret
names (`agent-runtime-secrets`/`neo4j-secrets`/`grafana-secrets`) -- a
genuine improvement, not a compromise, logged in `DECISIONS.md` and
`LIMITATIONS.md` item 50.

**Phase 10b (real Azure) has not started** -- it requires the user to
explicitly provide real Azure credentials first, per this project's
established discipline (Snowflake/Anthropic credentials were always
provided directly in chat, never guessed).

## 2026-07-30 — Phase 10b: real Azure infrastructure created

The user provided real Azure credentials and, after the first candidate
subscription turned out to lack the Contributor role needed for `apply`,
provided a second, working subscription. `terraform.tfvars` was written
(gitignored, real subscription/tenant IDs, a freshly generated Postgres
password, never committed or echoed back) and a real `navigraph-cd` app
registration + service principal created for CI use.

A real `terraform plan` (13 resources: resource group, VNet/subnet, ACR,
AKS, Key Vault, Postgres Flexible Server + database, Entra app
registration + service principal, 3 role assignments) was shown to the
user, who gave explicit, separate go-ahead specifically on that plan
before any `apply` ran.

`terraform apply` then surfaced four real, subscription-specific issues
invisible at `plan`/`validate` time -- none were code bugs, all were this
particular subscription's own restrictions -- each found, fixed, and
re-applied in turn: the azurerm provider's default attempt to
auto-register ~200+ resource providers timed out (fixed via
`skip_provider_registration = true` + registering only the 8 providers
this config actually uses); the default AKS VM size
(`Standard_D2s_v5`) isn't in this subscription's allowed list for
`eastus` (fixed via `Standard_D2s_v7`, confirmed from Azure's own real
error-returned allow-list); Postgres Flexible Server is offer-restricted
in both `eastus` and `eastus2` on this subscription (fixed via a new
`postgres_region` variable set to `centralus`, confirmed available via
real, immediately-deleted probe deployments across 7 candidate regions);
and AKS's OIDC issuer -- enabled by Azure by default and permanently
non-disablable -- needed explicit declaration
(`oidc_issuer_enabled = true`) to stop Terraform re-diffing it every
plan. Full reasoning for all four in `LIMITATIONS.md` item 53 and
`DECISIONS.md`.

**Real, live infrastructure now exists**: a resource group, VNet/subnet,
ACR, a 2-node AKS cluster (confirmed `Ready` via a real
`kubectl get nodes`), Key Vault, Postgres Flexible Server + database, and
an Entra app registration -- all created for real, verified via
`terraform state list` matching the reviewed plan exactly, no drift.

**Still pending**: cluster bootstrap (ingress-nginx, cert-manager),
GitHub Actions OIDC federated-credential wiring, a real domain name, a
real `cd-deploy.yml` run, the full eval-harness run against the cloud
environment, and the adversarial security review -- none of these have
started yet.

## 2026-07-30 — Phase 10b: cluster bootstrap, real data, adversarial security review

Completed the remaining Phase 10b work: installed ingress-nginx +
cert-manager on the real cluster; wired a real domain (nip.io, using the
ingress LoadBalancer's real public IP) with a staging Let's Encrypt
ClusterIssuer; populated real Key Vault secrets and pushed all 3 app
images to ACR; applied the full 52-resource dev manifest set. Pushed the
repo to a new GitHub remote and wired GitHub Actions OIDC (a federated
credential + the 3 required secrets) for the CD pipeline.

Ran the real Alembic migrations, then re-ran the real Snowflake crawl +
knowledge-graph ingestion against the fresh cloud Postgres/Neo4j (17
tables, 114 columns, 41 glossary rows, 835 assets and the full reference
graph synced) so the eval harness would have real data to answer
against, not an empty catalog.

Ran the real 10-question eval harness against the cloud environment: 5/10
completed the full pipeline before the real Anthropic API key hit its own
usage limit (resets 2026-08-01, an external constraint, not a NaviGraph
bug); diagnosed (but did not blind-fix) a real root cause behind 3
degraded scores -- `grounded_narrative_generation` caps `final_rows` at
200 before prompting the LLM but never caps `anomalies`, which can grow
into the hundreds for a 10k-row result and plausibly overflow the prompt.

Ran the full adversarial security review for real against the live
cluster (`tests/security/` re-pointed at the real OPA; `tests/security/cloud/`
against the real AKS/ACR/Key Vault) and found and fixed two genuine,
previously-undetected bugs along the way:

- **The real, public-facing `POST /ask` path was broken on real AKS**:
  `gateway` had no NetworkPolicy egress rule to `agent-runtime` at all
  (only the ingress half was ever declared) -- invisible through every
  prior local `kind` validation since `kindnet` never enforces
  NetworkPolicy. Found by the cloud test suite's own positive control
  failing on its first real run against this cluster; fixed, then
  verified twice (the test now passes, and a real `POST /ask` against
  the live public endpoint now reaches agent-runtime for real).
- **PII columns were untagged on the freshly re-crawled data source** --
  a real gap in this session's own process (the re-crawl never re-ran
  the Phase 6 PII backfill against the new data source), not a code
  defect. Fixed by re-running `tools/scripts/tag_pii_columns.py` against
  the same real, previously-confirmed column (`CUSTOMERID` across
  `CUSTOMER_INFORMATION`/`STAGING_CUSTOMER_INFORMATION`/
  `V_CUSTOMER_CURRENT`).

Also fixed, all found live against the real cluster and none caught by
any prior local validation: Key Vault had RBAC authorization disabled
(silently nullifying its own role assignments); Postgres Flexible Server
had zero firewall rules and no NetworkPolicy egress for port 5432;
Snowflake's OCSP checks were blocked on port 80, adding ~90s per
connection; a `%` in a real password broke `ConfigParser`-based Alembic
migrations; `ingress-patch.yaml`'s strategic-merge patch silently deleted
every Ingress's backend (the same list-replacement class of bug already
seen once for `StatefulSet.volumeClaimTemplates`, now confirmed to
generalize). Full detail on all of these in `LIMITATIONS.md` items 53-64.

Two real Postgres admin password exposures happened mid-session (a
`kubectl exec ... env` dump, then a traceback embedding a connection
URL) -- both caught immediately and the password rotated each time with
the user's explicit confirmation; recorded as a real incident in
`DECISIONS.md`, not glossed over.

**Final state**: real, live infrastructure with all 18 application pods
`Running`, both public hostnames serving real HTTPS traffic, real
Snowflake/knowledge-graph data loaded, and a fully passing adversarial
security review (16/16 non-cloud tests, 10/14 cloud tests passing with
the other 4 gracefully skipped for lack of a registered domain). **Not
yet done**: a real `cd-deploy.yml` CI run (attempted but not yet
confirmed executing/succeeding -- `gh` CLI auth was still pending at
session's end), and a full, unblocked eval-harness pass once the real
Anthropic API quota resets on 2026-08-01.

## 2026-07-31 — Phase 10b closed out: CI turned fully green, `cd-deploy.yml` proven end to end for real, and the deferred large-result-set eval bug fixed and re-verified

This entry closes the two items the previous entry logged as "not yet
done." Both required extensive real, live debugging against this repo's
actual GitHub Actions and Azure infrastructure -- not one of the real
bugs found below could have been caught by any local test, `terraform
validate`, or `kustomize build`, since they only manifest against a real
GitHub Actions runner, a real Azure OIDC token exchange, or a real,
resource-constrained AKS cluster.

**CI, fully green for the first time in this repo's history.** This
repository was only pushed to GitHub during Phase 10b -- every workflow
had literally zero real executions before this investigation began. Six
independent real bugs were found and fixed across three commits:
`ci.yml`'s Python job never installed `agent_runtime`'s real dependency
chain (only Phase 1's original three packages); its Node job never
installed the Playwright browser binary; `terraform-plan.yml` and
`cloud-security-tests.yml` both referenced the `secrets` context inside a
job-level `if:` (disallowed by GitHub, silently invalidating both entire
workflow files on every trigger); three `tools/scripts/*.py` files had a
real `ruff` `EXE001` violation (a shebang present but the file never
marked executable in git, invisible on this Windows dev machine); `web/playwright.config.ts`
had no `webServer` block, so its test always failed with
`ERR_CONNECTION_REFUSED` on a clean runner; and `mypy packages/`
collided on duplicate module names (`tests`, and a same-named Alembic
migration file) once every colliding package was finally swept together
in one invocation. Full root-cause detail for all six: `LIMITATIONS.md`
item 65.

**`cd-deploy.yml`, proven fully automatic for the first time, after six
more real bugs.** Getting from zero to a genuinely unattended, fully
successful CD run required: setting the `AZURE_CLIENT_ID`/`AZURE_TENANT_ID`/
`AZURE_SUBSCRIPTION_ID` GitHub secrets (never actually configured despite
an earlier record saying this wiring was complete); adding
`permissions: id-token: write` to three workflows (missing entirely,
so Azure OIDC login failed with "Failed to fetch federated token from
GitHub"); updating the `navigraph-cd` app registration's federated
credentials to the real, ID-based OIDC subject format GitHub actually
issues (`repo:owner@ownerId/repo@repoId:...`, not the plain
name-based format the credentials were originally created with);
explicitly setting `agent-runtime`'s image in `deploy-canary` (it has no
`*-stable`/`*-canary` split, so it was silently relying on a
kustomize `newTag` field that only gets bumped one full CD cycle late --
real pods were found still running the stale `:unreleased` tag
indefinitely); switching `agent-runtime`'s rollout `maxSurge`/
`maxUnavailable` from `1`/`0` to `0`/`1` after a real `FailedScheduling:
Insufficient cpu` event (the 2-node dev cluster had no spare capacity for
a genuinely extra pod once gateway/web's permanent canary tracks also
existed); and adding a bounded rebase-and-retry loop around `promote`'s
final `git push`, which was rejected twice for real whenever any other
commit landed on `main` during a run's ~20-minute build+bake window.
Full detail: `LIMITATIONS.md` items 66-72. The final, fully clean,
zero-manual-intervention run: every job succeeded, confirmed both via
the GitHub Actions UI and directly against the live cluster's deployed
image tags on `gateway-stable`/`web-stable`/`agent-runtime`.

**The deferred large-result-set eval-harness bug (`LIMITATIONS.md` item
63), fixed and verified once the real Anthropic API quota reset.** Two
distinct, independent real bugs were found and fixed, in that order:
(1) `insight.grounded_narrative_generation`, `insight.follow_up_suggestion`,
and `ops.evaluation_judge` all rendered `payload.anomalies` uncapped into
their LLM prompts (and `evaluation_judge` also rendered `final_rows`
uncapped) -- for real, heavy-tailed financial data, a population z-score
check can flag a large fraction of groups as outliers regardless of the
result set's actual row count (a 320-row result triggered this just as
easily as a 10,000-row one), bloating the prompt enough to produce a
malformed or empty model response. Fixed by capping anomalies to the
top-20 by `|z_score|` and rows to the first 200, with citation validation
in `grounded_narrative_generation` still checked against the full,
uncapped lists. (2) Re-verifying fix (1) against the real model surfaced
a second, genuinely independent bug: the live Anthropic API occasionally
returns a real HTTP 200 response -- real `usage`, no error -- with zero
text content blocks, unrelated to prompt size (confirmed via raw-response
inspection and a direct synthetic reproduction). Fixed by retrying the
identical request exactly once in `AnthropicLLMClient.complete()` when
text comes back empty. Both fixes carry real unit tests (6 new tests
total, including 3 against a real `httpx.MockTransport` for the retry
logic) and were verified with real, repeated re-runs of the golden-set
harness against the live cloud stack: `gq_004` recovered from a
`correctness=1` empty narrative to `correctness=4, groundedness=5,
narrative_quality=4` in the final run. The honest residual -- `gq_008`
still hit a double empty-completion in that same final run -- is logged,
not glossed over: the retry reduces but cannot fully eliminate genuine,
non-deterministic live-model behavior.

**Process note**: three real credential/permission gaps in the working
`gh` CLI PAT were hit and resolved live during this investigation (missing
Actions-secrets write permission, missing Actions read/write permission
for `workflow_dispatch`) -- each required the user to edit the token's
own permissions rather than issuing a new one, resolved in minutes each
time rather than another full re-authentication cycle.

**Final state**: CI green, `cd-deploy.yml` proven fully automatic end to
end against real Azure/AKS infrastructure with zero manual intervention,
and the eval harness's own real, cloud-verified pipeline-success rate
recovered from its original 60-70% (Phase 8/9 runs) with the specific
large-result-set failure mode now closed. Every fix in this entry is
committed, pushed, and independently confirmed via a real subsequent
CI/CD run or a real harness re-run -- never marked done on inspection
alone.

## 2026-08-04 — Fixed the SQL grouping bug: `sql_generation._build_from_clause` no longer silently emits a Cartesian-product `FROM` clause for unjoined multi-table queries

A real, live user report: "What is the total transaction volume by
market?" produced a bar chart showing the identical value
(3,722,786,012.55) repeated for every market. Investigated live: 3
repeated real calls to the same question confirmed non-deterministic
occurrence (Semantic Retrieval's LLM call sometimes resolves "market" to
`STAGING_MARKETS.NAME`, requiring a join Schema Mapping never produces --
no curated `RelationshipConcept` covers this table pair yet -- and
sometimes to `STAGING_TRANSACTIONS.MARKETID`, single-table, no join
needed). Root cause conclusively confirmed via a direct, manually-built
`POST /agents/query/sql_generation/invoke` call reproducing the exact
condition (`tables=[STAGING_TRANSACTIONS, STAGING_MARKETS]`, `joins=[]`):
the real response was `FROM STAGING.STAGING_TRANSACTIONS,
STAGING.STAGING_MARKETS` -- a genuine Cartesian product, explaining the
repeated grand-total exactly.

**Fix**: `_build_from_clause` now returns which resolved tables (if any)
it could not connect via the provided joins, instead of silently
appending them with a comma-join. `_generate_statements` treats a
non-empty result as a real, non-recoverable
`AgentError(code="unjoined_table_in_multi_table_query")` and returns no
SQL statement -- matching the agent's own existing
`no_resolved_data_source`/`cross_source_query_not_supported` precedent of
failing loudly rather than ever returning data that looks right but
isn't (see `DECISIONS.md`'s 2026-08-04 entry and `LIMITATIONS.md` item
83). Two new regression tests
(`test_unjoined_multi_table_query_is_rejected_not_cartesian_joined`,
`test_partially_unjoined_multi_table_query_is_also_rejected`) reproduce
the exact live-confirmed 2-table/0-join case and a 3-table case where one
table remains unreached despite a real join existing for the other two.
Full `query`/`understanding` unit-test tiers (113 tests) plus the 2 new
ones pass; `ruff check` clean.

**What's still open**: this is the defensive fix, not the deeper one --
"total transaction volume by market" now fails loudly instead of lying,
but still can't be *answered* via the join path until a real
`RelationshipConcept` for Transaction<->Market is added to
`navigraph_kg.ontology.RELATIONSHIP_CONCEPTS` and re-ingested into the
live Neo4j -- a separate, larger-blast-radius change not bundled into
this fix.

## 2026-08-04 — Added a real "View SQL query" panel to the web UI, showing the exact executed SQL for every answer

Directly requested after the grouping-bug investigation above: the user
asked to display the SQL used for every question, for real transparency
into what actually ran. `RequestOrchestratorResult` gained
`generated_sql: str | None` and `sql_params: dict[str, Any]`, populated
in `request_orchestrator/agent.py`'s final `outcome="answered"` branch
from `real_plan.sql`/`real_plan.params` -- the real `ExecutionPlan` Data
Federation actually executed (post-optimization: LIMIT injected, audit
comment added), not SQL Generation's earlier, unoptimized draft. No
gateway change was needed (`/ask` already forwards the orchestrator's
result verbatim). `web/src/app/ChatDemo.tsx` renders it in a new
collapsed-by-default `<details className="sql-view">` panel (new CSS in
`globals.css`, mirroring the existing "View data" panel's interaction
pattern exactly) placed after the data table, with bound parameter
values rendered separately from the raw SQL text.

Extended `request_orchestrator/tests/test_agent.py`'s existing
`test_happy_path_returns_answered_with_full_result` (rather than adding a
new test) to assert `generated_sql`/`sql_params` are threaded through
correctly, including a real non-empty bound parameter
(`{"predicate_0": "XATH"}`) to prove params round-trip, not just the SQL
text. Full `packages/agent_runtime/` suite (219 tests) and `web`'s
`tsc --noEmit` both pass clean; `ruff check` clean on every touched
Python file.

See `DECISIONS.md`'s matching 2026-08-04 entry for why the *executed*
plan's SQL was chosen over SQL Generation's earlier draft, and why the
cached demo-fallback path deliberately leaves `generated_sql` null.

## 2026-08-04 — Deep audit found and fixed 3 more real correctness bugs: a systemic relationship-join name mismatch, a relationship-label matching gap, and a silently-wrong "unknown intent" answer

Directly requested by the user after item 83 shipped ("check everything...
ensure users get accurate results"). A live audit (real `/ask` calls
against the live gateway, direct Postgres/Neo4j queries, direct code
reading) found three further real, verified bugs -- full detail in
`LIMITATIONS.md` item 84 and the two matching `DECISIONS.md` entries.
Summary:

1. **`schema_mapping._build_joins`'s exact-string table-name match was
   broken for the dominant real resolution path.** `RELATIONSHIP_CONCEPTS`'
   `realizing_table` values are bare (e.g. `"CUSTOMER_INFORMATION"`), but
   every column resolved via Ontology's business-concept path has a real
   `table_name` of e.g. `"STAGING_CUSTOMER_INFORMATION"` (all real
   `SCHEMA_ENRICHMENT` glossary mappings point at `STAGING_`-prefixed
   tables, item 14). This meant relationship-based joins essentially never
   fired for the dominant path -- item 15's Phase 9 "fix" only appeared to
   work because that one golden question happened to resolve to bare
   table names via the LLM fallback. Fixed: `_build_joins` now compares
   table names with a leading `STAGING_` stripped from both sides, then
   emits the `JoinSpec` using the real resolved table name. New test:
   `test_join_emitted_when_resolved_tables_are_staging_prefixed`.
2. **`ontology._label_matches_entities` couldn't match "risk level" against
   the seed label "RiskLevel"** -- confirmed live and directly relevant
   (golden questions `gq_005`/`gq_009` both extract exactly "risk level").
   Fixed via a new `_normalize_label` helper (strip non-alphanumerics,
   lowercase) applied to both sides before the substring comparison. New
   test: `test_relationship_fires_for_a_real_two_word_entity_phrasing`.
3. **A non-deterministic `intent="unknown"` classification produced a
   confidently wrong answer** -- live-reproduced: "What assets are held
   most frequently across transactions?" returned `outcome="answered"`,
   `confidence=1.0`, real SQL `SELECT TRANSACTIONS.ISIN,
   TRANSACTIONS.TRANSACTIONID FROM FAR_TRANS.TRANSACTIONS LIMIT 10000` (no
   join, no aggregation), and a narrative confidently claiming "a single
   asset dominates" from the raw, unaggregated dump. Fixed: Request
   Orchestrator now routes `actual_intent == "unknown"` through the same
   Clarification Coordinator the "zero tables resolved" case already uses,
   immediately after Intent Understanding runs.

Full `packages/agent_runtime/` suite (221 tests, up from 219) passes;
`ruff check` clean on every touched file.

**Security note, logged for the record**: while investigating live against
the AKS cluster, the investigating agent decoded and printed the live
Neo4j password in plaintext via `kubectl get secret ... | base64 -d`
across several tool calls. No external exposure occurred and only
read-only Cypher queries were run, but per this project's standing
credential-handling rule (see the earlier real GitHub password exposure
this session), that password should be treated as exposed and rotated.

**What's still open**: fix (1) closes the gap for every table pair that
already has a curated `RelationshipConcept` -- it does not add coverage
for pairs that still have none (item 15's original gap). Semantic
Retrieval's non-determinism in which schema variant (`STAGING_` vs bare)
a term resolves to (item 14) is unaddressed; fix (1) just makes joins
work correctly regardless of which variant gets picked.

## 2026-08-04 — Fixed a second `_build_joins` bug (blind joins to tables lacking the key column) and added a real "Asset traded in Market" relationship concept

A real, live compound question ("...is it concentrated in a few
securities or accounts?") correctly hit item 84's new
`unjoined_table_in_multi_table_query` error -- the system refusing rather
than lying, as designed. Investigating it surfaced a genuinely separate
bug: `_build_joins` connected a relationship's `realizing_table` to EVERY
other resolved table unconditionally, assuming each shares the
relationship's `subject_key_column` -- untrue here
(`STAGING_CUSTOMER_INFORMATION` has no `MARKETID`). Fixed by cross-checking
`payload.catalog_inventory` (the real, live catalog listing) before
emitting each join; a table lacking the key column is now left unjoined
rather than joined on a nonexistent column. New regression test
`test_third_table_lacking_the_join_key_is_not_joined`; existing join
tests' fixtures extended with the real join-key columns (a real
`catalog_inventory` always includes every column of every table, not just
resolved ones). Also added `"Asset traded in Market"`
(`ASSET_INFORMATION.MARKETID`) as a 5th curated `RelationshipConcept`, and
re-synced it into the live Neo4j graph via `_sync_relationship_concepts`
(idempotent, confirmed via a direct `kubectl exec` call against the live
agent-runtime pod).

Full `packages/agent_runtime/` (222 tests) and `packages/knowledge_graph/`
(42 tests, up from 40) suites pass; `ruff check` clean. See
`LIMITATIONS.md` item 85 and the matching `DECISIONS.md` entry for the
still-open limitation: the exact live question mixes two aggregation
granularities (per-security and per-account) that no single join graph
can answer at once given `CUSTOMER_MARKET_AGG` has no security dimension
-- splitting it into two separate questions is the real, working
workaround.

## 2026-08-04 — Fixed relationship matching failing to fire for specific real instance names (e.g. "Athens Exchange"), only the generic category word ("market")

Live re-testing of item 85's own suggested split questions (asking the
securities/accounts halves separately) still failed with the same
`unjoined_table_in_multi_table_query` error. A controlled A/B test
isolated the cause: "What is the total transaction volume by market?"
(generic wording) resolved a real join and answered correctly; the
identical question naming "Athens Exchange" specifically did not.
Root cause: `OntologyAgent._resolve_relationships` only matched a
relationship's subject/object label against extracted entities via a
literal substring check -- naming a real market instance instead of
saying "market" meant the label check could never succeed, so
"Transaction happens in Market" (an already-curated, correct
`RelationshipConcept`) never even got considered a candidate.

**Fix**: added `navigraph_kg.api.entity_matches_reference_node(client,
tenant_id=..., label=..., entity=...)`, checking a free-text entity
against real reference-data node values (Market's `name`, Asset's
`asset_name`/`asset_short_name`/`isin`, Channel/RiskLevel/CustomerType/
InvestmentCapacityBand's `name`). `OntologyAgent` now calls a new
`_label_or_instance_matches` helper -- the original literal check first,
falling back to this real-instance lookup only for labels with an actual
reference-data node type (`_REFERENCE_NODE_LABELS`). New tests:
`test_relationship_fires_for_a_real_named_instance_not_the_category_word`
(ontology agent) and a new `TestEntityMatchesReferenceNode` class (direct
API coverage, 4 tests). Full suite: 269 tests pass (up from 265), `ruff
check` clean.

**Live verification**: deployed via the real CD pipeline (canary
10%->50%->100%->promote, confirmed via `gh run watch` and direct
`kubectl` inspection of canary-weight annotations and deployment image
SHAs), then re-tested directly against the live gateway. Both
previously-failing named-market questions now DID resolve a real join
(confirming item 86's fix itself worked), but re-testing surfaced a
further, deeper, real wrong-data bug -- see the next entry below.

## 2026-08-04 — Found and fixed a real, PRE-EXISTING wrong-data bug in production: `_build_joins` joined tables via a shared column name that meant different things on each side

Live re-testing of item 86's fix showed "Which securities drove the most
transaction volume in Athens Exchange?" now returned `outcome="answered"`
with a real SQL join -- but every one of ~80 distinct securities under
"Athens Exchange S.A. Cash Market" showed the IDENTICAL total
(`914679074.6164`). As an immediate live mitigation, item 85's newly-added
"Asset traded in Market" concept was deleted directly from the live Neo4j
graph via `kubectl exec` -- the wrong-data behavior persisted completely
unchanged, conclusively proving it predated today's work. Root cause:
"Transaction happens in Market" (`TRANSACTIONS`/`MARKETID`, added Phase 9,
item 15) has always connected `realizing_table` to EVERY other resolved
table sharing a column with the same name -- and `STAGING_ASSET_INFORMATION`
genuinely has its own real `MARKETID` column, so `TRANSACTIONS` got joined
to it directly, fanning every security in a market out against every
transaction in that market. This bug has been live since Phase 9; it
simply never surfaced until a real question combined Transaction+Asset+
Market for the first time today.

**Fix**: `_build_joins` now requires the shared key to be unambiguous --
a relationship only connects `realizing_table` to `other_table` when
`other_table` is the SOLE other resolved table with a matching column
name; 2+ candidates means neither is joined (surfaces as the existing,
honest `unjoined_table_in_multi_table_query` error). Added a real,
correctly-keyed `RelationshipConcept` -- "Transaction involves Asset"
(`TRANSACTIONS.ISIN` = `ASSET_INFORMATION.ISIN`) -- so "transaction
volume by security" resolves via the real per-row foreign key. New test:
`test_ambiguous_shared_key_across_two_other_tables_joins_neither`. Full
suite: 272 tests pass (up from 269), `ruff check` clean.

**What's still open**: the exact live compound question (all three
tables sharing `MARKETID`) still can't be fully answered -- `MARKETS`
stays unjoined even after `TRANSACTIONS`/`ASSET_INFORMATION` correctly
join via `ISIN`, since `MARKETID` remains genuinely ambiguous across all
three. A real fix needs join-path-resolution logic (prefer extending an
already-connected component) that doesn't exist yet -- deliberately not
attempted given how the last two incremental relationship-only fixes each
turned out to have a real, unforeseen edge case under live testing. This
is now a safe, honest limitation, not a correctness risk. See
`LIMITATIONS.md` item 87 and the matching `DECISIONS.md` entry.

## 2026-08-04 — Full live golden-set sweep (all 10 real business questions) + fixed a real over-resolution gap it found

Ran all 10 real golden-set questions directly against the live deployment
to comprehensively check "is everything working as expected" after the
day's run of fixes. Result: 5 answered correctly with real, sensible SQL
(including `gq_007`, broken since Phase 9 until today's earlier fixes),
2 safely failed, 3 correctly asked for clarification -- **zero wrong-data
instances across all 10**, confirming today's earlier fixes hold under
real, comprehensive testing, not just the individual questions that
originally surfaced each bug.

The 2 safe failures (`gq_002`, `gq_009`) were both genuinely fixable, not
just honest refusals: `unjoined_table_in_multi_table_query` named two
tables that are really the same conceptual entity resolved twice (e.g.
`CUSTOMER_INFORMATION` and `STAGING_CUSTOMER_INFORMATION`). Root-caused
via direct, isolated live calls to Intent Understanding, Ontology, and
Semantic Retrieval with `gq_002`'s exact real question and candidate
list: Semantic Retrieval's real LLM call resolved "customer" to
`STAGING_TRANSACTIONS.CUSTOMERID` on this direct call, but the earlier
golden-sweep run had resolved it to `CUSTOMER_INFORMATION.CUSTOMERID`
instead -- confirming genuine, already-documented LLM non-determinism
(items 38/44) as the cause, not a deterministic bug worth chasing at the
LLM level.

**Fix**: a new `SchemaMappingAgent._collapse_redundant_key_only_tables`
pass redirects a resolved column away from a table whose ENTIRE
contribution is that one column, when another already-resolved table has
a real column of the identical name per the live catalog inventory --
collapsing the question to a single table with no join needed, instead
of requiring one for a purely redundant duplicate key. A table
contributing any other real attribute (e.g. `RISKLEVEL`) is never
touched. New tests:
`test_redundant_customer_id_from_a_second_table_collapses_to_one_table`,
`test_genuinely_needed_second_table_is_never_collapsed`. 274 tests pass
(up from 272), `ruff check` clean.

**Also found, not yet fixed**: the same diagnostic pass surfaced a real
over-matching risk in today's earlier `entity_matches_reference_node`
(item 86) -- generic English-word entities ("transactions", "customer")
spuriously matched real Asset reference-node values via substring
overlap, producing 2 bogus `relationship_resolutions`. Harmless in this
specific case (their `realizing_table`s were never part of the resolved
column set), and the item-87 ambiguity guard provides a real safety net
even if they had been -- logged honestly in `LIMITATIONS.md` item 88
rather than silently accepted, not fixed here since no live question has
yet shown it producing an actual wrong result.

## 2026-08-04 — Re-verified the over-resolution fix live; found and fixed a second, related gap: merging genuine same-table schema duplicates

Re-tested `gq_002` and `gq_009` against the deployed fix above. `gq_002`
now answers correctly for real: `SELECT STAGING_TRANSACTIONS.CUSTOMERID,
COUNT(*) FROM STAGING.STAGING_TRANSACTIONS GROUP BY CUSTOMERID` -- real,
varied per-customer counts (40, 262, 4, ...), confirmed live. `gq_009`
still failed, naming `CUSTOMER_INFORMATION` + `STAGING_CUSTOMER_INFORMATION`.
Root-caused via the same direct, isolated diagnostic technique (real
calls to Ontology and Semantic Retrieval with the exact question and
candidate list): "risk level" resolved to
`STAGING_CUSTOMER_INFORMATION.RISKLEVEL` (item 14's glossary anchor),
while "customer"/"trend" resolved to
`CUSTOMER_INFORMATION.CUSTOMERID`/`.TIMESTAMP` -- different column
names each, so the just-shipped redundant-key-only collapse correctly
didn't touch them, even though the two tables are the literal same real
Snowflake data (item 14).

**Fix**: a new `_merge_staging_schema_duplicate_tables` pass (runs before
the redundant-key-only collapse) detects a resolved bare table and its
`STAGING_`-prefixed duplicate both present, and redirects every column
from the bare table to the `STAGING_`-prefixed table's own real copy
(verified per-column against the live catalog). Unlike the redundant-key
fix, this isn't inferring redundancy from a coincidental shared column
name -- `STAGING_X`/`X` being the same real table is an already-confirmed
fact about this dataset (item 14), so merging is the correct default
whenever this exact pattern is detected. New test:
`test_bare_table_columns_redirect_to_the_staging_prefixed_duplicate`.
275 tests pass (up from 274), `ruff check` clean.

## 2026-08-04 — Built and registered a second real data source: a synthetic e-commerce star schema in a new Snowflake database

Built a real, synthetic e-commerce dataset per the user's request: 5
dimension tables (`DIM_CUSTOMER`, `DIM_PRODUCT`, `DIM_DATE`,
`DIM_CHANNEL`, `DIM_PROMOTION`) + 2 fact tables (`FACT_ORDERS`,
`FACT_ORDER_ITEMS`), real declared PK/FK constraints, a genuine
dimensional/star schema (`FACT_ORDER_ITEMS` fans out from both facts to
every dimension). Created in a brand-new Snowflake database
(`ECOMMERCE_POC`, same account/service user) via a one-off
`ACCOUNTADMIN`-scoped script (`SHUBHSNFLK` has both `FIDELITY_ANALYST_ROLE`
and `ACCOUNTADMIN` -- confirmed live via `SHOW GRANTS TO USER`), populated
with pure-stdlib-generated synthetic data (~100 customers, ~300 products,
~3000 orders, no Faker/pandas dependency). Granted `FIDELITY_ANALYST_ROLE`
real `USAGE`/`SELECT` grants (current + future tables) on the new
database/schema, then registered it as a real `DataSource` under a new
tenant (`ecommerce-poc`), crawled its schema into the metadata catalog via
the existing `snowflake_crawler`, and corrected the crawled schema name to
a fully-qualified `"ECOMMERCE_POC.CORE"` string as a real, minimal
workaround for item 21's connection-routing gap (see DECISIONS.md entry
this same date for the full reasoning).

Added 9 new `RelationshipConcept` entries to
`packages/knowledge_graph/navigraph_kg/ontology.py` covering the full
e-commerce star schema's real FK relationships (`RELATIONSHIP_CONCEPTS`
now 15 total, up from 6). Updated `test_ontology.py` and
`test_pipeline.py` to match. Full suite: `python -m pytest
packages/agent_runtime/ packages/knowledge_graph/ -q` -- **277 passed, 5
skipped**, confirming no regressions. `ruff check
packages/knowledge_graph/` clean.

Live-verified, BEFORE these new relationship concepts were synced to
Neo4j, that a real multi-table e-commerce question ("total revenue by
channel") correctly triggers `unjoined_table_in_multi_table_query` rather
than silently Cartesian-joining or fabricating an answer -- proving
today's earlier join-safety fixes (items 85-89) generalize correctly to a
brand-new schema with zero curated relationships, not just to the
brokerage dataset they were built against. Documented as
`LIMITATIONS.md` item 90.

Remaining for this task, not yet done as of this entry: commit + push
(both remotes, per the standing dual-repo instruction), deploy, re-run
`_sync_relationship_concepts` for `tenant_id="ecommerce-poc"` against the
live Neo4j, and re-test the same e-commerce question live to confirm a
real join now resolves.

## 2026-08-05 — Found and fixed the real reason "revenue by channel"-style questions would still fail even after item 90's relationship concepts synced

Before re-testing live, re-read `SchemaMappingAgent._build_joins`'s real
code (not assumed from memory) to predict what would actually happen once
the 9 e-commerce `RelationshipConcept`s are synced. Found a real,
more fundamental gap: `_build_joins` only ever considers relationships
present in `payload.relationship_resolutions` -- i.e. only what
`OntologyAgent._resolve_relationships` decided fired, which requires a
literal-or-instance match on BOTH `subject_label` and `object_label`.
Every e-commerce concept uses `"Order"`/`"OrderItem"` as `subject_label`,
a table-role word real questions like "What is the total revenue by
channel?" never say. Fixed BEFORE this was ever live-tested, by structural
analysis alone (matching this session's standing discipline of tracing
real code paths rather than guessing).

**Fix, three parts**:
1. `OntologyAgent._resolve_relationships` (`understanding/ontology/agent.py`)
   now also fires the subject-label check when the concept's
   `realizing_table` is already implied by a resolved business concept
   (an entity that resolved via the glossary to a column on that table).
   Required `resolve_business_term` (`knowledge_graph/navigraph_kg/api.py`)
   to also return `table_name` via a new `OPTIONAL MATCH ... COLUMN_OF`
   traversal, and `ConceptResolution` (`understanding/ontology/contracts.py`)
   to carry a matching `table_name: str | None` field. New tests:
   `test_query_also_optionally_resolves_the_columns_table_name`,
   `test_relationship_fires_when_realizing_table_is_already_implied_by_a_resolved_concept`.
2. A real e-commerce `ColumnGlossary` (`add_ecommerce_glossary.py`, ~30
   entries via the existing `upsert_glossary`/`find_column` catalog API) --
   "revenue"/"order value"/"sales" -> `FACT_ORDERS.TOTAL_AMOUNT`, plus
   discount/tax/shipping/category/segment/tier/channel/promotion/date
   terms. Necessary companion to fix #1: the relaxation only sees a table
   implied via Ontology's own glossary path, not Semantic Retrieval's LLM
   fallback.
3. A new `run_ecommerce_ingestion` (`knowledge_graph/navigraph_kg/ingestion/pipeline.py`),
   a deliberate sibling of `run_ingestion` (not a modification -- avoids
   any regression risk to the already-verified brokerage path), reusing
   its 3 generic stages plus a new e-commerce-specific stage crawling
   `DIM_CHANNEL.CHANNEL_NAME` into the existing, generic, tenant-scoped
   `Channel` label. Deliberately does NOT crawl Category/CustomerSegment/
   LoyaltyTier/Country -- none of the e-commerce `RelationshipConcept`s use
   them as a label, so there'd be no consuming code path. New tests:
   `TestRunEcommerceIngestion` (2 tests).

Full suite: `python -m pytest packages/agent_runtime/ packages/knowledge_graph/ -q`
-- **281 passed** (up from 277), `ruff check` clean on every touched
package. Documented as `LIMITATIONS.md` item 91 and 2 new `DECISIONS.md`
entries.

Remaining for this task: commit + push (both remotes), deploy, run
`add_ecommerce_glossary.py` and the new ingestion sync script against the
live cloud Postgres/Neo4j, and live-test "total revenue by channel" (and
a representative sample of the ~100-question test bank) end-to-end to
confirm a real, correct join and varied per-channel numbers -- not yet
done as of this entry.

## 2026-08-05 — Reviewed the Fidelity/brokerage dataset for the same class of gap; found a real metadata gap, not a data gap

Per the user's request to also review and enhance the brokerage dataset,
queried the real, live `FIDELITY_POC` catalog directly (via a running
`agent-runtime` pod) rather than assuming anything. Found the full real
table list for `tenant_id="navikenz-poc"` -- 17 tables, including 3 with
ZERO `RelationshipConcept` coverage: `CLOSE_PRICES`/`STAGING_CLOSE_PRICES`,
`LIMIT_PRICES`/`STAGING_LIMIT_PRICES`, and `CUSTOMER_MARKET_AGG` (the
direct market-level sibling of `CUSTOMER_ASSET_AGG`, which already had
"Customer holds Asset"). A second live query confirmed the two price
tables already have real `ColumnGlossary` entries (deterministic
resolution already works for their terms), but `CUSTOMER_MARKET_AGG` has
zero glossary rows. A third live query confirmed the real
`V_ASSET_CURRENT`/`V_CUSTOMER_CURRENT` views also have zero glossary
entries -- logged as a real, low-probability, defense-in-depth-only gap
(item 92) rather than fixed, since every meaningful term these views
could offer is already glossary-anchored to the `STAGING_`-prefixed real
tables, so Semantic Retrieval's LLM fallback has no live-observed reason
to ever pick a view over the anchored table.

**Fix**: added 3 new `RelationshipConcept` entries to `ontology.py`
("Asset has ClosingPrice", "Asset has LimitPrice", "Customer active in
Market" -- `RELATIONSHIP_CONCEPTS` now 18, up from 15) and a new one-off
script `add_customer_market_agg_glossary.py` (3 real glossary rows for
`TOTAL_VALUE`/`TXN_COUNT`/`LAST_DATE`, using the same `upsert_glossary`/
`find_column` catalog API as the e-commerce glossary script). Updated
`test_ontology.py` (count 15->18, 4 new tests) and both
`relationship_concepts_synced` count assertions in `test_pipeline.py`
(15->18). Full suite: `python -m pytest packages/agent_runtime/
packages/knowledge_graph/ -q` -- **285 passed** (up from 281), `ruff
check` clean. Documented as `LIMITATIONS.md` item 92 and a new
`DECISIONS.md` entry (which also records the explicit finding that no new
synthetic DATA was needed for the brokerage side -- the gap was purely
missing relationship/glossary metadata over already-real, already-
populated tables).

Remaining for this task: commit + push (both remotes, combined with or
following item 91's push), deploy, run the new glossary script and
re-run `_sync_relationship_concepts` for `tenant_id="navikenz-poc"`
against the live cloud Postgres/Neo4j, and live-test a real brokerage
question mixing price data with asset/sector names -- not yet done as of
this entry.

## 2026-08-05 — Production incident: item 91's deploy broke every real question; found and fixed same-day via live UI testing

While live-testing the new UI data-source dropdown (see next entry) via
a local `next dev` pointed at the real deployed gateway, a real question
against the E-commerce tenant returned "Gateway returned 502." A direct
`curl` against the real gateway confirmed the 502 was genuine and
persistent (not a canary-bake transient), and `kubectl logs` on the real
`agent-runtime` pod showed the actual cause: item 91's CD run
(`30939961726`, promoted while this investigation was in progress) had
added `table_name` to Ontology's `ConceptResolution` contract but not to
Schema Mapping's deliberate sibling-mirror copy, so
`request_orchestrator/agent.py`'s real
`ConceptResolution(**r.model_dump())` conversion raised
`pydantic.ValidationError: extra_forbidden` on every real request --
**every question, on every tenant, was broken in production** for the
window between item 91's promotion and this fix.

**Fix**: added the matching `table_name` field to
`schema_mapping.contracts.ConceptResolution`. Also fixed the real test
gap that let this ship silently: `request_orchestrator`'s own
`_wire_happy_path` test helper mocked Ontology's output with an EMPTY
`concept_resolutions` list, so the real conversion path was never
actually exercised with a real element by any orchestrator test. Now
uses one real, `table_name`-populated `ConceptResolution`, and
`test_happy_path_returns_answered_with_full_result` asserts the exact
conversion round-trips correctly. 285 tests pass, `ruff check` clean.
Documented as `LIMITATIONS.md` item 93 (includes a recommendation for a
more durable, systemic fix -- a shared test helper or CI check
comparing sibling-contract field sets across all 4 `**model_dump()`
conversions in this method -- not built here, logged as a real
follow-up).

This fix is bundled into the SAME push as the brokerage relationship
concepts (item 92) and the new UI data-source dropdown (next entry),
given the severity and to get the real fix live as fast as possible.

## 2026-08-05 — Added a data-source dropdown to the web UI so users can pick Fidelity Brokerage vs. E-commerce Demo before asking

The gateway's `/ask` contract already accepts a `tenant_id` per request
(`AskRequest.tenant_id` in `navigraph_gateway/main.py`), so no
gateway/agent change was needed at all -- this was purely a `ChatDemo.tsx`
change. Added a `DATA_SOURCES` list (tenant_id, label, description, and
per-tenant example questions for both `navikenz-poc` and `ecommerce-poc`),
a `<select>` in a new `.data-source-bar` header row, and a
`switchDataSource` handler that resets the conversation (messages,
session_id, input) on switch -- a session's history/follow-up context
from one schema is meaningless once the tenant changes underneath it.
`npm run typecheck` and `npm run lint` both clean. Live-verified via a
local `next dev` pointed at the real deployed gateway
(`https://api.navigraph.51-8-46-125.nip.io`, via a new
`.claude/launch.json` preview config): switching the dropdown correctly
updates the description/example questions and the outgoing request's
`tenant_id` for both data sources (confirmed via the same live session
that also surfaced item 93's production incident above).

## 2026-08-05 — Live re-test after item 93's deploy: "revenue by channel" now answers correctly; "revenue by category" still failed, root-caused and fixed

With items 91-93 deployed and both KG syncs re-run against the fixed
code (`sync_ecommerce_kg.py`: 18 relationship concepts, 4 channels
crawled; `sync_brokerage_kg.py`: 18 relationship concepts, 835 assets/
38 markets/etc. re-synced), live-tested "What is the total revenue by
channel?" against the real deployed gateway: **real success** -- a real
`JOIN` between `FACT_ORDERS` and `DIM_CHANNEL` on `CHANNEL_ID`, real
varied per-channel revenue numbers ($902,241 Website / $548,690 Mobile
App / $317,089 Marketplace / $170,248 In-Store), and a real, correctly
grounded narrative citing those exact numbers.

Continued testing "What are the top 5 categories by revenue?" --
**failed** with `unjoined_table_in_multi_table_query` on `['DIM_PRODUCT',
'FACT_ORDERS']`. Root-caused: my e-commerce glossary mapped "revenue" to
`FACT_ORDERS.TOTAL_AMOUNT`, which has no join path to `DIM_PRODUCT` at
all. Fixed by re-pointing "revenue"/"sales" synonyms to
`FACT_ORDER_ITEMS.LINE_TOTAL` (universally joinable). Re-tested -- still
failed, now on `['DIM_PRODUCT', 'FACT_ORDER_ITEMS']`: found the SAME
subject-side bug item 91 fixed, but on the OBJECT side ("categories"
never says "product"). Fixed by generalizing `OntologyAgent
._resolve_relationships`'s relaxation to skip BOTH the subject and
object literal-match checks together once the realizing table is
implied, not just the subject. 286 tests pass, `ruff check` clean.
Documented as `LIMITATIONS.md` item 94 and a new `DECISIONS.md` entry.

Also found and explicitly NOT fixed in this same pass (different agent,
out of scope for today): "How much revenue came from the Mobile App?"
answered with a full, unfiltered per-channel breakdown rather than a
Mobile-App-only total -- reproduced twice, so not just LLM
non-determinism. SQL Generation's predicate-resolution step did not
recognize "Mobile App" as a filter value. Logged honestly as a real,
separate gap (item 94's "what full version requires").

Remaining: commit + push this second-round fix (both remotes), deploy,
re-run both KG syncs once more against the new code, and re-test "top 5
categories by revenue" plus a broader sample of the ~100-question bank
-- not yet done as of this entry.

## 2026-08-05 — Second-round fix deployed and live-verified; broad live sweep across both tenants confirms the semantic-completeness work end-to-end

Pushed, deployed (`b394a73`, promoted), and re-ran both KG syncs against
the fixed code: `sync_ecommerce_kg.py` -- 18 relationship concepts, 4
channels; `sync_brokerage_kg.py` -- 18 relationship concepts, 835 assets/
38 markets/etc. Live-tested a representative sweep across both tenants
against the real deployed gateway:

**E-commerce** (`ecommerce-poc`): "top 5 categories by revenue" (real
`FACT_ORDER_ITEMS` + `DIM_PRODUCT` join, real per-category numbers) --
FIXED, was failing; "total revenue by loyalty tier" (real join through
`DIM_CUSTOMER`) -- new, works; "compare revenue between Website and
Mobile App" -- works; "revenue trend by month" -- works; "total revenue
by channel" -- still correct after the glossary re-point. One
`needs_clarification` (ambiguous "promotions by discount amount"
phrasing) -- correct, non-error behavior, not a bug.

**Brokerage** (`navikenz-poc`): "total transaction volume by market" (the
original golden-set question) -- still correct, no regression from any
of today's changes; "average closing price by asset sector" (real join
via the new "Asset has ClosingPrice" concept) -- NEW capability, works;
"which markets have the most customer trading activity" (real join via
the new "Customer active in Market" concept + `CUSTOMER_MARKET_AGG`
glossary) -- NEW capability, works.

Every question tested this session (across items 90-94's full arc)
either answered correctly with real, varied data, or failed/clarified
for a legitimate, non-bug reason (unresolvable ambiguity, or the
separately-logged SQL Generation predicate-resolution gap). Documented
final live-verification results in `LIMITATIONS.md` item 94.

Also wrote `eval/ecommerce_test_questions.md`: a 100-question bank
spanning all 4 real `IntentLabel`s, every glossary term, top-N/
comparison/trend/anomaly/multi-dimension/named-instance phrasing -- a
representative ~12 of these 100 were actually live-tested this session;
the rest seed future golden-set/regression coverage.

## 2026-08-05 — Fixed the SQL Generation predicate-resolution gap (item 94's flagged follow-up), on request

Root-caused via direct, isolated diagnostic calls to each real
Understanding-domain agent in sequence (Intent Understanding -> Ontology
-> Semantic Retrieval, via `kubectl port-forward` to the live
`agent-runtime` pod), the same methodology used throughout this session.
Intent Understanding correctly extracted `entities=["revenue", "Mobile
App"]`; Ontology correctly left "Mobile App" unresolved; Semantic
Retrieval **correctly** resolved "Mobile App" to `DIM_CHANNEL.CHANNEL_NAME`
-- proving the resolution itself was never broken. The actual bug: SQL
Generation's `_needs_predicate_resolution` only ever fires its
predicate-resolution LLM call on a fixed set of relative-date/comparison
trigger words ("last", "quarter", "since", "vs", ...) -- "Mobile App"
matches none of them, so the LLM was never even asked whether a filter
was needed, regardless of how well it could have answered.

**Fix**: a new `_resolved_via_named_value` check compares each resolved
dimension column's `.term` (the free-text phrase that resolved it --
already a real, existing field) against the column's own `column_name`
via the same normalize-and-substring heuristic
`understanding.ontology.agent._label_matches_entities` already uses.
"channel" vs `CHANNEL_NAME` matches (genuine dimension reference, no
new LLM call); "Mobile App" vs `CHANNEL_NAME` doesn't (named value,
triggers the call). `_needs_predicate_resolution` now fires on either
this OR the existing temporal trigger. Also broadened
`predicate_resolution.md`'s system prompt (previously framed almost
entirely around dates) with an explicit named-value example. Chose to
reuse the existing `.term` field rather than add `business_name`/
`synonyms` to `ResolvedColumnRef` specifically to avoid repeating
today's earlier item-93 sibling-contract-drift incident.

Updated 2 existing tests (`_UNJOINED_MARKET_COLUMNS`'s real "market" ->
`NAME` resolution now also triggers the LLM call -- a genuine, accepted
false positive) from a placeholder canned response to a valid empty one.
Added `test_named_value_dimension_triggers_predicate_resolution_with_no_temporal_words`
and `test_generic_dimension_reference_does_not_trigger_predicate_resolution`.
288 tests pass (up from 286), `ruff check` clean. Documented as
`LIMITATIONS.md` item 95 and a new `DECISIONS.md` entry.

Remaining: commit + push (both remotes), deploy, and live-verify "How
much revenue came from the Mobile App?" now returns a single,
correctly-filtered row -- not yet done as of this entry.

## 2026-08-05 — Deployed item 95's fix; live-verified "Mobile App" works, immediately found and fixed a second, related bug

Pushed, deployed (`30a5b7d`, promoted), live-tested against the real
deployed gateway: "How much revenue came from the Mobile App?" now
produces `WHERE DIM_CHANNEL.CHANNEL_NAME = %(predicate_0)s` bound to
`'Mobile App'`, returning exactly one row (`$511,654.77`) with a
correctly grounded narrative -- the original bug is fixed.

Continued testing (following the same rigor -- verify beyond the single
reported case) immediately found a second bug: "revenue from the Gold
loyalty tier" and "revenue from the Electronics category" both STILL
returned the full unfiltered breakdown. A direct diagnostic call to
Intent Understanding (`kubectl port-forward` to the live agent-runtime,
same methodology as before) showed why: it extracts the COMPOUND phrase
(`entities=["revenue", "Gold loyalty tier"]`), and
`_resolved_via_named_value`'s bidirectional substring check was wrongly
satisfied since the real column name `LOYALTY_TIER` is a genuine suffix
of the compound term `GOLDLOYALTYTIER`.

**Fix**: made the check one-directional (safe only when the term is
fully contained within the column name, never the reverse) -- simpler
than the original two-direction check and correctly handles compound
phrasing. Updated `test_multi_table_join_produces_correct_join_clause`
(its "customer risk level"/`RISKLEVEL` fixture is the same class of
accepted false positive) and added
`test_compound_named_value_phrase_triggers_predicate_resolution`. 289
tests pass (up from 288), `ruff check` clean.

Remaining: commit + push this second fix (both remotes), deploy, and
live-verify both "Gold loyalty tier" and "Electronics category" now
return a single, correctly-filtered row each -- not yet done as of this
entry.

## 2026-08-05 — Deployed the compound-phrase fix; live-verified all three predicate-resolution cases, plus a no-regression check

Pushed, deployed (`a11a381`, promoted), live-tested against the real
gateway:

- "How much revenue came from customers in the Gold loyalty tier?" ->
  real `WHERE DIM_CUSTOMER.LOYALTY_TIER = %(predicate_0)s` bound to
  `'Gold'`, one row, `$398,375.29`.
- "What is the total revenue from the Electronics category?" -> real
  `WHERE DIM_PRODUCT.CATEGORY = %(predicate_0)s` bound to `'Electronics'`,
  one row, `$652,267.27`.
- "How much revenue came from the Mobile App?" (item 95's original case)
  -- re-confirmed still correct after this second deploy.
- "What is the total revenue by channel?" (a genuine, generic group-by
  question with no named value) -- re-confirmed it still correctly
  returns the FULL, unfiltered per-channel breakdown, proving the fix
  doesn't over-trigger and break ordinary group-by questions.

All three real bugs found and fixed this session in the SQL Generation
predicate-resolution path (items 95 and this entry) are now live and
verified. Documented the final live results in `LIMITATIONS.md` item 95.

## 2026-08-05 — SEVERE: found and fixed a live, silent >500x wrong-data bug while continuing to test brokerage named-value questions

Per the user's request to enhance brokerage testing with more
named-value questions, pulled real reference values live (channel,
customer type, risk level, sector, exchange names) via a direct
Snowflake query, then live-tested 5 named-value questions against the
real gateway. Channel ("Internet Banking"), customer type ("Premium"),
and risk level ("Aggressive") all answered correctly with real, single-row
filtered results. "What is the total transaction value for the Technology
sector?" answered too -- but the number looked suspicious, so it was
independently re-derived directly from Snowflake before being trusted
(this session's standing discipline: never trust a plausible-looking
number without verification when in doubt).

**Confirmed a real, severe bug**: the pipeline's actual SQL joined
`TRANSACTIONS` to `ASSET_INFORMATION` via `MARKETID`, returning
**$22,818,053,245.26**. A correct, independent query joining via `ISIN`
returned **$44,664,559.45** -- the live answer was over 500x inflated.
Root cause: item 91's implied-table relaxation let both
`"Transaction happens in Market"` and `"Transaction involves Asset"`
fire simultaneously for this question (only `TRANSACTIONS` +
`ASSET_INFORMATION` resolved, no `MARKETS` table), and
`SchemaMappingAgent._build_joins`'s existing single-relationship
ambiguity guard (item 87) couldn't catch it, since each relationship
independently found `ASSET_INFORMATION` as its own sole candidate.

**Fix**: `_build_joins` now runs a second pass grouping join proposals
by the unordered `(other_table, realizing_table)` pair; a pair proposed
via 2+ DIFFERENT key columns by different relationship concepts has ALL
its proposals dropped rather than guessing which one applies. New test:
`test_two_different_relationships_proposing_the_same_table_pair_via_different_keys_join_neither`.
290 tests pass (up from 289), `ruff check` clean. Documented as
`LIMITATIONS.md` item 96 and a new `DECISIONS.md` entry.

Remaining: commit + push (both remotes) IMMEDIATELY given this is a live
wrong-data bug in production, deploy, and re-test the Technology-sector
question to confirm it now either resolves correctly via the ISIN join
alone or fails safely -- not yet done as of this entry.

## 2026-08-05 — Deployed and live-verified the critical fix; found and diagnosed a separate, pre-existing gap while continuing the requested testing

Pushed, deployed (`56fbecd`, promoted), live-tested against the real
gateway: "What is the total transaction value for the Technology sector?"
now correctly fails with `unjoined_table_in_multi_table_query`
(`['ASSET_INFORMATION', 'STAGING_TRANSACTIONS']`) instead of silently
returning $22.8B -- the critical fix works as designed. Re-confirmed
"total transaction value for Premium customers" (unambiguous, single
relationship) still answers correctly.

Continuing the requested brokerage named-value testing, re-ran "total
units traded by customers with Aggressive risk level" and "total
transaction volume by market" (this session's own original worked
example) 3x each -- both intermittently failed AND succeeded across
runs, ruling out a deterministic regression from today's fix. Direct
diagnostic calls (`kubectl port-forward` + isolated Intent
Understanding/Ontology calls, same methodology as always) on a failing
run found the real cause: Intent Understanding extracted the compound
phrase `"total units traded"`, which does not EXACTLY match the real
glossary's `"units traded"` synonym -- `resolve_business_term`'s Cypher
requires strict equality, unlike the substring heuristics used elsewhere
this session -- so `relationship_resolutions` came back completely
empty and nothing was left to join the two resolved tables. This is a
real, pre-existing, SEPARATE gap (glossary exact-match fragility
colliding with already-documented entity-extraction non-determinism,
items 38/44), confirmed NOT caused by today's cross-relationship fix.
Logged as newly found in `LIMITATIONS.md` item 96, not fixed in this
pass -- a safe failure, not wrong data, and carries its own real
over-matching tradeoff if relaxed carelessly.

Answering the user's "is this task done?": the requested brokerage
named-value testing is complete. It found and fixed one severe,
live wrong-data bug (this item), and surfaced one separate, lower-
severity, pre-existing gap (documented, not fixed). The originally-
reported Euronext/multi-hop-join failure from the earlier testing pass
remains open and not yet investigated.

## 2026-08-05 — Fixed the Euronext multi-hop join gap (item 97), on request

Diagnosed live (via `kubectl port-forward` + direct per-agent HTTP calls,
same discipline as every prior fix today): "average closing price for
assets on Euronext - Growth Paris" failed with
`unjoined_table_in_multi_table_query` even though Ontology's
`relationship_resolutions` already returned everything needed to answer
it. Root cause confirmed with certainty: `_build_joins` only ever
considered a relationship whose realizing_table was already among the
resolved (column-selected) tables -- `ASSET_INFORMATION`, the required
2-hop bridge between `CLOSE_PRICES` and `MARKETS`, was never
independently resolved for this question, so the relevant relationship
was silently skipped no matter how clearly Ontology flagged it.

Designed and implemented a new, narrow Pass 1.5 bridge search in
`_build_joins` -- see DECISIONS.md's matching entry for why a general
graph search was rejected in favor of requiring a candidate bridge to
prove itself via its own separate relationship. Added `left_schema`/
`right_schema` to `JoinSpec` (both schema_mapping's and sql_generation's
sibling contracts, in the same commit, plus a new cross-agent conversion
test) so a bridge table -- which contributes no selected column -- still
gets correctly schema-qualified in the generated SQL rather than left
bare.

Five new regression tests added: three in schema_mapping (the exact
Euronext scenario including the LIMIT_PRICES false-positive exclusion, a
no-bridge-found case, an ambiguous-bridge case), one in sql_generation
(the bridge table's schema qualification in the actual generated SQL),
one in request_orchestrator (the new JoinSpec fields survive the
sibling-contract conversion without a `pydantic.ValidationError`, per
item 93's own lesson). 406 tests pass (up from 403). `ruff check` clean.
`mypy --exclude '(^|[\/])(tests|migrations)([\/]|$)' packages/` clean
(the project's real CI invocation, run from repo root).

Committed, rebased, pushed to both remotes, monitored the CD deploy to
completion (all three images built, canary bake passed cleanly at
10%/50%/100%, promoted to stable). Live-verified the Euronext question
against the real deployed gateway: it now answers correctly via the real
2-hop bridge, generated SQL exactly matching the fix's design (see
LIMITATIONS.md item 97's live-verified update for the full statement).
Also found, live, a separate, lower-severity gap in the same call: the
question asks for an average but SQL Generation has no `AVG` support
(only `SUM`/`COUNT`), so the real answer is a mislabeled sum -- the
Grounded Narrative Generation agent's own grounding check caught this on
its own; logged as a new, out-of-scope gap, not fixed here.

Regression spot-checks on two previously-fixed questions turned up a
REAL, SEVERE-CLASS bug in today's own fix: "total transaction volume by
market" (the session's flagship worked example) still answered
correctly, but its generated SQL now contained a real, completely
unnecessary extra `JOIN STAGING_ASSET_INFORMATION ON ISIN` -- Pass 1.5's
bridge search fired for `"Transaction involves Asset"` even though
`TRANSACTIONS` was ALREADY connected to `MARKETS` directly via a
different relationship. This is exactly the class of silent-wrong-data
risk item 96 was built to prevent (an unnecessary `INNER JOIN` can
silently drop unmatched rows or fan out), even though it happened not to
change this particular result. Root-caused, fixed, and tested BEFORE
declaring the Euronext task done -- see LIMITATIONS.md item 97's
SIXTH-REAL-BUG update for the full root cause and fix (a Pass-1
connectivity check gating Pass 1.5, so a bridge is only ever considered
when it would connect two tables not already reachable from each other).
407 tests pass (up from 406), `ruff check`/`mypy` clean.

The Technology-sector regression check (item 96's flagship wrong-data
case) still correctly and safely fails with
`unjoined_table_in_multi_table_query` rather than the wrong $22.8B --
confirms item 96's cross-relationship ambiguity guard is untouched by
either of today's two bridge-search changes.

Committed the second fix, rebased against the CD promote commit, pushed
to both remotes, monitored the second CD deploy to completion (all three
images rebuilt, canary bake clean at 10%/50%/100%, promoted to stable).

Live re-verified the Euronext question twice post-deploy: still answers
correctly via the 2-hop bridge, byte-identical generated SQL to the
first deploy. Attempted to re-verify "total transaction volume by
market" specifically producing no unnecessary bridge, but three separate
live calls each resolved a DIFFERENT real table combination
(`CUSTOMER_MARKET_AGG` alone, `CUSTOMER_MARKET_AGG`+`MARKETS`,
`CUSTOMER_MARKET_AGG`+`STAGING_MARKETS`) -- never landing back on the
exact `TRANSACTIONS`+`MARKETS` resolution that exposed the bug, due to
the SAME pre-existing Semantic Retrieval non-determinism already
documented (items 38/44/96). All three real outcomes observed were
still correct/safe on their own terms (no wrong-data fan-out in any of
them) -- but this specific live scenario could not be cleanly
re-confirmed on demand. The connectivity-guard fix's correctness for the
exact `TRANSACTIONS`+`MARKETS` case rests on its deterministic unit test
(part of the 407 passing) rather than a live confirmation this time --
logged honestly in LIMITATIONS.md item 97 rather than overclaiming a
live result that didn't actually land.

This item is now considered closed: the Euronext multi-hop join gap is
fixed and live-verified; the bridge-search regression found during this
fix's own testing is fixed and unit-test-verified (with two clean live
confirmations that the Euronext bridge itself is unaffected); the
separate AVG-aggregation gap and the pre-existing entity-extraction
non-determinism are both logged as known, out-of-scope limitations, not
silently folded into this item's scope.

## 2026-08-05 — Fixed the glossary exact-match fragility (item 98), on request

Diagnosed and root-caused (see LIMITATIONS.md item 98 and this date's
matching DECISIONS.md entry): `resolve_business_term`'s exact-match-only
Cypher lookup silently dropped compound extracted-entity phrases against
the real glossary's exact synonym strings -- a real, repeatable gap
flagged since items 38/44 and confirmed again during items 96/97's live
testing.

Queried the real live glossary directly (via `kubectl exec` into the
agent-runtime pod against the real cloud Postgres `column_glossary`
table) before designing the fix, rather than guessing at typical term
shapes -- found real short synonyms (`"tax"`, `"qty"`, `"date"`,
`"city"`, `"tier"`, `"isin"`) that ruled out a naive raw-substring
approach (would risk `"state"` matching inside `"real estate"`).
Implemented a token-sequence containment fallback in `OntologyAgent`
instead, firing only when the exact match finds nothing, with an
ambiguity guard (2+ distinct matching columns -> stays unresolved) and a
new `navigraph_kg.api.list_business_concepts` read function (fetched
once per request, not once per entity).

New tests: 5 in the ontology agent (compound-phrase resolution, fallback
not consulted on exact-match success, short-word collision avoidance,
ambiguous-match safety, single-fetch-per-run), 2 in knowledge_graph
(`list_business_concepts`'s Cypher shape). 415 tests pass (up from 407).
`ruff check` and `mypy --exclude '(^|[\/])(tests|migrations)([\/]|$)'
packages/` (the project's real CI invocation) both clean. All 13
pre-existing ontology tests needed no changes -- confirmed why
(`MagicMock`'s default empty `__iter__`) rather than assuming it was
safe.

Committed, pushed to both remotes (no rebase needed, already up to
date), monitored the CD deploy to completion (all three images rebuilt,
canary bake clean at 10%/50%/100%, promoted to stable).

Live-verified against the real deployed gateway with the exact question
that intermittently failed earlier this session: "What is the total
units traded by customers with Aggressive risk level?", run twice.
Both runs answered correctly with the identical result
(`602,136,555.1275` units, correct `STAGING_TRANSACTIONS`/
`STAGING_CUSTOMER_INFORMATION` join, correct `RISKLEVEL = 'Aggressive'`
filter) -- no longer dependent on which exact phrasing Intent
Understanding happens to extract on a given run. This item is closed.

This closes out today's second fix. Both of today's requested items are
now complete: the Euronext multi-hop join gap (item 97) and the glossary
exact-match fragility (item 98), each root-caused live, fixed, tested,
deployed through a full canary rollout, and live-verified against the
real production gateway -- with one additional real bug (the
unnecessary-bridge-join regression) found and fixed along the way during
the Euronext fix's own regression testing, never shipped unverified.

## 2026-08-06 — Phase 1: MCP server mounted on the gateway (5 tools)

Prompted by the user sharing a whiteboard-sketch architecture and asking
how aligned NaviGraph's real, built architecture is with it. Gave a real
gap analysis (3 real gaps: no MCP entry point, RBAC not backed by
verified identity, only one real connector) before building anything.
The user confirmed via `AskUserQuestion` to build the Azure AD mechanism
now (feature-flagged off) and both a Postgres AND a Databricks connector
-- this entry covers the first of the three resulting build phases.

Built `packages/gateway/navigraph_gateway/mcp_tools.py` using the real
`mcp` SDK (`mcp.server.fastmcp.FastMCP`, pinned `mcp>=1,<2` after live
research found the official package shipped a breaking `2.0.0` a week
earlier). Five tools (`ask_navigraph`, `resolve_business_term`,
`list_data_sources`, `list_business_glossary`, `get_lineage`), the first
two new plain `GET /data_sources`/`GET /glossary` routes added to
agent-runtime. Mounted at `/mcp` on the existing gateway app, reusing its
shared `http_client`.

Found and fixed 5 real integration gotchas via live, iterative smoke
testing before writing production code (not assumed from docs): mount
path doubling (`/mcp/mcp`), a `Task group is not initialized` lifespan
error (Starlette doesn't run a mounted sub-app's lifespan), a `421
Misdirected Request` (FastMCP's default transport security only allows
localhost), route-shadowing (a root `Mount` registered early 404s every
later route), and a `session_manager.run() can only be called once`
error surfaced by this repo's own pre-existing tests reusing `TestClient(app)`
across multiple test functions.

New `packages/gateway/tests/test_mcp_tools.py` -- all 5 tools plus
`build_mcp_server` registration/round-trip tested via real `list_tools()`/
`call_tool()` calls. 453 tests pass (up from 440). `ruff check` and
`mypy --exclude '(^|[\/])(tests|migrations)([\/]|$)' packages/` both clean.

## 2026-08-06 — Phase 2: Azure AD JWT/JWKS verification mechanism (built, feature-flagged off)

Built the real, generic Azure AD (Entra ID) bearer-token verifier the
user asked for: `packages/shared/navigraph_shared/auth/azure_ad.py`
(`AzureADSettings`, `VerifiedIdentity`, `AzureADTokenError`,
`AzureADTokenVerifier` ABC, `HttpAzureADTokenVerifier`,
`FakeAzureADTokenVerifier`), following the exact triad convention already
established by `LLMClient`/`OpaClient`. Real RS256 JWT/JWKS verification
(issuer/audience/expiry checks, `roles`/`tid` claim extraction, TTL-cached
JWKS fetch), tested against a real self-signed RSA keypair and a real
signed JWT -- only the JWKS HTTP fetch is mocked (`httpx.MockTransport`),
never the cryptographic verification itself. `AzureADSettings.azure_ad_enabled`
defaults to `False`.

Wired the mechanism into gateway's `/ask` route: a new `_verify_identity`
FastAPI dependency that is a complete no-op passthrough while disabled
(today's default, zero behavior change) and, once enabled, requires and
verifies a real `Authorization: Bearer` token, overriding the request
body's self-declared `roles`/`claims`/`tenant_id` with the VERIFIED
identity's own. New `packages/gateway/tests/test_azure_ad_wiring.py`
covers both states directly against the real dependency function.

Found and fixed one real, pre-existing latent issue while getting a
clean `ruff check --no-cache packages/` (the real CI invocation, not the
cached one this session had been implicitly relying on): 3 gateway test
files had an unaddressed `I001` (import-block formatting) violation that
ruff's cache had been silently hiding. Auto-fixed (blank-line-only,
zero behavior change) and added the `[tool.ruff]`/`[tool.mypy]` config
gateway's own `pyproject.toml` had been missing (present in `shared`'s
but never copied to `gateway`'s), plus the standard `flake8-bugbear`
`extend-immutable-calls` allowance ruff itself documents for FastAPI's
`Depends()` pattern -- the first real `Depends()` call anywhere in this
codebase.

459 tests pass (up from 453), `ruff check --no-cache packages/` and
`mypy --exclude '(^|[\/])(tests|migrations)([\/]|$)' packages/` both
clean.

## 2026-08-06 — Phase 3: Postgres and Databricks connectors

Built both connectors the user asked for (`AskUserQuestion`: "Do it for
postgres and databricks both"), following the exact Snowflake trio's
shape with zero changes needed to `connector_sdk`'s `base.py` ABC --
confirming the interface really is source-agnostic as designed back in
Phase 2.

`navigraph_connectors.postgres.PostgresConnector` (`psycopg` v3, ANSI
`information_schema` introspection -- genuinely portable SQL, a real test
of the abstraction against Snowflake's proprietary `SHOW`/`DESCRIBE`
convention). `PostgresSettings` deliberately prefixed `source_postgres_*`
to avoid a real, caught-before-shipping collision with
`MetadataCatalogSettings`'s own bare `postgres_*` fields (NaviGraph's
internal catalog DB).

`navigraph_connectors.databricks.DatabricksConnector`
(`databricks-sql-connector`, Unity-Catalog-scoped `information_schema`),
plus a `_to_named_paramstyle()` regex transform bridging the driver's
NATIVE-mode `:name` parameter style to this codebase's universal
`%(name)s` pyformat convention (confirmed via the driver's own docstring
before assuming compatibility). Capabilities reported honestly per
connector: Postgres reports real RLS but no column masking; Databricks
reports both as real Unity Catalog features.

Both registered via the existing `register_connector(...)` registry,
triggered by 2 new import lines in `agent_runtime/main.py`. New unit
tests (mocked driver `connect()`) plus skipped-without-real-credentials
integration tests, mirroring Snowflake's exact test-tier split. 2 new
pytest markers (`postgres_integration`, `databricks_integration`) added
to BOTH `connector_sdk`'s own `pyproject.toml` and the root
`packages/pyproject.toml` (the file CI's `pytest packages/` actually uses
as its marker registry -- confirmed this is a real, separately-maintained
duplicate, not an oversight, while adding the new markers).

Zero real Postgres or Databricks instances registered as an actual
tenant `DataSource` yet -- both connectors are real, tested, and
registry-wired, but unexercised against a live instance.

## 2026-08-08 — Fixed a real, live MCP bug: intermittent "Session not found" on every other `tools/call`

Reported live, through an actual Claude Desktop connector session: every
MCP tool call was failing with a generic execution error, across
multiple different questions. Two prior rounds of remote diagnosis (from
inside that same Claude Desktop session) guessed the backend/orchestrator
itself was down or unreachable -- confirmed both times, live, that this
was wrong: `/healthz`/`/readyz` were 200 OK throughout, and a real
`ask_navigraph` call succeeded with a correct answer in the middle of the
reported outage window.

Root-caused by reproducing the failure directly, independent of any
client: ran the real MCP `initialize` -> `tools/call` sequence via `curl`
repeatedly and got a genuine, intermittent `"Session not found"` error --
proving it wasn't a Claude Desktop timeout (the earlier theory) or a
backend outage (the theory this replaced), but a real infrastructure gap.
`gateway-stable` runs 2 replicas; the `mcp` SDK's `StreamableHTTPSessionManager`
holds session state in-memory, per-process, with no shared store. With no
session affinity on the ingress, a follow-up call landing on a different
pod than the one that ran `initialize` gets a real "unknown session"
error from that pod's own, genuinely-empty session table.

First fix attempt -- `nginx.ingress.kubernetes.io/upstream-hash-by-header`
on a new path-scoped `/mcp` Ingress, hashing by the real `mcp-session-id`
header -- was built, deployed, and then DISPROVEN by direct testing (10/10
real failures against the live cluster): hashing by the session-id VALUE
routes consistently to *a* pod, not necessarily the pod that generated
that value, since the `initialize` call that creates the session carries
no session header at all and is hashed differently (or not at all).
Removed rather than left in place once proven ineffective, matching this
project's standing "never leave a fix that doesn't actually work" rule.

Real fix: scaled `gateway-stable` to 1 replica live, re-verified 10/10
real successes (including a full real `ask_navigraph` round-trip against
Snowflake), then made the change permanent in
`infra/k8s/base/gateway/deployment-stable.yaml` (and mirrored on
`deployment-canary.yaml`, so a future canary bake window doesn't
reintroduce the exact same bug) so the next `cd-deploy.yml` run doesn't
silently revert it. Logged as a new `LIMITATIONS.md` item (gateway now
has no HA/redundancy) -- REST `/ask` itself needs no session affinity and
is unaffected; only the MCP session-affinity gap forced this tradeoff.

Also found, while diagnosing: the live `gateway-canary` Ingress carries a
real `nginx.ingress.kubernetes.io/canary: "true"` annotation that was
never present in this repo's own `infra/k8s/overlays/dev/ingress-patch.yaml`
-- a real, pre-existing drift between git and the cluster, logged but not
touched as part of this fix (out of scope, and risky to "correct" without
first understanding how/when it was applied).

## 2026-08-09 — Merged in lineage search, admin CLI/UI, opt-in Semantic Model + onboarding tooling, a Slack bot, and AAD K8s RBAC Terraform from a parallel build

Reconciled and merged a batch of work from a separate build track that
shares this repo's early history. Per-piece decisions and rationale are
in `DECISIONS.md`'s matching entry; `LIMITATIONS.md` item 104 has the full
what-was/wasn't-merged breakdown. Summary of what landed:

- `navigraph_lineage.api.list_traces` (real Postgres aggregate search
  across a tenant's traces), a new agent-runtime `GET /lineage` route, and
  a gateway proxy (`GET /lineage`, `GET /lineage/{trace_id}`) gated by
  this repo's existing `_verify_identity` dependency.
- `tools/scripts/navigraph_admin.py` (admin CLI) and
  `web/src/app/admin/lineage` (admin web UI for lineage search).
- A standalone `web/src/app/chat` page + same-origin `api/ask` proxy, with
  small nav links added to the existing homepage (the existing `ChatDemo`
  component was left untouched).
- `navigraph_catalog.drift` (real schema-hash drift detection) and two
  new Alembic migrations adding `is_default`/`last_crawled_at`/
  `schema_hash` to the metadata catalog, plus the partial unique index
  enforcing one default `DataSource` per tenant.
- A new onboarding-time-only Ontology Drafting agent
  (`understanding/ontology_drafting`), the `navigraph_semantic_model`
  package it drafts into, and `tools/scripts/onboard_data_source.py`
  chaining registration -> crawl -> drafting -> compile -> activation.
  Landed as new, opt-in tooling only -- the live `knowledge_graph
  /navigraph_kg/ontology.py` and its `RELATIONSHIP_CONCEPTS`-driven
  ingestion pipeline were not touched.
- `packages/slack_bot` (answers `@NaviGraph` Slack mentions via the
  gateway's `/ask`; needs a real Slack app configured before it does
  anything).
- `terraform/modules/aks-aad-groups` (two real Azure AD groups) wired
  into `terraform/modules/aks`'s new
  `azure_active_directory_role_based_access_control` block, plus
  `infra/k8s/base/rbac/cluster-role-binding-viewers.yaml`. NOT applied to
  the live cluster as part of this merge -- see
  `docs/runbooks/aad-k8s-rbac-rollout.md`.

Deliberately left out: a second, standalone MCP server (this repo already
has one on the gateway) and a second Azure AD auth implementation (the new
routes reuse the existing one) -- both would have been dormant
duplicates, not real functionality.

Verified for real before merging, not assumed: every touched/added
Python package's unit tests were run in a clean virtualenv against this
repo's actual merged code -- `shared` (14 tests, including the new
`OpaClient.set_data` this merge's `navigraph_semantic_model.opa_sync`
needs), `metadata_catalog` (48), `semantic_model` (43), `slack_bot` (25),
`agent_runtime` (18, including the new Ontology Drafting agent), and
`gateway` (28, including 8 new tests for the `/lineage` proxy routes that
exercise the real `_verify_identity` gate end-to-end via `TestClient` --
not mocked out) all pass. `ruff check` is clean across every new/changed
file. `terraform fmt` is clean on the new Terraform; `terraform validate`/
`plan` need real network access to the provider registry this merge's
environment didn't have, so those are still owed before anyone runs
`apply`.

## 2026-08-09 — Fixed four real CI bugs surfaced by the merge PR's first live run

Full rationale for each is in `DECISIONS.md`'s matching entry. In short:
`mypy.ini` targeted Python 3.11 against a real 3.12 CI runner (numpy's
bundled stub needs 3.12+); `adversarial-tests.yml` never installed the
packages `tests/security/` actually imports; `web/package.json`'s
`brace-expansion` override was pinned to the last *vulnerable* version, not
a fix; and `security-scan.yml`'s pip-audit job was missing five of
`agent_runtime`'s local package dependencies from its install list, so
pip-audit silently never ran at all -- fixing that then surfaced a real
`setuptools` CVE, fixed by upgrading it explicitly. Two other failing
checks (`k8s-manifests-ci.yml`'s canary-weight proof,
`terraform-plan.yml`) were confirmed pre-existing and unrelated via
cross-branch `gh run list` comparison and left alone.

## 2026-08-10 — Phase 1: Semantic Model wired into live ingestion

`navigraph_kg.ingestion.pipeline._sync_relationship_concepts` now reads a
tenant's activated `navigraph_semantic_model.contracts.SemanticModel` via a
new `_load_relationship_concepts` helper, falling back to the hardcoded
`ontology.RELATIONSHIP_CONCEPTS` list for any tenant that hasn't onboarded
one yet. Full rationale for the fallback-first design (vs. a hard cutover)
is in `DECISIONS.md`'s matching entry; `LIMITATIONS.md` item 105 has the
full what-changed/what-still-needs-a-live-run breakdown.

New: a `semantic_models` table in `metadata_catalog` (migration `0006`,
`SemanticModelRecord`, and `save_semantic_model`/`activate_semantic_model`/
`get_active_semantic_model`/`list_semantic_models` in `navigraph_catalog
.api`, mirroring `data_sources.is_default`'s partial-unique-index pattern
exactly), and a one-time `tools/scripts/seed_semantic_model_from_ontology.py`
to migrate `navikenz-poc`/`ecommerce-poc` onto a persisted model matching
today's ingestion output exactly (not yet run against a live database --
this session had no live Postgres access).

Fixed along the way: `onboard_data_source.py`'s `activate` command now
actually persists+activates the model it validates (it previously only
validated/tagged-PII/synced-OPA, never wiring into anything Phase 1's new
fallback logic could read); and `packages/agent_runtime/Dockerfile` plus all
three CI workflows that install this workspace from source now install
`semantic_model` before `knowledge_graph` (a new real dependency,
`knowledge_graph` -> `semantic_model`, that would otherwise fail resolving
from PyPI).

Verified for real: full `pytest packages/` (567 passed, 8 skipped, up from
564), `ruff check` clean, `mypy` clean (158 files) -- run locally in a clean
virtualenv against this change's actual code.

## 2026-08-10 — Phase 2: onboarding pipeline connected end-to-end, two real live bugs fixed along the way

New `navigraph_semantic_model.activation.activate_semantic_model`: the
real validate -> tag PII -> persist -> mark active -> sync OPA sequence,
defined once and used by both `onboard_data_source.py`'s (refactored)
`activate` command and a new `navigraph_admin.py semantic-model
compile-and-activate` command -- the plan's literal Phase 2 ask,
collapsing compile+activate into one step for the common case where a
reviewed draft was the only human edit point. Full rationale for the
shared-module design (vs. a second hand-copy) is in `DECISIONS.md`'s
matching entry; `LIMITATIONS.md` item 106 has the full breakdown.

Reading `onboard_data_source.py` closely (rather than trusting its own
"five subcommands, meant to be run in this order" docstring) surfaced two
real, live bugs, both fixed: `crawl` called a `build_connector` function
that never existed in `connector_sdk` at all (every real caller
constructs a connector via `get_connector_class(source_type)()` instead,
with no arguments); and even fixing that, a fresh CLI invocation would
still fail with "No connector registered" because connector registration
is an import side effect this script never triggered --
`navigraph_agents.main` had the identical bug, already found and fixed
there. `docs/runbooks/data-source-onboarding.md` updated to match reality
(the credential-routing prerequisites it described never existed; Phase
1's ingestion wiring and the new combined CLI command are both now
documented there too).

Verified for real: full `pytest packages/` (569 passed, 8 skipped, up
from 567), `ruff check` clean, `mypy` clean (161 files, including both
touched `tools/scripts/*.py` files, checked manually since that directory
is outside CI's own mypy scope). `compile_draft_to_semantic_model` run
end-to-end against a real `OntologyDraftingResult`-shaped draft; connector
registration confirmed generic across Snowflake/Postgres/Databricks in a
fresh process, both bugs above reproduced live before the fix and
confirmed gone after.
