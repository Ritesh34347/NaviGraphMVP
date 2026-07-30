terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.100"
    }
  }
}

resource "azurerm_postgresql_flexible_server" "this" {
  name                   = var.name
  resource_group_name    = var.resource_group_name
  location               = var.location
  version                = var.postgres_version
  administrator_login    = var.administrator_login
  administrator_password = var.administrator_password
  storage_mb             = var.storage_mb
  sku_name               = var.sku_name
  zone                   = "1"
  tags                   = var.tags
}

# Phase 10: the real application database -- without this, only the
# server's own default "postgres" database exists, and every real
# POSTGRES_DB=navigraph connection (navigraph_catalog, navigraph_lineage)
# would fail against a real server.
resource "azurerm_postgresql_flexible_server_database" "app" {
  name      = var.database_name
  server_id = azurerm_postgresql_flexible_server.this.id
  collation = "en_US.utf8"
  charset   = "UTF8"
}

# Phase 10b: real bug found live -- `public_network_access_enabled = true`
# on the server only means the server CAN be reached from the internet;
# Postgres Flexible Server separately requires an explicit firewall rule
# before any connection succeeds. Without this, every real connection
# attempt (including from AKS pods on the same VNet, since this module
# has no VNet integration / private endpoint) hung until
# ConnectionTimeout. "0.0.0.0"-"0.0.0.0" is Azure's own documented special
# convention for "Allow public access from any Azure service within
# Azure to this server" -- it does not open the server to the public
# internet at large, only to traffic originating from Azure's own
# infrastructure (which AKS's outbound load balancer IP is part of).
resource "azurerm_postgresql_flexible_server_firewall_rule" "allow_azure_services" {
  name             = "AllowAllAzureServices"
  server_id        = azurerm_postgresql_flexible_server.this.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}
