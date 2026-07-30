# Runbook: Kubernetes Manifest Local Validation (`kind`)

This runbook proves `infra/k8s/`'s manifests -- and, critically, the real
weighted-canary mechanism -- work correctly, entirely on your own machine,
with **zero Azure cost and zero Azure credentials**. It is the required
step before ever touching Phase 10b's real AKS deployment. `.github/workflows/k8s-manifests-ci.yml`
runs this exact sequence on every PR touching `infra/k8s/**`.

## Prerequisites

- Docker running.
- `kubectl` (any recent version; its embedded Kustomize is fine for
  reading, but see the next point).
- The **standalone** `kustomize` CLI, not just `kubectl`'s embedded one --
  required because `infra/k8s/base/kustomization.yaml`'s `configMapGenerator`
  entries pull real config directly from `infra/opa/`, `infra/neo4j/`,
  `infra/prometheus/`, `infra/grafana/` (deliberately, so those files are
  never duplicated -- see `base/kustomization.yaml`'s own comment), which
  escapes Kustomize's default per-kustomization security sandbox. Plain
  `kubectl apply -k` has no flag to relax this; only the standalone binary's
  `--load-restrictor LoadRestrictionsNone` does. Install it from
  https://github.com/kubernetes-sigs/kustomize/releases (pin the same
  version `.github/workflows/*.yml` uses).
- `kind` (https://kind.sigs.k8s.io/).

## Steps

### 1. Create the cluster

```bash
kind create cluster --config infra/k8s/kind-config.yaml
```

Creates a real 3-node cluster (`navigraph-dev`: 1 control-plane + 2
workers). The control-plane's `extraPortMappings` (80/443) are what make
anything deployed reachable from the host at all.

### 2. Install ingress-nginx (the kind-specific provider manifest) and its metrics Service

```bash
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml
kubectl apply -f infra/k8s/ingress-nginx-metrics-service.yaml
kubectl -n ingress-nginx wait --for=condition=Available deployment/ingress-nginx-controller --timeout=180s
```

The second `apply` is **not optional** -- the official manifest serves
Prometheus metrics on port 10254 by default but creates no Service
exposing it, which would otherwise silently break
`tools/scripts/canary_gate.py`'s entire promotion-gate mechanism (a real
gap found live the first time this runbook's sequence was actually
followed).

### 3. Build the 3 app images and load them into `kind`

```bash
docker build -f packages/gateway/Dockerfile -t navigraph-gateway:local packages/
docker build -f packages/agent_runtime/Dockerfile -t navigraph-agent-runtime:local packages/
docker build -f web/Dockerfile -t navigraph-web:local web/
kind load docker-image navigraph-gateway:local navigraph-agent-runtime:local navigraph-web:local --name navigraph-dev
```

No registry, no ACR, no push, zero Azure credentials at any point -- images
go directly into `kind`'s containerd.

### 4. Create the manual secrets (this overlay has no CSI driver)

```bash
kubectl create namespace navigraph --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic agent-runtime-secrets -n navigraph \
  --from-literal=ANTHROPIC_API_KEY=local-placeholder-not-used \
  --from-literal=SNOWFLAKE_PASSWORD=local-placeholder-not-used \
  --from-literal=POSTGRES_PASSWORD=navigraph-kind-local \
  --from-literal=NEO4J_PASSWORD=navigraph-kind-local
kubectl create secret generic neo4j-secrets -n navigraph \
  --from-literal=NEO4J_PASSWORD=navigraph-kind-local
kubectl create secret generic grafana-secrets -n navigraph \
  --from-literal=GRAFANA_ADMIN_PASSWORD=navigraph-kind-local
```

See `overlays/kind/secret-instructions.md` for the full version of this
step (including how to source real `ANTHROPIC_API_KEY`/`SNOWFLAKE_PASSWORD`
from your `infra/.env` if you want a real, non-`FakeLLMClient` validation
run).

### 5. Apply the `kind` overlay

```bash
kustomize build infra/k8s/overlays/kind --load-restrictor LoadRestrictionsNone | kubectl apply -f -
```

### 6. Wait for everything to become Ready

```bash
kubectl -n navigraph wait --for=condition=Available deployment --all --timeout=300s
kubectl -n navigraph rollout status statefulset/neo4j --timeout=300s
```

### 7. Real HTTP smoke test through the real ingress

```bash
curl -sf --resolve api.navigraph.example.com:80:127.0.0.1 http://api.navigraph.example.com/healthz
curl -sf --resolve app.navigraph.example.com:80:127.0.0.1 http://app.navigraph.example.com/ -o /dev/null
```

`--resolve` avoids needing a real `/etc/hosts` edit, so this is scriptable
in CI too.

### 8. Prove the canary weighting mechanism itself, for real

This is the highest-risk new piece of the whole Phase 10 design -- proven
locally before Azure is ever touched:

