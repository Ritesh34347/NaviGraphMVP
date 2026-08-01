# Operations Runbook

Real, production operations reference for the live AKS deployment. For
first-time local setup, see
[`local-dev-smoke-test.md`](./local-dev-smoke-test.md); for validating
`infra/k8s/` changes before they reach production, see
[`k8s-local-validation.md`](./k8s-local-validation.md). This document
covers the live environment itself.

## What's monitored today

- **Prometheus** scrapes: `gateway` (`:8000/metrics`), `agent-runtime`
  (`:8001/metrics`), `otel-collector` (`:8889`), and the
  `ingress-nginx-controller`'s own metrics (`:10254`) — see
  `infra/k8s/base/networkpolicy-allow.yaml`'s `allow-prometheus-scrape`
  policy for the exact real allow-list.
- **Grafana** reads from Prometheus; dashboards provisioned from
  `infra/grafana/dashboards/**` (reused via Kustomize's
  `configMapGenerator`, not duplicated into the k8s manifests).
- **OTel Collector** receives traces from both `gateway` and
  `agent-runtime` (every real request gets a span; `tracer.start_as_current_span`
  is called by every agent).
- **The canary gate itself** (`tools/scripts/canary_gate.py`) polls the
  real `nginx_ingress_controller_requests`/`_request_duration_seconds`
  metrics during every rollout — this is the one automated check that
  runs on every real deploy, not just when someone opens Grafana.

## Canary promotion / rollback procedure

Normal path is fully automated by `cd-deploy.yml` (see
`system-architecture.md`'s sequence diagram) — build → deploy canary at
0% → bake at 10%/50%/100% (each gated on 5xx rate, relative error rate,
and p95 latency vs. stable) → promote. No manual steps needed for a
clean rollout.

**Manual rollback**: `cd-deploy.yml` supports a `workflow_dispatch` input
(`action: rollback`) — an independent, immediate escape hatch to any
prior SHA still present in ACR, usable even if the automated gate
mis-judged a real issue.

**If a `promote` job fails with a git conflict** (has happened twice —
see `LIMITATIONS.md` items 72/76): first confirm the *live cluster* is
unaffected — `kubectl get deployment gateway-stable web-stable
agent-runtime -n navigraph -o jsonpath='{range .items[*]}{.metadata.name}{"
-> "}{.spec.template.spec.containers[0].image}{"\n"}{end}'` — the actual
`kubectl set image`/rollout steps run *before* the git bot-commit step,
so a git-only failure never means a bad deploy. Then manually correct
`infra/k8s/overlays/dev/kustomization.yaml`'s `newTag` fields on `main`
to match the confirmed-live image tag, commit, and push.

## Known real failure modes (from `LIMITATIONS.md`'s actual incident log)

Each of these happened for real, was root-caused with real evidence, and
is fixed — kept here as a playbook for if the same symptom reappears.

### "gateway is unreachable" / 502 from a real `/ask` call

**Symptom**: `HTTP 502 {"detail":"agent-runtime is unavailable or
returned an error"}`.

**Likely cause (already fixed once, verify it hasn't regressed)**:
gateway's own `httpx` timeout or ingress's `proxy-read-timeout` shorter
than the real pipeline's latency (up to ~90s for a complex question).
Check `packages/gateway/navigraph_gateway/main.py`'s
`httpx.AsyncClient(timeout=...)` and both `gateway`/`gateway-canary`
Ingress objects' `proxy-read-timeout`/`proxy-send-timeout` annotations —
both must agree (`LIMITATIONS.md` item 75).

### TLS certificates stuck `Ready: False`

**Symptom**: `kubectl get certificate -n navigraph` shows `gateway-tls`/
`web-tls` not ready; `kubectl describe` shows "Waiting for HTTP-01
challenge propagation."

**Likely cause**: cert-manager's dynamically-created HTTP-01 solver pods
(labeled `acme.cert-manager.io/http01-solver: "true"`, fixed internal
port 8089) not covered by a `NetworkPolicy` allow-rule under
`default-deny-all`. Check
`infra/k8s/base/networkpolicy-allow.yaml`'s `allow-ingress-nginx-to-acme-solver`
policy exists and matches (`LIMITATIONS.md` item 74). Diagnose by a
direct in-cluster `curl` to the solver Service's ClusterIP directly
(bypasses ingress-nginx) — if that also hangs, it's the NetworkPolicy,
not ingress routing.

### A new pod type is silently unreachable

**Symptom**: a newly-introduced pod (static or dynamically created, like
cert-manager's solvers) can't be reached by anything, with no error
beyond a timeout.

**Likely cause**: `default-deny-all`'s empty `podSelector: {}` denies
*any* pod with no explicit allow-rule — this is the single most-repeated
real bug class this project has hit (3 separate real instances: the
gateway→agent-runtime ingress-only gap missing its egress half, external
Postgres egress silently uncovered, and cert-manager's HTTP-01 solver
pods). Every new pod type needs its own explicit NetworkPolicy
allow-rule; there is no implicit "internal traffic is fine" default.

### Anthropic API calls failing with a usage-limit error

**Symptom**: real agent responses come back with
`errors: [{"code": "llm_call_failed", "message": "...You have reached
your specified API usage limits. You will regain access on <date> at
00:00 UTC..."}]`.

**Cause**: the Anthropic account's own real spend/usage cap, not a bug —
confirmed live (2026-08-01) during this project's own debugging. Every
LLM-backed agent already degrades gracefully on this (empty/failed
completion → recoverable fallback, never a crash), so the platform stays
up; individual questions just can't be answered until the cap resets.
No code fix applies here — check the account's real usage dashboard and
wait for the stated reset time.

### `promote` job's git push rejected/conflicted

See "Manual rollback" section above — this is a git-bookkeeping issue,
not a deployment issue; the live cluster is unaffected either way.

## Escalation / where to look next

If a symptom doesn't match anything above: `LIMITATIONS.md` has 80
numbered, dated, real entries — search it first before assuming a novel
bug. `BUILD_LOG.md` has the phase-by-phase build narrative if historical
context on *why* something is built a certain way is needed.
