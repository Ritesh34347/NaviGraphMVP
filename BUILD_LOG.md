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

## 2026-07-30 — Phase 10b continued: real AKS cluster bootstrap, two real security incidents found and fixed

**Backfilled 2026-08-09**: this entry documents work that was done in the
Phase 10b session above but was never given its own `BUILD_LOG.md` entry at
the time -- found only because `LIMITATIONS.md` items 54-58 already
documented it in detail while this file still said cluster bootstrap
"[had] not started." Adding it now so this log matches what actually
happened, per the same discipline that motivated the broader 2026-08-09
docs-reconciliation entry below.

Real ingress-nginx and cert-manager were deployed to the real Phase 10b AKS
cluster, with real, live-discovered fixes along the way (all detail in
`LIMITATIONS.md`): a Kustomize `ingress-patch.yaml` strategic-merge bug that
silently deleted every Ingress's backend (item 58); missing
`securityContext.fsGroup` on Grafana/Prometheus, which crashed on real
Azure Disk-backed PVCs in a way `kind`'s local-path storage class never
surfaced (item 57); AKS having no ACR pull access even though the CI
principal had push access (item 56); and Key Vault silently ignoring its
own RBAC role assignments because `enable_rbac_authorization` was never set
(item 55). Real, live proof after every fix: all pods `Running`/`Ready`,
real HTTP 200s through the real ingress controller, and a real weighted-canary
run confirming NGINX's canary-weight annotation actually performs
proportional traffic splitting (~13% observed against a configured 10%
weight).

Two real security incidents were found and remediated during this work, not
just functional bugs: `terraform output -json` briefly printed a real,
cluster-admin-equivalent AKS kubeconfig credential to this session (item
54) -- resolved by deleting the file it was written to and rotating the
cluster's certs via `az aks rotate-certs`; and the Key Vault RBAC gap above
(item 55) meant a real role assignment had been silently granting nothing
since its creation.

## 2026-08-09 — Documentation reconciliation pass (LIMITATIONS.md items 7, 32, 35 resolved)

A dedicated pass, not bundled into any feature phase, to close the
long-deferred documentation-staleness gap `LIMITATIONS.md` item 32 first
flagged. Verified the real current state directly against code
(`registry.py`, `main.py`'s `lifespan()`, `request_orchestrator/agent.py`)
rather than trusting any existing doc, then corrected every place that
still described the pre-Phase-9 world:

- Added `docs/architecture/single-stage-mvp.md` as the authoritative,
  verified description of the real 19-agent Request Orchestrator sequence,
  its outcome/failure model, and real-vs-stubbed infrastructure.
- Rewrote `docs/architecture/overview.md`'s per-domain agent tables: all 25
  real agents, with explicit notes on which originally-planned agents were
  renamed (e.g. "Semantic Catalog Retrieval" → `understanding.semantic_retrieval`),
  consolidated (e.g. "Conversation Context Tracker" → `understanding.conversation`
  + `orchestrator.session_context_manager`), shipped under a different
  domain than planned (Federated Query Executor/Result Caching shipped
  under Query, not Ops), or never built as a standalone agent at all
  (Entity Resolution, Ambiguity Detection, Cypher Generation, Metric
  Definition Resolver, Query Plan Composer, Error/Retry Handler).
- Rewrote `docs/architecture/data-flow.md`'s narrative to match, and
  corrected a deeper inaccuracy found along the way: its described
  per-phase lineage event names (`intent_extracted`, `query_generated`,
  etc.) don't exist in the real code -- every real `LineageEvent.agent_name`
  is that agent's own registry key, and there is no gateway-level
  `request_received` event.
- Fixed the one still-stale module docstring
  (`navigraph_agents/__init__.py`); found `navigraph_gateway/main.py`'s
  docstring (also flagged stale by item 32) was already accurate,
  apparently corrected independently at some earlier, undocumented point.
- Found and fixed staleness item 32 never mentioned: `README.md`'s Status
  section and `terraform/README.md` both still claimed Terraform "has never
  been applied," directly contradicted by Phase 10b's real `apply` (closing
  out item 5, also marked RESOLVED here); `README.md`'s repository-layout
  table still described `packages/`/`web/` as built by an external,
  parallel workstream "not part of this scaffold"; and
  `docs/runbooks/local-dev-smoke-test.md`'s "Expected output" section
  described `postgres`/`neo4j`/`redis`/`web` checks, `/readyz` probes, and a
  `POST /ask` round-trip that `tools/scripts/smoke-test.sh` has never
  actually performed -- corrected to match the real script.
