output "id" {
  description = "Resource ID of the Postgres Flexible Server."
  value       = azurerm_postgresql_flexible_server.this.id
}

output "fqdn" {
  description = "Fully qualified domain name of the Postgres Flexible Server."
  value       = azurerm_postgresql_flexible_server.this.fqdn
}
