variable "name_prefix" {
  description = "Prefix for both groups' display names, e.g. \"navigraph-dev\" -> \"navigraph-dev-aks-admins\"."
  type        = string
}

variable "admin_member_object_ids" {
  description = "Azure AD object IDs (users, or service principals) to add to the cluster-admin group at apply time. Defaults to empty -- see this module's main.tf for why that's the safe, deliberate default."
  type        = list(string)
  default     = []
}

variable "viewer_member_object_ids" {
  description = "Azure AD object IDs to add to the read-only viewer group at apply time. Defaults to empty -- see this module's main.tf."
  type        = list(string)
  default     = []
}