- Logged one new, real finding surfaced only by reading the orchestrator
  code line-by-line: `query.caching` is fully built and registered but
  never called by the live Request Orchestrator sequence (new item 59).
- Backfilled the missing Phase 10b cluster-bootstrap `BUILD_LOG.md` entry
  above, found only because `LIMITATIONS.md` items 54-58 already documented
  work this file never recorded.

Deliberately not touched: `DECISIONS.md` (dated ADRs describing the
reasoning live at the time a decision was made, not living status) and
`BUILD_LOG.md`'s own prior entries (a factual, dated log; corrected by
backfilling a missing entry above, not by rewriting history).

## 2026-08-09 — Real fix: `query.caching` wired into the live Request Orchestrator sequence (LIMITATIONS.md item 59)

While scoping a broader pass to address open functional gaps, found that
`RequestOrchestratorAgent` never actually called `CachingAgent` despite it
being fully built, tested, and registered since Phase 5 (see the docs-
reconciliation entry above, item 59). Fixed for real:
`RequestOrchestratorAgent` now constructs a `CachingAgent` (sharing the
real `cache_client` already passed in for `SessionContextManagerAgent`)
and calls it around Data Federation -- a real Redis lookup keyed on
`(tenant_id, sql, params, data_source_id)` immediately before Data
Federation would run, skipping Data Federation entirely on a hit; a real
store of the executed result immediately after a successful miss-path
execution. A cache-backend failure on either operation is recoverable and
behaves exactly like a real miss, per `CachingAgent`'s own pre-existing
error contract -- it never blocks the pipeline.

Four new unit tests added to
`orchestrator/request_orchestrator/tests/test_agent.py`: a real cache hit
skips Data Federation entirely; a real cache miss calls Data Federation
then stores its exact result; a recoverable cache-backend error on lookup
still lets the request answer, with the error recorded; and the existing
happy-path test was updated to wire a (miss-returning) Caching mock like
every other real sub-agent. All 204 tests in `packages/agent_runtime/`
pass (1 pre-existing, unrelated collection error in
`tests/test_healthz.py` due to `fastapi` not being installed in this
verification environment -- not something this change touches); `ruff
check` is clean on every changed file.

Also fixed a real documentation regression introduced by the same day's
earlier docs-reconciliation pass: `LIMITATIONS.md` item 4 claimed OPA
"runs an allow-all placeholder policy," but item 18 already documented,
in full, that Phase 6 replaced it with a real deny-by-default RBAC +
tenant-ABAC Rego policy (`infra/opa/policies/authz.rego`), hardened via
`tests/security/`'s real adversarial suite -- item 4 was an unmarked
duplicate left behind at Phase 6 and never resolved, and the earlier
reconciliation pass trusted its text at face value instead of checking
the actual policy file, re-propagating the stale claim into `README.md`,
`overview.md`, `data-flow.md`, and `single-stage-mvp.md`. All corrected
to describe the real, narrower remaining gap: no row-/column-level ABAC
beyond PII, and no real Azure AD JWT verification behind the claims OPA
evaluates (item 23).

