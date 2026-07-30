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

  # By default the azurerm provider tries to auto-register every resource
  # provider it supports (~200+), including many this config never uses
  # (e.g. Microsoft.DataMigration) -- on a freshly created subscription this
  # can hang or time out on providers that are slow to register or briefly
  # restricted, blocking `plan` entirely. This provider version (3.117.1)
  # predates the newer resource_provider_registrations argument, so we use
  # the older skip_provider_registration flag instead and register exactly
  # the providers the modules under terraform/modules/ actually reference
  # (confirmed via a real grep for every azurerm_* resource type in this
  # config) once, out of band, via `az provider register`.
  skip_provider_registration = true
}

provider "azuread" {
  tenant_id = var.tenant_id
}
