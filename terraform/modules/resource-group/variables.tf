variable "name" {
  description = "Name of the resource group."
  type        = string
}

variable "location" {
  description = "Azure region the resource group is created in."
  type        = string
}

variable "tags" {
  description = "Tags applied to the resource group."
  type        = map(string)
  default     = {}
}
