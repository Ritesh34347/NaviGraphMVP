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

  tags = var.tags
}
