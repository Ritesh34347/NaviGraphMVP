variable "name" {
  description = "Name of the Postgres Flexible Server. Must be globally unique."
  type        = string
}

variable "location" {
  description = "Azure region for the Postgres Flexible Server."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group the Postgres Flexible Server is created in."
  type        = string
}

variable "administrator_login" {
  description = "Administrator login name for the Postgres Flexible Server."
  type        = string
}

variable "administrator_password" {
  description = "Administrator password for the Postgres Flexible Server. Never hardcode a real value at the call site — supply via TF_VAR_ or an untracked tfvars file."
  type        = string
  sensitive   = true
}

variable "storage_mb" {
  description = "Storage size in MB."
  type        = number
  default     = 32768
}

variable "sku_name" {
  description = "SKU name for the Postgres Flexible Server, e.g. B_Standard_B1ms for dev/burstable."
  type        = string
  default     = "B_Standard_B1ms"
}

variable "postgres_version" {
  description = "Postgres major version."
  type        = string
  default     = "16"
}

variable "tags" {
  description = "Tags applied to the Postgres Flexible Server."
  type        = map(string)
  default     = {}
}

variable "database_name" {
  description = "Name of the real application database created on this server (navigraph_catalog and navigraph_lineage both connect to this one database -- see LineageSettings' module docstring on sharing one physical instance). Phase 10: this module previously created only the server itself, leaving just its default 'postgres' database -- a real gap, since every service's POSTGRES_DB setting expects a database with this name to actually exist."
  type        = string
  default     = "navigraph"
}
