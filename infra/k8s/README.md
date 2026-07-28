# Kubernetes (kind) — future phase

This directory holds a [kind](https://kind.sigs.k8s.io/) cluster config for
future Kubernetes-based experimentation. It is **not** part of the Phase 1
inner dev loop — `infra/docker-compose.yml` is the everyday local stack. This
exists so that when a later phase needs to validate Kubernetes manifests
(e.g. as a step towards the AKS target described in `terraform/`), a local
multi-node cluster is one command away.

## Usage

```bash
kind create cluster --config infra/k8s/kind-config.yaml
```

This creates a 3-node cluster (`navigraph-dev`): one control-plane node and
two worker nodes.

To tear it down:

```bash
kind delete cluster --name navigraph-dev
```

## Status

No Kubernetes manifests exist yet. Manifests (Deployments, Services, Ingress,
ConfigMaps mirroring the docker-compose service set) land in a later phase,
once the AKS Terraform module (`terraform/modules/aks`) is closer to being
exercised for real. Until then, this config is validated to create a cluster
but nothing is deployed into it.
