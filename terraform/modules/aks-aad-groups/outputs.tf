output "admin_group_object_id" {
  description = "Object ID of the cluster-admin AAD group -- pass this into terraform/modules/aks's aad_admin_group_object_ids."
  value       = azuread_group.admins.object_id
}

output "viewer_group_object_id" {
  description = "Object ID of the viewer AAD group -- fill this into infra/k8s/base/rbac/'s ClusterRoleBinding subject (see that manifest's own comment and docs/runbooks/aad-k8s-rbac-rollout.md)."
  value       = azuread_group.viewers.object_id
}
