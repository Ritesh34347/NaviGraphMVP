variable "subscription_id" {
  description = "Azure subscription ID to deploy into. No default — must be supplied via terraform.tfvars or an environment variable, and is never applied against a real subscription during this phase (see terraform/README.md)."
  type        = string
}

variable "tenant_id" {
  description = "Azure AD (Entra ID) tenant ID that owns the subscription and the app registration."
  type        = string
}

variable "ci_service_principal_object_id" {
  description = "Object ID of the existing CI service principal (the same identity already used by .github/workflows/terraform-plan.yml's OIDC login). Phase 10 grants it AcrPush on the container registry and Azure Kubernetes Service Cluster User Role on the AKS cluster, so .github/workflows/cd-deploy.yml can push images and deploy. No default — must be supplied once real Azure credentials exist (see terraform/README.md and DECISIONS.md's Phase 10 entry)."
  type        = string
}

variable "region" {
  description = "Azure region to deploy resources into."
  type        = string
  default     = "eastus"
}

variable "postgres_region" {
  description = "Azure region for the Postgres Flexible Server specifically. Separate from `region` because this subscription is offer-restricted from provisioning that service in eastus AND eastus2 (both confirmed via real LocationIsOfferRestricted errors) -- a resource group is just a management container, so Postgres can live in a different region than the rest of the environment. centralus, northeurope, uksouth, and australiaeast were all confirmed available via real (immediately deleted) probe deployments; centralus is used as the closest region to the rest of this environment's eastus resources."
  type        = string
  default     = "centralus"
}

variable "resource_group_name" {
  description = "Name of the resource group all dev-environment resources are created in."
  type        = string
  default     = "navigraph-dev-rg"
}

variable "environment" {
  description = "Short environment name, used in resource naming and tags."
  type        = string
  default     = "dev"
}

variable "postgres_administrator_password" {
  description = "Administrator password for the dev Postgres Flexible Server. Supply via a .tfvars file that is never committed, or via TF_VAR_postgres_administrator_password — never hardcode a real value here."
  type        = string
  sensitive   = true
  default     = "placeholder-never-applied"
}

variable "aks_admin_member_object_ids" {
  description = "Azure AD object IDs to add to the real navigraph-dev-aks-admins group at apply time (Phase 15.4, LIMITATIONS.md item 51). Defaults to empty -- applying with the default grants nobody cluster-admin via AAD; a human must explicitly list real object IDs here (or add members out-of-band after apply) before this group does anything. See docs/runbooks/aad-k8s-rbac-rollout.md."
  type        = list(string)
  default     = []
}

variable "aks_viewer_member_object_ids" {
  description = "Azure AD object IDs to add to the real navigraph-dev-aks-viewers group at apply time. Defaults to empty -- see aks_admin_member_object_ids's identical rationale."
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Common tags applied to every resource in this environment."
  type        = map(string)
  default = {
    project     = "navigraph"
    environment = "dev"
    managed_by  = "terraform"
  }
}
