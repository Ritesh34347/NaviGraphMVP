terraform {
  required_version = ">= 1.6.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.100"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 2.47"
    }
  }

  # Uncomment and configure a real remote state backend before any team
  # beyond one person uses this environment. Local state (the default when
  # no backend block is present) has no locking and is easy to lose.
  #
  # backend "azurerm" {
  #   resource_group_name  = "navigraph-tfstate-rg"
  #   storage_account_name = "navigraphtfstate"
  #   container_name       = "tfstate"
  #   key                  = "dev.terraform.tfstate"
  # }
}

provider "azurerm" {
  features {}
  subscription_id = var.subscription_id
  tenant_id       = var.tenant_id
}

provider "azuread" {
  tenant_id = var.tenant_id
}
