terraform {
  required_providers {
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 2.47"
    }
  }
}

# Real Azure AD groups for AKS RBAC (Phase 15.4, LIMITATIONS.md item 51) --
# mirrors terraform/modules/entra-app-registration's identical stance of
# managing real Azure AD objects via this project's own Terraform, not a
# manual portal click. `members` defaults to `[]` on both groups
# deliberately: applying this module creates two real, but genuinely
# empty, groups -- nobody gets cluster-admin or view access just because
# this module ran. A human must explicitly add real member object IDs
# (their own, or a service account's) before either group grants anyone
# anything on the real cluster -- see this module's own README/the
# aad-k8s-rbac-rollout runbook for that explicit, separate step.

resource "azuread_group" "admins" {
  display_name            = "${var.name_prefix}-aks-admins"
  security_enabled        = true
  description             = "Cluster-admin access to the ${var.name_prefix} AKS cluster via AAD-integrated K8s RBAC."
  members                 = var.admin_member_object_ids
  prevent_duplicate_names = true
}

resource "azuread_group" "viewers" {
  display_name            = "${var.name_prefix}-aks-viewers"
  security_enabled        = true
  description             = "Read-only (Kubernetes built-in 'view' ClusterRole) access to the ${var.name_prefix} AKS cluster via AAD-integrated K8s RBAC."
  members                 = var.viewer_member_object_ids
  prevent_duplicate_names = true
}
