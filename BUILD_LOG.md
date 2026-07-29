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
