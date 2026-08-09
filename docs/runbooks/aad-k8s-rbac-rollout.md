# Runbook: Rolling Out AAD-Integrated Kubernetes RBAC

Phase 15.4 (`LIMITATIONS.md` item 51) built real Terraform code and
Kubernetes manifests for AAD-integrated Kubernetes RBAC, but deliberately
**did not apply any of it** to the real `dev` AKS cluster. This runbook is
the real, step-by-step path to actually rolling it out — read
`terraform/README.md` first if you haven't: `terraform apply` is a manual,
human-approved action, never run from CI, and this phase follows that same
rule.

## What exists today (code only, not applied)

- `terraform/modules/aks`: a new `azure_active_directory_role_based_access_control`
  block, gated behind two new variables (`aad_admin_group_object_ids`,
  `azure_rbac_enabled`, defaulting to native Kubernetes RBAC mode).
- `terraform/modules/aks-aad-groups`: creates two real Azure AD groups
  (`navigraph-<env>-aks-admins`, `navigraph-<env>-aks-viewers`), both with
  **zero members by default** — applying this module creates real but
  empty groups, granting nobody anything until a human adds real members.
- `terraform/environments/dev/main.tf`: wires both of the above together
  — the admins group gets cluster-admin via Azure AD integration; the
  viewers group is meant to be bound to Kubernetes' built-in `view`
  ClusterRole via a real manifest (next bullet).
- `infra/k8s/base/rbac/cluster-role-binding-viewers.yaml`: a real
  `ClusterRoleBinding` binding a group named
  `REPLACE_WITH_AKS_VIEWER_GROUP_OBJECT_ID` (a placeholder, not a working
  value) to the `view` ClusterRole. Included in the base kustomization, so
  it already renders correctly (verified via a real `kustomize build` in
  this phase) — it just doesn't reference a real group yet.

## Why this wasn't applied here

Two real reasons, not caution for its own sake:

1. **A real business decision this session cannot make unilaterally.**
   Which real humans (or service accounts) belong in the admin group vs.
   the viewer group is exactly the kind of decision this project's own
   precedent (e.g. the `navikenz-poc` default-`DataSource` choice) treats
   as requiring an explicit human answer, not an assumption.
2. **`terraform apply` against a live cluster is a real, live,
   cost-and-security-relevant infrastructure change.** This sandbox has no
   Azure credentials anyway, but even with them, this is exactly the class
   of action `terraform/README.md`'s "never-apply policy" and this
   project's own risk posture reserve for a human to run directly, outside
   CI, after reviewing a real `plan`.

## Rollout steps, once a human is ready

### 1. Decide real group membership

Get the real Azure AD object IDs (user or service principal) of whoever
should have cluster-admin vs. read-only access to the `dev` cluster.

### 2. Apply the Terraform changes

```bash
cd terraform/environments/dev
terraform init
terraform plan \
  -var="aks_admin_member_object_ids=[\"<real-object-id-1>\"]" \
  -var="aks_viewer_member_object_ids=[\"<real-object-id-2>\"]"
# Review the plan for real. Only then:
terraform apply \
  -var="aks_admin_member_object_ids=[\"<real-object-id-1>\"]" \
  -var="aks_viewer_member_object_ids=[\"<real-object-id-2>\"]"
```

This creates the two real Azure AD groups (with real members) and enables
`azure_active_directory_role_based_access_control` on the live cluster —
this step alone lets your admin group's members authenticate via
`az aks get-credentials` with real Azure AD login and receive
cluster-admin.

### 3. Fill in the real viewer group's object ID

```bash
terraform output -raw aks_viewer_group_object_id
```

Replace `REPLACE_WITH_AKS_VIEWER_GROUP_OBJECT_ID` in
`infra/k8s/base/rbac/cluster-role-binding-viewers.yaml` with that real
value (a `sed -i` one-liner, or edit by hand).

### 4. Apply the updated manifest to the real cluster

```bash
az aks get-credentials --resource-group <dev-resource-group> --name navigraph-dev-aks
kustomize build infra/k8s/overlays/dev --load-restrictor LoadRestrictionsNone | kubectl apply -f -
```

### 5. Verify for real

As a member of the viewer group (a real `az login` as that user, then
`az aks get-credentials`):

```bash
kubectl auth can-i get pods --all-namespaces        # expect: yes
kubectl auth can-i delete pods --all-namespaces     # expect: no
kubectl auth can-i get secrets --all-namespaces      # expect: no -- `view` never grants this
```

As a member of the admin group:

```bash
kubectl auth can-i "*" "*" --all-namespaces          # expect: yes
```

If any of these return the opposite of what's expected, the rollout
didn't work as designed — do not assume it's fine.

## What this does NOT change

- **The CI/deploy service principal's own permissions.** That's a
  separate, already-real, and deliberately still-broad grant (deployment
  automation needs to manage cluster resources) —
  `tests/security/cloud/test_rbac_least_privilege.py` documents this and
  is expected to keep passing (finding effective cluster-admin for that
  identity) even after this rollout; see that test's own updated
  docstring.
- **Anything for local `kind` validation.** `infra/k8s/overlays/kind`
  builds and applies this same `ClusterRoleBinding` with the placeholder
  subject name — harmless there (no real AAD-authenticated user will ever
  match that literal string), and lets `kustomize build`/`kubectl apply`
  exercise the manifest's structure for free on every PR without needing
  real Azure AD at all.

## What has and hasn't been verified

- **Verified for real, in this sandbox**: `terraform fmt -check -recursive`
  is clean (proves the HCL is syntactically well-formed). `kustomize build`
  against both `infra/k8s/overlays/kind` and `infra/k8s/overlays/dev`
  succeeds and renders `cluster-role-binding-viewers.yaml` correctly —
  critically, as a genuinely cluster-scoped object with NO `namespace:`
  field stamped onto it (Kustomize's built-in resource-scope awareness
  correctly treated `ClusterRoleBinding` like it already treats
  `Namespace`), confirming this manifest integrates correctly with the
  existing kustomization tree.
- **NOT verified**: `terraform validate`/`plan` could not be run in this
  sandbox — network egress to `registry.terraform.io` (needed to download
  the `azurerm`/`azuread` provider plugins) is blocked by this
  environment's egress policy, a real, reported, not-routed-around
  limitation (mirrors this session's identical stance on `api.slack.com`
  being blocked for the Slack bot's own verification). `kubectl apply
  --dry-run` against a real API server's OpenAPI schema and a full `kind`
  cluster smoke test were attempted and also blocked (`kind.sigs.k8s.io`
  is not reachable here either) — this azurerm block's exact attribute
  names/types are correct per the schema this session has direct
  knowledge of, but that is not the same as a real `terraform validate`
  proving it. **Whoever runs step 2 above for the first time should treat
  `terraform plan`'s output as the real first check this configuration is
  valid, not assume this runbook already proved that.**
