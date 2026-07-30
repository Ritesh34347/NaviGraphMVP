# Secrets for the `kind` overlay

No Azure Key Vault CSI driver exists in a local `kind` cluster -- this
overlay needs one real Kubernetes `Secret` per service, created manually
before `kubectl apply -k infra/k8s/overlays/kind`, exactly mirroring how
`infra/.env.example` -> `infra/.env` already has to exist before
`docker compose up` for local dev.

```bash
kubectl create namespace navigraph --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic agent-runtime-secrets -n navigraph \
  --from-literal=ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
  --from-literal=SNOWFLAKE_PASSWORD="$SNOWFLAKE_PASSWORD" \
  --from-literal=POSTGRES_PASSWORD=navigraph-kind-local \
  --from-literal=NEO4J_PASSWORD=navigraph-kind-local

kubectl create secret generic neo4j-secrets -n navigraph \
  --from-literal=NEO4J_PASSWORD=navigraph-kind-local

kubectl create secret generic grafana-secrets -n navigraph \
  --from-literal=GRAFANA_ADMIN_PASSWORD=navigraph-kind-local
```

`ANTHROPIC_API_KEY`/`SNOWFLAKE_PASSWORD` should be read from your real,
gitignored `infra/.env` (`export $(grep -E '^(ANTHROPIC_API_KEY|SNOWFLAKE_PASSWORD)=' infra/.env | xargs)`
before running the block above) if you want a real, non-`FakeLLMClient`
`kind` validation run -- otherwise these two values can be any non-empty
placeholder string, since `k8s-manifests-ci.yml`'s own validation run
never exercises real LLM/Snowflake calls (it only proves the manifests
apply, pods become Ready, and the canary weighting mechanism itself
works -- see that workflow and `docs/runbooks/k8s-local-validation.md`).

`neo4j-secrets`/`grafana-secrets`'s values above (`navigraph-kind-local`)
match `overlays/kind/postgres/deployment.yaml`'s own hardcoded
`POSTGRES_PASSWORD` -- all three are throwaway values for a disposable
local cluster, never real credentials.
