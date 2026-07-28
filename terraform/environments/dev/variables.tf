variable "subscription_id" {
  description = "Azure subscription ID to deploy into. No default — must be supplied via terraform.tfvars or an environment variable, and is never applied against a real subscription during this phase (see terraform/README.md)."
  type        = string
}

variable "tenant_id" {
  description = "Azure AD (Entra ID) tenant ID that owns the subscription and the app registration."
  type        = string
}

variable "region" {
  description = "Azure region to deploy resources into."
  type        = string
  default     = "eastus"
}

variable "resource_group_name" {
  description = "Name of the resource group all dev-environment resources are created in."
  type        = string
  default     = "navigraph-dev-rg"
}

variable "environment" {
  description = "Short environment name, used in resource naming and tags."
  type        = string
  default     = "dev"
}

variable "postgres_administrator_password" {
  description = "Administrator password for the dev Postgres Flexible Server. Supply via a .tfvars file that is never committed, or via TF_VAR_postgres_administrator_password — never hardcode a real value here."
  type        = string
  sensitive   = true
  default     = "placeholder-never-applied"
}

variable "tags" {
  description = "Common tags applied to every resource in this environment."
  type        = map(string)
  default = {
    project     = "navigraph"
    environment = "dev"
    managed_by  = "terraform"
  }
}
