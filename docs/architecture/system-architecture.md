# System Architecture

Deep technical architecture reference — deployment topology, tech stack,
and the canary rollout mechanism. For the agent-level request lifecycle,
see [`overview.md`](./overview.md); for the data/schema model, see
[`data-model.md`](./data-model.md).

## Tech stack (rationale summarized; see `DECISIONS.md` for full reasoning)

| Layer | Choice | Why (one line — full rationale in `DECISIONS.md`) |
|---|---|---|
| Agent runtime | Python 3.12, FastAPI, Pydantic v2 | Mature LLM/data/warehouse-client ecosystem (`docs/adr/0001`) |
| Orchestration | Plain async Python function | LangGraph reversed in Phase 9 — never needed graph checkpointing across 8 phases |
| Gateway | FastAPI (same language as runtime) | Shares Pydantic contracts; proxies to agent-runtime over real HTTP |
| Web UI | Next.js (App Router) | Server + client components; real chat UI calls gateway directly from the browser |
| Metadata catalog | Postgres + SQLAlchemy 2 + Alembic | Structural schema/glossary/PII-tag storage, tenant-scoped |
| Knowledge graph | Neo4j Community | Two-tier reference-data + business-concept graph (see `data-model.md`) |
| Session/cache | Redis | Short-lived, TTL-bounded by nature — no reason for a permanent store |
| Federation | Trino (registered, not default route) | Proven early; `direct_connector` stays default until a 2nd source creates real federation need |
| Policy engine | OPA (real `authz.rego`) | RBAC/ABAC only; PII enforcement is separate Python (not routed through Rego) |
| IaC | Terraform (applied for real) | AKS, ACR, Key Vault, Postgres Flexible Server, networking, Entra app registration |
| K8s manifests | Kustomize (`kubectl apply -k`) | No new tooling surface vs. Helm/ArgoCD |
| CD | Push-based GitHub Actions workflow | No new cluster-side operational surface vs. a real GitOps controller |

## System context

```mermaid
flowchart LR
    User([Business analyst / API caller])
    Web["web (Next.js)<br/>real chat UI"]
    GW["gateway<br/>(FastAPI)"]
    AR["agent-runtime<br/>(FastAPI, 25 agents)"]
    PG[(Postgres<br/>catalog + lineage)]
    Neo[(Neo4j<br/>knowledge graph)]
    Redis[(Redis<br/>session + cache)]
    OPA["OPA<br/>authz.rego"]
    SF[(Snowflake<br/>FIDELITY_POC)]
    Anthropic["Anthropic API<br/>claude-sonnet-5"]

    User -->|browser| Web
    User -->|curl / Postman| GW
    Web -->|POST /ask, direct from browser| GW
    GW -->|POST /agents/orchestrator/request_orchestrator/invoke| AR
    AR --> PG
    AR --> Neo
    AR --> Redis
    AR -->|POST /v1/data/navigraph/authz/decision| OPA
    AR -->|real SQL, direct_connector route| SF
    AR -->|LLM calls: intent, semantic retrieval, predicate resolution, narrative, follow-up, judge| Anthropic
```

## Deployment topology (real, live AKS)

```mermaid
flowchart TB
    Internet([Internet])
    Internet -->|api.navigraph.51-8-46-125.nip.io| IngGW[ingress-nginx: gateway + gateway-canary]
    Internet -->|app.navigraph.51-8-46-125.nip.io| IngWeb[ingress-nginx: web + web-canary]

    subgraph AKS["AKS cluster — namespace: navigraph"]
        IngGW --> GWStable["gateway-stable (2 replicas)"]
        IngGW -.weighted %.-> GWCanary["gateway-canary (0-2 replicas)"]
        IngWeb --> WebStable["web-stable (2 replicas)"]
        IngWeb -.weighted %.-> WebCanary["web-canary (0-2 replicas)"]

        GWStable --> AgentRT["agent-runtime (2 replicas, plain rolling update)"]
        GWCanary --> AgentRT
        WebStable -->|GATEWAY_URL, always stable, never canary| GWStable

        AgentRT --> Neo4jSS["neo4j-0 (StatefulSet)"]
        AgentRT --> RedisD["redis (Deployment)"]
        AgentRT --> OpaD["opa (Deployment)"]
        AgentRT --> OtelD["otel-collector"]
        AgentRT -.NetworkPolicy egress.-> External1["Snowflake (443/80)"]
        AgentRT -.NetworkPolicy egress.-> External2["Anthropic API (443)"]
        AgentRT -.NetworkPolicy egress.-> PGExt[("Azure Postgres<br/>Flexible Server (5432)")]

        PromD["prometheus"] --> AgentRT
        PromD --> GWStable
        PromD --> IngGW
        GrafD["grafana"] --> PromD

        CertMgr["cert-manager<br/>(installed cluster-wide)"] -.HTTP-01 challenge.-> IngGW
    end

    CertMgr -->|letsencrypt-prod| LE["Let's Encrypt"]

    classDef stable fill:#1a2540,stroke:#5b8def,color:#e8ecf7
    classDef canary fill:#2a1f3d,stroke:#d9a441,color:#e8ecf7
    class GWStable,WebStable stable
    class GWCanary,WebCanary canary
```

