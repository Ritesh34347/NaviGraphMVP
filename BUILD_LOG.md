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
