output "resource_group_name" {
  description = "Name of the dev resource group."
  value       = module.resource_group.name
}

output "aks_cluster_id" {
  description = "Resource ID of the dev AKS cluster."
  value       = module.aks.cluster_id
}

output "aks_kube_config_host" {
  description = "API server endpoint for the dev AKS cluster."
  value       = module.aks.host
}

output "aks_kube_config_raw" {
  description = "Raw kubeconfig for the dev AKS cluster. Sensitive — never print or commit this."
  value       = module.aks.kube_config_raw
  sensitive   = true
}

output "acr_login_server" {
  description = "Login server hostname for the dev container registry."
  value       = module.acr.login_server
}

output "key_vault_uri" {
  description = "Vault URI for the dev Key Vault."
  value       = module.key_vault.vault_uri
}

output "postgres_fqdn" {
  description = "Fully qualified domain name of the dev Postgres Flexible Server."
  value       = module.postgres_flexible_server.fqdn
}

output "entra_app_client_id" {
  description = "Application (client) ID of the dev Azure AD app registration."
  value       = module.entra_app_registration.client_id
}

output "aks_key_vault_secrets_provider_client_id" {
  description = "Client ID of the AKS-managed Key Vault Secrets Store CSI driver addon identity -- used by infra/k8s/overlays/dev's SecretProviderClass resources' userAssignedIdentityID parameter."
  value       = module.aks.key_vault_secrets_provider_client_id
}