Key real design points:

- **Only `gateway` and `web` get a canary track.** `agent-runtime` and
  every data store/OPA/observability component is plain rolling-update,
  internal-only — no external traffic ever hits them directly.
- **`web`'s server-side `GATEWAY_URL` always points at `gateway-stable`**,
  never canary — an in-flight gateway canary rollout must never risk
  breaking SSR page renders for users who didn't opt in via a direct
  browser call. Only `NEXT_PUBLIC_GATEWAY_URL` (the browser-side URL, set
  as a real Deployment env var on both tracks — see `LIMITATIONS.md`
  item 78) sees canary-weighted traffic.
- **`NetworkPolicy` is default-deny-all**, with explicit allow-rules
  layered on top per real, necessary traffic path (`infra/k8s/base/networkpolicy-*.yaml`)
  — this exact model has caught 3 real bugs this project (gateway↔agent-runtime
  egress gap, cert-manager's HTTP-01 solver pods, each logged in
  `LIMITATIONS.md`).
- **TLS is real and browser-trusted**: `cert-manager` + `letsencrypt-prod`,
  promoted from `letsencrypt-staging` only after a real staged
  verification (`DECISIONS.md`'s cert promotion entry).

## Canary rollout mechanics

```mermaid
sequenceDiagram
    participant Dev as git push to main
    participant CI as CI (lint/typecheck/unit)
    participant Build as build-and-push job
    participant Deploy as deploy-canary job
    participant Bake as canary-bake job (×3: 10%, 50%, 100%)
    participant Gate as canary_gate.py
    participant Prom as Prometheus
    participant Promote as promote job

    Dev->>CI: push
    CI-->>Dev: pass
    Dev->>Build: CD Deploy triggered
    Build->>Build: docker build + push 3 images to ACR (tag = git SHA)
    Build->>Deploy: images ready
    Deploy->>Deploy: kubectl apply -k overlays/dev (base manifests)
    Deploy->>Deploy: point *-canary track at new SHA, weight=0
    loop for each weight in [10, 50, 100]
        Deploy->>Bake: set canary weight
        Bake->>Prom: port-forward, poll every 30s (up to 5 min)
        Prom-->>Gate: nginx_ingress_controller_requests / _request_duration_seconds
        Gate->>Gate: check: 5xx<1%, error rate <=2x stable, p95 <1.5x stable
        alt gate fails
            Gate->>Bake: rollback (weight->0, canary replicas->0), job fails
        else gate passes
            Gate->>Bake: continue
        end
    end
    Bake->>Promote: all 3 weights passed
    Promote->>Promote: kubectl set image *-stable to new SHA, wait for rollout
    Promote->>Promote: scale canary back to a clean single-track state
    Promote->>Promote: bot-commit new SHA into overlays/dev/kustomization.yaml
```

Real, documented failure modes of this exact mechanism (all in
`LIMITATIONS.md`, all with real fixes on record):

- The `promote` job's bot-commit can race a concurrent CD run's own
  bot-commit, either as a clean-rebase conflict (fixed with a bounded
  retry-and-rebase loop) or a genuine same-line merge conflict when two
  promotions land on the same field (no auto-resolution possible —
  requires a manual correction to match the confirmed-live cluster
  state; happened twice, see items 72/76).
- `canary_gate.py`'s checks are the same three metrics regardless of
  10%/50%/100% weight — a real, deliberate simplification, not a bug.

## Secrets and identity

- **Azure Key Vault Provider for Secrets Store CSI Driver** syncs named
  secrets into one shared `navigraph-app-secrets` K8s `Secret` per
  namespace — never plaintext K8s secrets checked into git.
- **Known, accepted gap**: one shared AKS addon identity, not per-pod
  Azure Workload Identity federation — real isolation is which secret
  names each `SecretProviderClass` declares, not a hard per-pod identity
  wall (tested explicitly by `tests/security/cloud/test_secret_provider_scoping.py`).
- **Known, accepted gap**: no AAD-integrated Kubernetes RBAC in `dev` —
  anyone with a kubeconfig is effectively cluster-admin (tested
  explicitly by `tests/security/cloud/test_rbac_least_privilege.py`).

See [`security-compliance.md`](../security/security-compliance.md) for
the full controls mapping.
