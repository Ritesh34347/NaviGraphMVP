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

  name                    = "navigraph-${var.environment}-vnet"
  location                = var.region
  resource_group_name     = module.resource_group.name
  address_space           = ["10.20.0.0/16"]
  subnet_name             = "navigraph-${var.environment}-subnet"
  subnet_address_prefixes = ["10.20.1.0/24"]
  tags                    = var.tags
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
  # Standard_D2s_v5 is not in this subscription's allowed VM size list for
  # eastus (confirmed via a real 400 from AKS create: "The VM size of
  # Standard_D2s_v5 is not allowed in your subscription in location
  # 'eastus'"); Standard_D2s_v7 is the closest equivalent (2 vCPU, general
  # purpose) that IS on the real allowed list for this subscription.
  vm_size   = "Standard_D2s_v7"
  subnet_id = module.networking.subnet_id
  tags      = var.tags
}

module "postgres_flexible_server" {
  source = "../../modules/postgres-flexible-server"

  # This subscription is offer-restricted from provisioning Postgres
  # Flexible Server in eastus AND eastus2 (both confirmed via real
  # "LocationIsOfferRestricted" errors), so Postgres alone uses a separate
  # region (see postgres_region) -- a resource group is just a management
  # container, resources inside it are not required to share its nominal
  # location. The name is unique to this attempt (never tried in eastus or
  # eastus2) because a failed create there left a transient ARM name-lock
  # blocking reuse elsewhere even though `az resource show` confirmed no
  # such resource actually existed -- a fresh name sidesteps that entirely.
  name                   = "navigraph-${var.environment}-pg-cus"
  location               = var.postgres_region
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

# Phase 10: lets the AKS-managed Key Vault Secrets Store CSI driver addon
# (module.aks.key_vault_secrets_provider_object_id) actually read real
# secrets that infra/k8s/overlays/dev's SecretProviderClass resources
# request -- without this, the addon identity exists but has no
# permission to read anything from the vault.
resource "azurerm_role_assignment" "aks_key_vault_secrets_user" {
  scope                = module.key_vault.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = module.aks.key_vault_secrets_provider_object_id
}

# Phase 10: lets .github/workflows/cd-deploy.yml's existing CI service
# principal (the same identity already used by terraform-plan.yml's OIDC
# login) push real images to the real registry.
resource "azurerm_role_assignment" "ci_acr_push" {
  scope                = module.acr.id
  role_definition_name = "AcrPush"
  principal_id         = var.ci_service_principal_object_id
}

# Phase 10: lets that same CI service principal fetch a kubeconfig and
# deploy to the real AKS cluster. See LIMITATIONS.md's new item on this
# module having no AAD-integrated Kubernetes RBAC yet -- this role grants
# cluster access, not namespace-scoped Kubernetes permissions.
resource "azurerm_role_assignment" "ci_aks_cluster_user" {
  scope                = module.aks.cluster_id
  role_definition_name = "Azure Kubernetes Service Cluster User Role"
  principal_id         = var.ci_service_principal_object_id
}
