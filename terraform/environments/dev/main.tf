# NaviGraph dev environment — see terraform/README.md for the module graph
# and the never-apply policy for this environment.

module "resource_group" {
  source = "../../modules/resource-group"

  name     = var.resource_group_name
  location = var.region
  tags     = var.tags
}

module "networking" {
  source = "../../modules/networking"

  name                     = "navigraph-${var.environment}-vnet"
  location                 = var.region
  resource_group_name      = module.resource_group.name
  address_space            = ["10.20.0.0/16"]
  subnet_name              = "navigraph-${var.environment}-subnet"
  subnet_address_prefixes  = ["10.20.1.0/24"]
  tags                     = var.tags
}

module "acr" {
  source = "../../modules/acr"

  name                = "navigraph${var.environment}acr"
  location            = var.region
  resource_group_name = module.resource_group.name
  sku                 = "Basic"
  tags                = var.tags
}

module "key_vault" {
  source = "../../modules/key-vault"

  name                = "navigraph-${var.environment}-kv"
  location            = var.region
  resource_group_name = module.resource_group.name
  tenant_id           = var.tenant_id
  sku_name            = "standard"
  tags                = var.tags
}

module "aks" {
  source = "../../modules/aks"

  name                = "navigraph-${var.environment}-aks"
  location            = var.region
  resource_group_name = module.resource_group.name
  dns_prefix          = "navigraph-${var.environment}"
  node_count          = 2
  vm_size             = "Standard_D2s_v5"
  subnet_id           = module.networking.subnet_id
  tags                = var.tags
}

module "postgres_flexible_server" {
  source = "../../modules/postgres-flexible-server"

  name                   = "navigraph-${var.environment}-pg"
  location               = var.region
  resource_group_name    = module.resource_group.name
  administrator_login    = "navigraphadmin"
  administrator_password = var.postgres_administrator_password
  sku_name               = "B_Standard_B1ms"
  storage_mb             = 32768
  postgres_version       = "16"
  tags                   = var.tags
}

module "entra_app_registration" {
  source = "../../modules/entra-app-registration"

  display_name     = "NaviGraph (${var.environment})"
  sign_in_audience = "AzureADMyOrg"
  redirect_uris    = ["http://localhost:3000/api/auth/callback/azure-ad"]
}
