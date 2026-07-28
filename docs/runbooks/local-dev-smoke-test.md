# Runbook: Local Dev Smoke Test

This runbook walks through bringing up the full local NaviGraph stack and
confirming it's healthy end-to-end. Follow it top to bottom on a fresh
checkout.

## Prerequisites

- Docker (with Compose v2) installed and running.
- `ANTHROPIC_API_KEY` for the agent runtime to call Claude.
- An Azure AD (Entra ID) app registration for local dev OAuth/OIDC (see
  `terraform/modules/entra-app-registration` for the intended shape of this
  registration — in local dev today you create this manually in the Azure
  portal, since Terraform is never applied against a real subscription; see
  `terraform/README.md`).

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
values (`SNOWFLAKE_*`) can stay blank for now; no Trino catalog is registered
yet (see `LIMITATIONS.md` item 3), so there is nothing for them to connect to.

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

The smoke test script is expected to check, in order: that `postgres`,
`neo4j`, `redis`, `opa`, `trino-coordinator`, `agent-runtime`, `gateway`, and
`web` are all reachable and reporting healthy; that `agent-runtime`'s
`/healthz` and `/readyz` both return 200; that `gateway`'s `/healthz` and
`/readyz` both return 200; and that a basic `POST /ask` round-trip against the
gateway returns a well-formed response (exercising the one real agent, Intent
Understanding, end-to-end). A clean run prints a final `SMOKE TEST PASSED`
line and exits 0.

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