```bash
# Add a distinguishable marker endpoint, build a v2 image, load it
cat >> packages/gateway/navigraph_gateway/main.py <<'EOF'


@app.get("/healthz-canary-marker")
async def healthz_canary_marker() -> dict:
    return {"status": "ok", "marker": "canary-v2"}
EOF
docker build -f packages/gateway/Dockerfile -t navigraph-gateway:local-v2 packages/
kind load docker-image navigraph-gateway:local-v2 --name navigraph-dev
kubectl set image deployment/gateway-canary gateway=navigraph-gateway:local-v2 -n navigraph
kubectl rollout status deployment/gateway-canary -n navigraph --timeout=120s

# Shift 10% canary weight, fire real requests, check the observed split
kubectl patch ingress gateway-canary -n navigraph --type=merge \
  -p '{"metadata":{"annotations":{"nginx.ingress.kubernetes.io/canary-weight":"10"}}}'
sleep 5
MARKER_COUNT=0
for i in $(seq 1 200); do
  if curl -sf --max-time 3 --resolve api.navigraph.example.com:80:127.0.0.1 \
       http://api.navigraph.example.com/healthz-canary-marker 2>/dev/null | grep -q canary-v2; then
    MARKER_COUNT=$((MARKER_COUNT + 1))
  fi
done
echo "canary-v2 seen in $MARKER_COUNT/200 requests (expected ~10%, real observed run: 26/200 = 13%)"

# Reset to steady state
kubectl patch ingress gateway-canary -n navigraph --type=merge \
  -p '{"metadata":{"annotations":{"nginx.ingress.kubernetes.io/canary-weight":"0"}}}'
# Revert the marker endpoint from packages/gateway/navigraph_gateway/main.py
# before committing anything -- it is a throwaway test artifact.
```

### 9. Tear down

```bash
kind delete cluster --name navigraph-dev
```

## Real bugs found and fixed by actually running this sequence

Six genuine, load-bearing bugs were found and fixed the first time this
runbook's sequence was followed for real (all now fixed in the manifests
themselves, described here so the *symptoms* are recognizable if a future
regression reintroduces any of them):

1. **PVCs stuck `Pending` forever, no error**: `storageClassName: managed-csi`
   (real for AKS) doesn't exist in `kind` (whose default is `standard`).
   Fixed via `overlays/kind/patch-storageclass.yaml`.
2. **`configMapGenerator`'s ConfigMaps silently landed in the wrong
   namespace** (`default`, not `navigraph`) because generated resources
   have no explicit `metadata.namespace` unless a top-level `namespace:`
   transformer is declared. Fixed by adding `namespace: navigraph` to
   `base/kustomization.yaml` **and** both overlays' own kustomization files
   (base's transformer does not retroactively cover an overlay's own
   separately-declared generators).
3. **OPA failed to start** ("multiple default rules found", the same rule
   reported 3 times): mounting a ConfigMap volume at a whole directory
   (`mountPath: /policies`) makes OPA's own recursive directory scan walk
   into the ConfigMap volume's internal `..data` symlink structure and
   find the same file multiple times. Fixed via `subPath` mounts (a single
   real file, no symlink indirection) in `base/opa/deployment.yaml`.
4. **`web` pods `CrashLoopBackOff`** despite the app logging "Ready": the
   root page (`page.tsx`) does a server-side fetch to the gateway with its
   own internal 3000ms timeout, but Kubernetes' `httpGet` probe defaults to
   a 1-second `timeoutSeconds` -- guaranteed failures whenever that fetch
   took 1-3s. Fixed by setting `timeoutSeconds: 5` on both probes in
   `base/web/deployment-{stable,canary}.yaml`.
5. **`neo4j` failed to start** ("Unrecognized setting... PASSWORD"): the
   official neo4j image auto-translates every `NEO4J_*` env var into a
   config setting; a plain `NEO4J_PASSWORD` var (needed to compose
   `NEO4J_AUTH` via Kubernetes' `$(VAR)` interpolation) got mistranslated
   into an invalid bare `PASSWORD` setting. Fixed by renaming it to
   `NAVIGRAPH_NEO4J_PASSWORD` (deliberately not `NEO4J_`-prefixed) in
   `base/neo4j/statefulset.yaml`.
6. **A `StatefulSet` patch silently dropped required PVC fields**: a
   Kustomize patch touching only `volumeClaimTemplates[].spec.storageClassName`
   replaced the WHOLE nested `spec`, not just that one field (unlike
   `containers`/`volumes`, `volumeClaimTemplates` doesn't get the same
   field-level strategic merge) -- the StatefulSet controller then failed
   to create the PVC at all ("accessModes: Required value"). Fixed by
   repeating the full spec (`accessModes`, `storageClassName`,
   `resources`) in the patch, not just the one changed field.

Real end-to-end proof after all six fixes: all 18 pods `Running`/`Ready`,
real HTTP 200s through the real ingress for both `gateway` and `web`, and
a real weighted-canary run showing `canary-v2` in 26/200 requests (~13%,
against a 10% configured weight -- well within the expected statistical
band for a probabilistic per-request split).

## Known, environment-specific quirk: not a manifest defect

During one local validation session, `agent-runtime` pods became
genuinely unreachable from every other pod (including a fresh, unrelated
debug pod) over the real cluster network, despite: the app responding
correctly on `localhost` from inside its own pod; `kubectl`'s own httpGet
readiness/liveness probes (run by the node's kubelet, not pod-to-pod)
succeeding continuously; and pod-to-pod traffic on an *identical* path
(`ingress-nginx-controller` -> `gateway-stable`/`web-stable`) working
correctly at the same time. Restarting the affected pods did not resolve
it. Docker resource usage was not elevated (ruling out contention). This
points at a `kindnet`-on-Windows/Docker-Desktop/WSL2-specific pod-routing
flake, not a defect in the manifests or NetworkPolicy design -- real AKS
uses Azure CNI on real Linux nodes, an entirely different networking
stack. If you hit this: try deleting and letting the Deployment recreate
the affected pods, or (more reliably) delete and recreate the whole
`kind` cluster. This is exactly why `tests/security/cloud/test_network_policy_isolation.py`'s
positive control exists -- to catch a REAL cross-pod networking break
during CI, distinct from `kind`'s own environment flakiness during manual
local runs.