Every architecture doc's "19-agent"/"19-call" framing was updated to "20"
(or "the real agent sequence," where a specific count wasn't load-bearing)
to reflect Caching's addition to the live pipeline, without rewriting the
historical "this exact 19-call sequence" language in `agent.py`'s module
docstring describing `run_full_pipeline`'s original, real Phase 8 proof --
that statement was and remains true about what it originally described.

**Deliberately not attempted in this pass** (flagged, not fixed, pending
explicit scoping): a second real connector (item 1), real Azure AD JWT
verification (item 23), promoting Trino to the default execution route
(item 3 -- gated by an explicit `DECISIONS.md` condition, not something to
flip unilaterally), and mid-pipeline crash recovery (item 39 -- would mean
reversing the Phase 9 LangGraph-removal decision). See this session's
chat-turn plan for the full reasoning on what's in/out of scope and why.

## 2026-08-09 — Real second connector: Postgres (LIMITATIONS.md item 1, partially resolved)

Built `navigraph_connectors.postgres.PostgresConnector`, a complete,
real `Connector` implementation mirroring `SnowflakeConnector`'s exact
structure (`settings.py`, `connector.py`, `__init__.py` registering
`"postgres"` in the connector registry). Verified live, not just
cross-checked against docs: started a real local Postgres 16 instance,
created a real `navigraph_customer_sample` database with a `sales` schema
(`customers`/`orders` tables, a column comment, a nullable column, and
seed rows), and ran both `introspect_schema()` and `execute_query()` --
including with real `%(name)s`-style bind parameters, the exact paramstyle
`query.sql_generation` hardcodes because it matches Snowflake's driver --
against it for real. Both the new `postgres_integration`-marked test
(mirroring `snowflake_integration`'s pattern exactly, skipping cleanly
without real `CUSTOMER_POSTGRES_*` env vars) and the fake-backed default
unit tests pass; `ruff check` is clean.

Real finding from that live verification: `psycopg2` natively accepts
`%(name)s` pyformat bind parameters, so SQL Generation's output runs
against this connector with zero dialect translation -- confirmed, not
assumed, closing the specific "pressure-test the abstraction against a
second, differently-shaped source" gap item 1 named.

Settings use a `CUSTOMER_POSTGRES_*` env-var prefix, not `POSTGRES_*` --
see `DECISIONS.md` for why (those exact names are already claimed by
NaviGraph's own internal catalog database's settings, and reusing them
would have silently pointed customer query execution at NaviGraph's own
operational Postgres instance in any process that constructs both, which
`agent_runtime`'s `main.py` does). `navigraph_connectors.postgres` is now
imported for its registration side effect in `main.py`, alongside the
pre-existing `navigraph_connectors.snowflake` import.

Also added `infra/trino/coordinator/catalog/postgresql.properties.example`,
mirroring the pre-existing `snowflake.properties.example` pattern, to
document (not yet wire for real) what Trino-side Postgres catalog
registration would look like.

**Deliberately not done in this same pass**: promoting Trino to the
default execution route, even though a second real connector was one of
the two stated conditions for reconsidering that default. See
`DECISIONS.md`'s "Promoting Trino to the default execution route is
deferred" entry and `LIMITATIONS.md` item 3's 2026-08-09 update for the
full reasoning -- no Trino-side Postgres catalog exists yet, and flipping
the default now would only change how the one real, live production data
source (Snowflake) executes, with no live-verified cross-source path to
justify it. Also not done: registering a real `postgres`-type `DataSource`
row for any tenant, or a metadata-catalog crawl against this connector
(item 1's remaining "still open" list) -- this pass proved the connector
itself is real and correct, not that it's wired into a live tenant's
request pipeline yet.

## 2026-08-09 — Phase 11 part 1: per-`DataSource` credential routing + a real default-`DataSource` concept (LIMITATIONS.md items 21, 26, 42)

Built a real `navigraph_shared.secrets.SecretsProvider` abstraction
(`EnvVarSecretsProvider`, `AzureKeyVaultSecretsProvider`, and a
`FakeSecretsProvider` for tests) and extended
`navigraph_connectors.registry` with a settings-factory mechanism
(`register_connector(..., settings_factory=...)`, a new
`build_connector(source_type, *, connection_ref, secrets)`) so both real
connectors resolve their full settings from a `DataSource.connection_ref`
+ `SecretsProvider`, keyed by a per-row `secret_scope`, instead of reading
process env vars directly (`navigraph_connectors.snowflake.settings_factory
.build_snowflake_settings`, `navigraph_connectors.postgres.settings_factory
.build_postgres_settings`). `DataSourceDiscoveryAgent`, `DataFederationAgent`,
and `RequestOrchestratorAgent` were all updated to accept and forward a
`secrets: SecretsProvider`; `main.py`'s `lifespan()` now wires a real
`AzureKeyVaultSecretsProvider` when `SECRETS_KEY_VAULT_URL` is set, falling
back to `EnvVarSecretsProvider` (with a logged warning) otherwise. This
closes item 21: two `DataSource` rows sharing the same `source_type` now
genuinely resolve to distinct credentials -- proven with unit tests
including an adversarial assertion that an unrelated global env var never
leaks into a scoped lookup.

Also added `DataSource.is_default` (migration `0004_data_source_is_default`)
with a partial unique index (`uq_data_sources_tenant_default`) enforcing
"at most one default per tenant" at the DB level, plus
`navigraph_catalog.api.get_default_data_source`/`set_default_data_source`
(an atomic unset-then-set swap, since a plain single `UPDATE` would
transiently violate the index). `RequestOrchestratorAgent
._resolve_data_source_id` now falls back to a tenant's marked default when
more than one `DataSource` is registered, instead of unconditionally
failing -- closing item 42. Verified for real, not just at the model/API
layer: stood up a fresh local Postgres role/database, ran
`alembic upgrade head` for real, and proved the partial unique index
genuinely raises `IntegrityError` on a second `is_default=true` row per
tenant and that `set_default_data_source`'s swap genuinely works, before
tearing the test database down (`tests/integration/metadata_catalog
/test_migrations.py`, new `postgres_integration`-marked test). New
orchestrator unit tests cover both the resolved-to-default path (two
data sources, one marked default, `data_source_id` omitted -> answers) and
the still-ambiguous path (two data sources, neither marked default ->
still fails, unchanged). Full suite across `agent_runtime`,
`metadata_catalog`, `connector_sdk`, and `shared` (298 tests, DB/network-free
tier) passes with zero regressions; `ruff check` is clean on every changed
file.

**Item 26 is only partially resolved by this pass**: the mechanism to
designate a default now exists, but nobody has actually called
`set_default_data_source` against the real, live `navikenz-poc` catalog to
pick `fidelity_poc_snowflake` or `_v2` as canonical -- that is a real
business decision for whoever owns that tenant's data, not something this
pass makes unilaterally. See `LIMITATIONS.md` items 21, 26, 42 for the full
detail, including what's still open on each.

**Deliberately not attempted in this pass**: real Azure AD JWT verification
(item 23) -- security-critical code that needs its own careful pass with
real cryptographic tests, not bundled into a credential-routing change.
Also not attempted: verifying `AzureKeyVaultSecretsProvider` against the
real, live `navigraph-dev-kv` Key Vault from Phase 10b -- this sandbox has
no live Azure credentials to do so.

## 2026-08-09 — Phase 11 part 1 follow-up: real navikenz-poc default decision + DataSourceDiscoveryAgent's own tie-break fixed

Item 26's real navikenz-poc ambiguity has two independent resolution
points, not one: the Request Orchestrator's `_resolve_data_source_id`
(item 42, resolved above) and `DataSourceDiscoveryAgent
._resolve_table_owners`'s own "first data source encountered wins"
tie-break -- the specific mechanism that made `STAGING_TRANSACTIONS`
resolve to the older `fidelity_poc_snowflake` registration in earlier
phases. Fixed the second one too: `_resolve_table_owners` now sorts
data sources so a tenant's marked `is_default` source is processed first,
winning any table-name collision on its own merit -- proven with a new
unit test that deliberately returns the non-default source first from
`list_data_sources`. `agent_runtime`'s suite is now at 207 passing tests
(was 204 before Phase 11 part 1, +3 for this fix), `ruff check` clean.

Also resolved the actual business question item 26 left open: asked the
user directly (`AskUserQuestion`) which of the two real navikenz-poc
registrations should be canonical. They chose `fidelity_poc_snowflake_v2`
-- a real behavior change from what the pipeline resolved to before this
pass, not the no-op "keep the status quo" option. Recorded in
`DECISIONS.md` and `LIMITATIONS.md` item 26. **Not yet applied**: this
sandbox has no connectivity to the live `navikenz-poc` metadata catalog
(no docker-compose stack, no Azure credentials), so the one-time
`set_default_data_source(tenant_id="navikenz-poc", data_source_id=...)`
call against that live system, and re-running the golden-question eval
suite afterward to confirm the switch doesn't regress anything, are both
real follow-up steps for whoever has that access -- not done here.

## 2026-08-09 — Phase 11 part 2: real Azure AD JWT verification (LIMITATIONS.md item 23)

Built the dedicated, careful pass the previous two entries in this log
explicitly deferred to. New `navigraph_shared.auth` package (mirroring
`navigraph_shared.opa`/`.secrets`'s exact ABC/real/fake triad):
`TokenVerifier` (ABC), `AzureAdTokenVerifier` (real -- PyJWT-based RS256
signature verification against a real JWKS endpoint, plus issuer/audience/
expiry checks, `algorithms=["RS256"]` passed explicitly as an allowlist),
`FakeTokenVerifier` (no-crypto test double). `packages/gateway
/navigraph_gateway/main.py`'s `/ask` endpoint now requires a real
`Authorization: Bearer <token>` header and builds `RequestContext.user_id`/
`roles`/`claims` from the verified token -- entirely replacing, never
merging with, whatever the caller also puts in the request body -- when
`AZURE_AD_TENANT_ID`/`AZURE_AD_AUDIENCE` are both configured; falls back to
the original caller-supplied trust model (loudly logged) otherwise, same
pattern as `_build_secrets_provider`'s item-21 fallback.

Verified with real cryptography, not mocks: `packages/shared/tests
/test_auth_client.py` (19 tests) generates a real RSA keypair, signs real
JWTs with PyJWT, and builds a real JWKS document -- only the actual
network fetch is replaced (a `jwt.PyJWKClient` subclass overriding just
`fetch_data()`), so signature verification, `kid` matching, and
key-rotation refresh-on-miss are all real, unmodified PyJWT/`cryptography`
code paths. Proves two classic JWT forgery attacks are actually defeated,
not just assumed defended against by construction: an `alg: none`
(unsigned) token, and an HS256 token hand-forged with raw `hmac` (not
`jwt.encode()`, which refuses this outright) using the RSA **public**
key's PEM bytes as the HMAC secret -- the classic "algorithm confusion"
attack a verifier that trusted the token's own header `alg` claim would
fall for. Also: expired tokens, wrong audience, wrong issuer, a
missing-but-required `exp` claim, a signature forged with a different
keypair, and a single-byte payload tampering are all proven rejected.

`fastapi` (and `redis`, `prometheus-fastapi-instrumentator`,
`opentelemetry-instrumentation-fastapi`) were not installed in this
sandbox by default -- installed them for real this pass specifically so
`packages/gateway/tests/test_ask.py` (5 new tests, using a real FastAPI
`TestClient`) and the full `agent_runtime`/`gateway` suites could actually
run rather than being written untested. `test_ask.py` proves the real
security property this feature exists for: a caller presenting a valid
verified token while ALSO self-declaring `roles=["admin"]` and a different
`tenant_id` claim in the request body gets the verified identity forwarded
to agent-runtime, not the self-declared one. Full repo-wide suite (with
those four packages now installed) is 320 passed, 7 skipped, zero
regressions; `ruff check` clean across all of `packages/`.

**Deliberately left open, named rather than worked around**: no live Entra
tenant exists anywhere this project runs, so none of the above has ever
verified a real, Microsoft-issued token end-to-end -- everything is
verified against locally-generated, real cryptography instead. Separately,
NaviGraph's own business `tenant_id` has no established mapping to an
Azure AD tenant ID or claim; OPA's existing `claims.tenant_id ==
input.tenant_id` check will fail closed (safely, not insecurely) against
any real verified token until a real Entra app registration is configured
to emit a matching claim -- a deployment-time decision, not a code gap.
See `LIMITATIONS.md` item 23's full "still open" section.
