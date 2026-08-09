variable "name" {
  description = "Name of the AKS cluster."
  type        = string
}

variable "location" {
  description = "Azure region for the AKS cluster."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group the AKS cluster is created in."
  type        = string
}

variable "dns_prefix" {
  description = "DNS prefix for the AKS cluster's API server."
  type        = string
}

variable "node_count" {
  description = "Number of nodes in the default node pool."
  type        = number
  default     = 2
}

variable "vm_size" {
  description = "VM size for nodes in the default node pool."
  type        = string
  default     = "Standard_D2s_v5"
}

variable "subnet_id" {
  description = "Subnet ID (from the networking module) the default node pool attaches to."
  type        = string
}

variable "tags" {
  description = "Tags applied to the AKS cluster."
  type        = map(string)
  default     = {}
}

# Phase 15.4 (LIMITATIONS.md item 51): real AAD-integrated Kubernetes RBAC.
# Deliberately no default for the admin group list -- see this module's
# main.tf comment on `azure_active_directory_role_based_access_control`:
# an empty list here is a REAL, valid, and safe choice (AAD auth enabled,
# nobody has cluster-admin via it yet), but it must be an explicit choice
# a caller makes, not an accidental one from a default nobody thought
# about. Every environment wiring this module in must pass a real value,
# even if that value is `[]`.
variable "aad_admin_group_object_ids" {
  description = <<-EOT
    Object IDs of Azure AD groups that should be granted cluster-admin via
    AKS-managed Azure AD integration. Pass `[]` to enable AAD auth with no
    admin group yet (safe -- see this module's main.tf); pass real Azure AD
    group object IDs (e.g. from `terraform/modules/aks-aad-groups`) to grant
    them cluster-admin for real. There is no way to express "skip AAD
    integration entirely" here -- see `azure_rbac_enabled`'s docstring for
    why enabling AAD auth itself is not optional once this module is used.
  EOT
  type        = list(string)
}

variable "azure_rbac_enabled" {
  description = <<-EOT
    false (default): AKS-managed Azure AD integration authenticates users,
    but AUTHORIZATION is still native Kubernetes RBAC (Role/RoleBinding
    objects in infra/k8s/, with `subjects: [{kind: Group, name: "<AAD
    group object id>"}]`) -- this is the mode LIMITATIONS.md item 51 and
    DECISIONS.md describe, and the one infra/k8s/base/rbac/'s manifests
    are written for.

    true: Azure RBAC (`azurerm_role_assignment` roles like "Azure
    Kubernetes Service RBAC Reader/Admin", scoped via Azure's own IAM
    instead of Kubernetes objects) becomes the enforcement layer instead.
    Real and supported by this module's `main.tf`, but a different design
    this phase did not build the matching K8s-side manifests for -- setting
    `true` without also writing Azure `azurerm_role_assignment` grants for
    every real Azure AD group leaves nobody but `admin_group_object_ids`
    able to do anything at all.
  EOT
  type        = bool
  default     = false
}
