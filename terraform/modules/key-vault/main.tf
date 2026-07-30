terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.100"
    }
  }
}

resource "azurerm_key_vault" "this" {
  name                       = var.name
  location                   = var.location
  resource_group_name        = var.resource_group_name
  tenant_id                  = var.tenant_id
  sku_name                   = var.sku_name
  purge_protection_enabled   = false
  soft_delete_retention_days = 7
  tags                       = var.tags

  # Phase 10: this module's callers grant data-plane access via
  # azurerm_role_assignment (e.g. the AKS Key Vault Secrets Store CSI
  # driver's "Key Vault Secrets User" role in environments/dev/main.tf),
  # which only takes effect when the vault itself is in Azure RBAC mode --
  # the classic access-policy model ignores those role assignments
  # entirely. Confirmed via a real `az keyvault show` after Phase 10b's
  # apply that this defaulted to false, meaning the CSI driver's role
  # assignment had silently granted nothing.
  enable_rbac_authorization = true
}
