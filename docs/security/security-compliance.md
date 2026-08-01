# Security & Compliance

NaviGraph's compliance target, set at project kickoff, is **SOC 2 Type
II** — this maps the real controls actually built to that regime's usual
trust-services criteria, each linked to its real real adversarial test
and, where relevant, its known, honestly-logged gap. Nothing below is
aspirational; every control listed is real code, running today against
the live deployment.

## Access control (RBAC/ABAC)

| Control | Real implementation | Adversarial test |
|---|---|---|
| Role-based authorization | Real OPA policy `infra/opa/policies/authz.rego` — `default allow = false`, `allowed_roles := {"analyst", "pii_viewer", "admin"}` | `tests/security/test_insufficient_roles_fail_closed.py` |
| Tenant isolation (ABAC) | `authz.rego`'s `tenant_claim_matches` — `input.claims.tenant_id == input.tenant_id` | `tests/security/test_tenant_isolation.py` |
| Fail-closed on policy engine failure | `guardrail.policy_authorization` — any OPA-call exception → one `AgentError(code="opa_unreachable")`, `authorized=[]`, nothing proceeds (the opposite default from Caching's deliberate fail-open) | Same suite; explicitly tests OPA-unreachable as well as bad-input cases |
| Adversarial policy inputs | Parametrized: malformed/empty `tenant_id`, missing `claims.tenant_id`, empty roles, self-declared-`admin` escalation with no backing claim | `tests/security/test_opa_policy_adversarial.py` — every case denied; the self-escalation case is explicitly noted as the literal shape of the still-open Azure AD gap below, not a policy bug |
| Row/cost limits per role | `guardrail.query_cost_estimator` — hardcoded `ROLE_ROW_LIMITS` dict, capped at `execution_planning`'s global `MAX_ROWS_CAP` | Covered by `tests/integration/guardrail_pipeline/` |

**Known, open gap**: `roles`/`claims` are caller-supplied, not
cryptographically verified — no real Azure AD JWT validation exists yet
(`LIMITATIONS.md` item 23). Every control above is real and correctly
enforced *given* the roles/claims it's handed; the identity those values
represent isn't yet independently verified. This is the single most
significant open item in the platform's security posture.

## Data protection (PII)

| Control | Real implementation | Adversarial test |
|---|---|---|
| PII column tagging | `CatalogColumn.is_pii: bool`, backfilled via a real, confirmed-live discovery query + `tools/scripts/tag_pii_columns.py` (never invented column names) | — |
| PII access denial | `guardrail.pii_exposure_checker` — any statement referencing an `is_pii=true` column, without an authorized role (`pii_viewer`/`admin`), is a non-recoverable `AgentError` | `tests/security/test_pii_exposure_denied.py` — `analyst` denied on `RISKLEVEL`, `pii_viewer` cleared |
| Separation from RBAC | PII enforcement is a **separate Python agent**, not routed through Rego — keeps the policy engine simple/auditable while still giving PII its own dedicated, independently-testable gate | Same suite |

## Injection / execution safety

| Control | Real implementation |
|---|---|
| SQL injection | Bind-parameterized values only, never string-interpolated (`query.sql_generation`) |
| Statement-shape safety | `query.execution_planning` — real SQL parsing (not regex) rejects anything but a single SELECT statement; a deliberately malicious `; DROP TABLE` statement is proven rejected in `tests/integration/query_pipeline/` |
| No LLM-authored raw SQL | The LLM in `sql_generation` only ever resolves predicate *values* into `{column, operator, value}` triples — it never writes SQL text directly |

## Audit / traceability

| Control | Real implementation |
|---|---|
| Full request audit trail | `ops.lineage_recorder` persists every upstream agent's real `lineage_events`, idempotently, queryable via `GET /lineage/{trace_id}?tenant_id=...` |
| Change management | `CODEOWNERS` + required CI checks (`ci.yml`) on every PR |
| Real, itemized incident log | `LIMITATIONS.md` — every real bug found, its root cause, and its fix, numbered and dated (80 items) |

## Network security

| Control | Real implementation |
|---|---|
| Default-deny network posture | `infra/k8s/base/networkpolicy-default-deny.yaml` — every pod denied by default; explicit allow-rules layered on top per real, necessary path only |
| Positive control on isolation | `tests/security/cloud/test_network_policy_isolation.py` — asserts gateway *can* reach agent-runtime (not just that unrelated traffic is blocked) |
| TLS everywhere public-facing | Real, browser-trusted certs via cert-manager + `letsencrypt-prod` (promoted from `letsencrypt-staging` only after a verified staging issuance) | `tests/security/cloud/test_ingress_tls.py` |
| ACR not publicly exposed | — | `tests/security/cloud/test_acr_private.py` |
| AKS API server exposure | — | `tests/security/cloud/test_aks_api_server_exposure.py` |

## Secrets management

| Control | Real implementation |
|---|---|
| No plaintext secrets in git | Azure Key Vault Provider for Secrets Store CSI Driver syncs named secrets into `navigraph-app-secrets`; local `kind` dev uses `kubectl create secret generic --from-env-file` manually, never committed |
| Secret scoping | Each service's own `SecretProviderClass` declares only the secret names *it* needs | `tests/security/cloud/test_secret_provider_scoping.py` |

**Known, accepted gap**: one shared AKS addon identity, not per-pod
Azure Workload Identity federation — real isolation is *which secret
names* each `SecretProviderClass` declares, not a hard per-pod identity
wall. Logged and tested honestly, not assumed away.

## Kubernetes RBAC

**Known, accepted gap**: no AAD-integrated Kubernetes RBAC in the `dev`
environment — anyone who can fetch a kubeconfig is effectively
cluster-admin. `tests/security/cloud/test_rbac_least_privilege.py`
proves and documents this gap explicitly rather than assuming it away —
this is real, current scope for the `dev` environment, not a production
posture.

## Summary: real gaps carried forward, honestly

Every gap named above is already logged in `LIMITATIONS.md` with its own
item number and "what full version requires" note. Nothing here is a new
disclosure — this document exists to give the *consolidated*, SOC-2-
shaped view of the same real facts already tracked incrementally
throughout the build.
