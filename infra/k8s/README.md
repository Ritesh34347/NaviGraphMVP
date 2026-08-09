# Kubernetes (`kind` for local validation, real manifests for AKS)

This directory holds a [kind](https://kind.sigs.k8s.io/) cluster config plus
the real Kustomize manifest tree (`base/` + `overlays/{kind,dev}`) built in
Phase 10. It is still **not** part of the everyday inner dev loop —
`infra/docker-compose.yml` remains that — but unlike Phase 1, this is no
longer aspirational: `overlays/kind` deploys the full real topology
(including a real DB-backed `/ask` round trip and the real weighted-canary
mechanism) with zero Azure cost, and `overlays/dev` is what Phase 10b
applies to the real AKS cluster. See
`docs/runbooks/k8s-local-validation.md` for the full local validation
sequence — building/applying this tree requires the standalone `kustomize`
CLI (not just `kubectl`'s embedded one), since `configMapGenerator`
pulls real config directly from `infra/opa/`, `infra/neo4j/`,
`infra/prometheus/`, `infra/grafana/` rather than duplicating those files.

## Usage

```bash
kind create cluster --config infra/k8s/kind-config.yaml
```

This creates a 3-node cluster (`navigraph-dev`): one control-plane node and
two worker nodes. The control-plane node's `extraPortMappings` (80/443) are
what make anything deployed into the cluster reachable from the host at all.

To tear it down:

```bash
kind delete cluster --name navigraph-dev
```

## Status

**Updated 2026-08-09**: Real manifests exist under `base/` (every service
from `infra/docker-compose.yml` except Trino, deliberately excluded from the
cloud deployment — see `LIMITATIONS.md`) and two real overlays:
`overlays/kind` (zero Azure, an ephemeral in-cluster Postgres stands in for
the real Azure Postgres Flexible Server) and `overlays/dev` (real AKS, real
Key Vault-synced secrets, real Postgres Flexible Server FQDN).

Phase 10b's `terraform apply` has since run for real, and `overlays/dev` has
been applied to the real AKS cluster for real (ingress-nginx and
cert-manager bootstrapped, all pods `Running`/`Ready`, a real weighted-canary
run proven — see `LIMITATIONS.md` items 53-58 and `BUILD_LOG.md`'s Phase 10b
entries). **The `REPLACE_AFTER_APPLY` placeholders have in fact already been
substituted with real values, committed directly in these files** — the
handful of comment lines still describing them as placeholders "below" are
themselves now stale (e.g. `secretproviderclass-*.yaml`'s header comments).
This means `overlays/dev/` currently has a real Azure tenant ID, a real
managed-identity client ID, the real Key Vault name, the real ACR hostname,
the real Postgres Flexible Server FQDN, and a real public IP (embedded in
the `nip.io` ingress hostnames) checked into this repository in plaintext —
not secret *values* (no passwords; those stay in Key Vault, fetched via the
CSI driver), but real, identifying infrastructure fingerprints. Worth a
deliberate decision about whether that belongs in a public repo, separate
from whether the docs describing it are accurate.
