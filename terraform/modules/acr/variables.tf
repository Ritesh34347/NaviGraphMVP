variable "name" {
  description = "Name of the container registry. Must be globally unique, alphanumeric only."
  type        = string
}

variable "location" {
  description = "Azure region for the container registry."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group the container registry is created in."
  type        = string
}

variable "sku" {
  description = "SKU for the container registry (Basic, Standard, or Premium)."
  type        = string
  default     = "Basic"
}

variable "tags" {
  description = "Tags applied to the container registry."
  type        = map(string)
  default     = {}
}
