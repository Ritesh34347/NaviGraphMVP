variable "name" {
  description = "Name of the AKS cluster."
  type        = string
}

variable "location" {
  description = "Azure region for the AKS cluster."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group the AKS cluster is created in."
  type        = string
}

variable "dns_prefix" {
  description = "DNS prefix for the AKS cluster's API server."
  type        = string
}

variable "node_count" {
  description = "Number of nodes in the default node pool."
  type        = number
  default     = 2
}

variable "vm_size" {
  description = "VM size for nodes in the default node pool."
  type        = string
  default     = "Standard_D2s_v5"
}

variable "subnet_id" {
  description = "Subnet ID (from the networking module) the default node pool attaches to."
  type        = string
}

variable "tags" {
  description = "Tags applied to the AKS cluster."
  type        = map(string)
  default     = {}
}
