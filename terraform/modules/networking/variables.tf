variable "name" {
  description = "Name of the virtual network."
  type        = string
}

variable "location" {
  description = "Azure region for the virtual network."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group the virtual network is created in."
  type        = string
}

variable "address_space" {
  description = "CIDR address space for the virtual network."
  type        = list(string)
}

variable "subnet_name" {
  description = "Name of the single subnet created within the virtual network."
  type        = string
}

variable "subnet_address_prefixes" {
  description = "CIDR address prefixes for the subnet."
  type        = list(string)
}

variable "tags" {
  description = "Tags applied to networking resources."
  type        = map(string)
  default     = {}
}
