output "vnet_id" {
  description = "Resource ID of the virtual network."
  value       = azurerm_virtual_network.this.id
}

output "subnet_id" {
  description = "Resource ID of the subnet, for use by AKS/Postgres modules."
  value       = azurerm_subnet.this.id
}
