# Runbook: Local Dev Smoke Test

This runbook walks through bringing up the full local NaviGraph stack and
confirming it's healthy end-to-end. Follow it top to bottom on a fresh
checkout.

## Prerequisites

- Docker (with Compose v2) installed and running.
- `ANTHROPIC_API_KEY` for the agent runtime to call Claude.
- An Azure AD (Entra ID) app registration for local dev OAuth/OIDC (see
  `terraform/modules/entra-app-registration` for the shape of this
  registration; local dev still creates this manually in the Azure portal
  rather than via Terraform's `entra-app-registration` module output, even
  though Terraform has been applied for real elsewhere in the project as of
  Phase 10b — see `terraform/README.md`).

## Steps

### 1. Copy the env template

```bash
cp infra/.env.example infra/.env
```

### 2. Fill in required values in `infra/.env`

At minimum for a full smoke test:

- `ANTHROPIC_API_KEY` — your Anthropic API key.
- `AZURE_AD_TENANT_ID`, `AZURE_AD_CLIENT_ID`, `AZURE_AD_CLIENT_SECRET` — from
  your Azure AD dev app registration.

The stack's infra services (postgres, neo4j, redis, observability, OPA,
Trino) will boot fine with these left blank — they're only needed once you
exercise a real end-to-end `/ask` request through the gateway. Snowflake
values (`SNOWFLAKE_*`) can stay blank if you only want the stack to boot and
answer via mocked/deterministic paths; a real Snowflake catalog is
registered in Trino (`LIMITATIONS.md` item 3 is RESOLVED) and the default
execution route (`route="direct_connector"`) genuinely executes against
Snowflake, so a real end-to-end `/ask` request that reaches
`query.data_federation` requires real `SNOWFLAKE_*` credentials.

### 3. Bring up the stack

```bash
docker compose -f infra/docker-compose.yml up -d
```

### 4. Wait for all services to report healthy

```bash
docker compose -f infra/docker-compose.yml ps
```

Every service should show `healthy` (not just `running`). This can take a
minute or two on first boot — see Troubleshooting below if a specific service
is slow or stuck.

### 5. Run the smoke test script

```bash
tools/scripts/smoke-test.sh
```

### Expected output

**Corrected 2026-08-09** — this section previously described checks
(`postgres`/`neo4j`/`redis`/`web` reachability, `/readyz` probes, a
`POST /ask` round-trip) that `tools/scripts/smoke-test.sh` has never
actually performed; it was rewritten to match the real script rather than
the script being changed to match this aspirational description.

The script curls each of the following and fails loudly (non-zero exit) if
any doesn't return a 2xx: `gateway` `/healthz`, `agent-runtime` `/healthz`,
`grafana` `/api/health`, `prometheus` `/-/healthy`, `opa` `/health`,
`otel-collector`'s root path, and `trino-coordinator` `/v1/info`. It does
not check `postgres`, `neo4j`, `redis`, or `web` directly, does not call
`/readyz` on any service, and does not exercise `POST /ask` — a real
end-to-end question still needs to be sent manually to confirm the full
pipeline actually answers, e.g.:

```bash
curl -s http://localhost:8000/ask \
  -H 'Content-Type: application/json' \
  -d '{"question": "What was our churn rate by region last quarter?",
       "tenant_id": "navikenz-poc", "user_id": "local-dev", "roles": ["analyst"]}'
```

A clean smoke-test run prints
`==> smoke test PASSED: all 7 endpoints returned 2xx.` and exits 0.

## Troubleshooting

### Neo4j is slow on first boot

Neo4j can take 30-60+ seconds on a fresh volume to initialize before its
healthcheck starts passing. This is normal — give it time before assuming it's
stuck. Check its logs with `docker compose -f infra/docker-compose.yml logs neo4j`
if it hasn't gone healthy after ~2 minutes.

### Trino worker not joining the coordinator

If `trino-worker` is up but queries against `trino-coordinator` show zero
active workers (check `http://localhost:8080/ui` or `/v1/info`), confirm both
containers resolved `discovery.uri` to the coordinator's service name
(`trino-coordinator`, not `localhost`) inside `infra/trino/worker/config.properties`,
and that both containers are on the `navigraph-net` network. Restarting the
worker after the coordinator is fully up often resolves a race on first boot.

### Postgres connections from the host fail with "password authentication failed"

If a host-side tool (Alembic, a local Python script, a GUI client) gets a
password-auth error connecting to Postgres even though you're sure the
credentials in `infra/.env` are right, check whether something *else* on
your machine is already listening on port 5432
(`Get-NetTCPConnection -LocalPort 5432` on Windows) — a stray native
Postgres install silently intercepts the connection instead of Docker's
forwarded port, and rejects it with a *password* error rather than
"connection refused," which is very misleading. This project's compose
file maps the container to host port **5433** specifically to avoid this
(`postgres:5432` from *inside* the docker network is unaffected either
way) — connect host-side tools to `localhost:5433`, not `5432`.

### OPA bundle not loading

This local setup runs OPA in bundle-less mode, reading policy files directly
from the mounted `infra/opa/policies/` directory (see
`infra/opa/conf/config.yaml`). If OPA logs show it can't find
`placeholder.rego`, confirm the volume mount path in
`infra/docker-compose.yml` matches the path referenced in `config.yaml`, and
that the file has valid Rego syntax (`package navigraph.authz` at minimum).

### Gateway or agent-runtime never go healthy

These images are built from `packages/gateway` and `packages/agent_runtime`
respectively, which are owned by a parallel workstream. If they fail to build
or start, confirm those directories exist and contain a working `Dockerfile`
exposing `/healthz` and `/readyz` on the expected ports (8000 and 8001) — this
infra scaffold references those paths but does not create them.
