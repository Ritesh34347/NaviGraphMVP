output "cluster_id" {
  description = "Resource ID of the AKS cluster."
  value       = azurerm_kubernetes_cluster.this.id
}

output "host" {
  description = "API server endpoint of the AKS cluster."
  value       = azurerm_kubernetes_cluster.this.kube_config.0.host
  sensitive   = true
}

output "kube_config_raw" {
  description = "Raw kubeconfig for the AKS cluster. Sensitive."
  value       = azurerm_kubernetes_cluster.this.kube_config_raw
  sensitive   = true
}

output "key_vault_secrets_provider_client_id" {
  description = "Client ID of the AKS-managed identity for the Key Vault Secrets Store CSI driver addon. Used by infra/k8s/overlays/dev's SecretProviderClass resources' userAssignedIdentityID parameter."
  value       = azurerm_kubernetes_cluster.this.key_vault_secrets_provider[0].secret_identity[0].client_id
}

output "key_vault_secrets_provider_object_id" {
  description = "Object (principal) ID of the same identity, used to grant it Key Vault access via azurerm_role_assignment."
  value       = azurerm_kubernetes_cluster.this.key_vault_secrets_provider[0].secret_identity[0].object_id
}
