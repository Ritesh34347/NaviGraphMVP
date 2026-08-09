# Terraform (Azure)

**`terraform apply` must never run in CI.** CI
(`.github/workflows/terraform-plan.yml`) runs `terraform fmt -check` and
`terraform validate` on every PR touching `terraform/**`, and runs
`terraform plan` only when Azure credentials happen to be configured as repo
secrets — even then, `plan` is read-only. Applying real infrastructure
changes is a deliberate, manual action performed by a human, outside of CI,
only after explicit, separate sign-off on the exact reviewed `plan` — see
`DECISIONS.md` for why this project chose a local-first-by-default,
Azure-targeted design, and its Phase 10b entries for the real `apply`.

**Updated 2026-08-09** (was stale: this file previously said Terraform was
"never applied" and "no real Azure resources exist"). As of Phase 10b
(2026-07-30), real Azure infrastructure has been created and verified via
`terraform apply` and `terraform state list`/`kubectl get nodes` against a
real subscription: a resource group, VNet/subnet, ACR, a 2-node AKS cluster,
Key Vault, Postgres Flexible Server + database, and an Entra app
registration + service principal. This is genuinely billable infrastructure,
not a plan preview — see `LIMITATIONS.md` items 5, 53, 54, 55, 56, 57, 58 for
the full history, including real subscription-specific issues `apply`
surfaced and fixed, and two real incidents (a briefly-exposed cluster
credential, a Key Vault RBAC misconfiguration) found and remediated along
the way. What's still true from this document's original framing: CI itself
still only ever runs `fmt`/`validate`/`plan`, never `apply`, and every real
`apply` so far has been a manual, human-approved action outside CI.

## Why this exists now

Local development happens entirely through `infra/docker-compose.yml`.
Writing the Azure target now, even unapplied, forces the eventual cloud
topology to be designed deliberately rather than retrofitted later, and gives
CI something real to validate on every infra-related change.

## Module graph

`terraform/environments/dev` wires together the following modules from
`terraform/modules/`:

```
                      +-------------------+
                      | resource-group    |
                      +---------+---------+
                                |
       +---------------+-------+-------+------------------+
       |               |               |                  |
       v               v               v                  v
+-------------+  +-----------+  +-------------+  +----------------------+
| networking  |  | acr       |  | key-vault   |  | entra-app-registration|
+------+------+  +-----------+  +-------------+  +----------------------+
       |
       v
+-------------+        +------------------------------+
| aks         |        | postgres-flexible-server      |
+-------------+        +------------------------------+
```

- **resource-group**: the container every other module's resources live in.
- **networking**: VNet + subnet that `aks` and `postgres-flexible-server`
  attach to.
- **aks**: the eventual home for the gateway/agent-runtime/web services in
  the cloud target.
- **acr**: container registry those services' images would be pushed to.
- **key-vault**: secret storage for Snowflake credentials, the Anthropic API
  key, and Azure AD client secrets in the cloud target.
- **postgres-flexible-server**: managed Postgres, replacing the
  docker-compose `postgres` service in the cloud target.
- **entra-app-registration**: the Azure AD (Entra ID) app registration behind
  OAuth/OIDC login, replacing the manually-created dev app registration used
  in local development (see `docs/runbooks/local-dev-smoke-test.md`).

Note there is deliberately no Neo4j or Trino module yet — per
`LIMITATIONS.md` items 2 and 3, Neo4j HA/Aura and real Trino catalog wiring
are both deferred past this phase, so there is nothing productive to encode
in Terraform for them yet.

## Environments

Only `dev` exists today. A `staging`/`prod` environment would be added as a
sibling under `terraform/environments/` when there is an actual need to
provision one, reusing the same modules with different variable values.

## Remote state

`terraform/environments/dev/providers.tf` includes a commented-out
`backend "azurerm"` block. It must be uncommented and pointed at a real
storage account before more than one person relies on this environment's
state — local state files are fine for a single person validating the
skeleton, but are not safe for team use (no locking, easy to lose, no shared
source of truth).
