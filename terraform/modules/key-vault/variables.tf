variable "name" {
  description = "Name of the Key Vault. Must be globally unique."
  type        = string
}

variable "location" {
  description = "Azure region for the Key Vault."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group the Key Vault is created in."
  type        = string
}

variable "tenant_id" {
  description = "Azure AD tenant ID that owns the Key Vault's access policies."
  type        = string
}

variable "sku_name" {
  description = "SKU for the Key Vault (standard or premium)."
  type        = string
  default     = "standard"
}

variable "tags" {
  description = "Tags applied to the Key Vault."
  type        = map(string)
  default     = {}
}
