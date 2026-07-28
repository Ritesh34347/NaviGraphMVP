output "id" {
  description = "Resource ID of the container registry."
  value       = azurerm_container_registry.this.id
}

output "login_server" {
  description = "Login server hostname for the container registry (e.g. used for `docker push`)."
  value       = azurerm_container_registry.this.login_server
}
