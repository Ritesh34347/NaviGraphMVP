terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.100"
    }
  }
}

resource "azurerm_kubernetes_cluster" "this" {
  name                = var.name
  location            = var.location
  resource_group_name = var.resource_group_name
  dns_prefix          = var.dns_prefix

  default_node_pool {
    name           = "default"
    node_count     = var.node_count
    vm_size        = var.vm_size
    vnet_subnet_id = var.subnet_id
  }

  identity {
    type = "SystemAssigned"
  }

  # AKS enabled the OIDC issuer by default on the real cluster even though
  # this module never requested it, and Azure's API rejects any attempt to
  # disable it once on ("OIDCIssuerFeatureCannotBeDisabled") -- declaring it
  # explicitly here matches the real cluster's actual state instead of
  # Terraform trying to revert it to an unset/default value every plan.
  oidc_issuer_enabled = true

  # Phase 10: the Azure Key Vault Provider for Secrets Store CSI Driver
  # addon. Enabling this creates a real, AKS-managed user-assigned identity
  # (exposed below as key_vault_secrets_provider_client_id/_object_id) that
  # infra/k8s/overlays/dev's SecretProviderClass resources reference to sync
  # real Key Vault secrets into the cluster — see LIMITATIONS.md's new item
  # on this addon using one shared identity rather than per-pod Azure
  # Workload Identity federation.
  key_vault_secrets_provider {
    secret_rotation_enabled  = true
    secret_rotation_interval = "2m"
  }

  # Phase 10: Azure CNI networking with NetworkPolicy enforcement. Without
  # this block, real Kubernetes NetworkPolicy objects (see
  # infra/k8s/base/*/networkpolicy.yaml) would be silently unenforced —
  # the cluster would accept them without complaint but never actually
  # restrict traffic.
  network_profile {
    network_plugin = "azure"
    network_policy = "azure"
  }

  # Phase 15.4 (LIMITATIONS.md item 51, DECISIONS.md): real AAD-integrated
  # Kubernetes RBAC — closes the gap `tests/security/cloud
  # /test_rbac_least_privilege.py` documents for HUMAN cluster access.
  # `managed = true` is AKS-managed Azure AD integration (the only mode
  # this azurerm provider version supports for a new cluster — "legacy"
  # non-managed AAD integration is deprecated). `azure_rbac_enabled =
  # false` keeps AUTHORIZATION as native Kubernetes RBAC objects (see
  # `azure_rbac_enabled`'s own variable docstring for the real tradeoff),
  # with `admin_group_object_ids` granting the listed Azure AD groups
  # cluster-admin — everyone else's access comes from real
  # RoleBinding/ClusterRoleBinding objects in infra/k8s/base/rbac/
  # naming an Azure AD group's object ID as the subject.
  #
  # This does NOT change what the CI/deploy service principal can do —
  # that is a separate, already-real, and still-legitimately-broad grant
  # (`azurerm_role_assignment` for "Azure Kubernetes Service Cluster User
  # Role", used by automation, not a human) — see
  # `test_rbac_least_privilege.py`'s own updated comment for why it
  # correctly keeps passing even once this block is real and applied.
  azure_active_directory_role_based_access_control {
    managed                = true
    admin_group_object_ids = var.aad_admin_group_object_ids
    azure_rbac_enabled     = var.azure_rbac_enabled
  }

  tags = var.tags
}
