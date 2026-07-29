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
