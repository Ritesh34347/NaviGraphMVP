output "id" {
  description = "Resource ID of the Key Vault."
  value       = azurerm_key_vault.this.id
}

output "vault_uri" {
  description = "URI of the Key Vault, used by application services to fetch secrets."
  value       = azurerm_key_vault.this.vault_uri
}
