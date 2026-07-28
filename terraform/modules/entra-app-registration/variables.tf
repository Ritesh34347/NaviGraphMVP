variable "display_name" {
  description = "Display name for the Azure AD (Entra ID) app registration."
  type        = string
}

variable "sign_in_audience" {
  description = "Sign-in audience for the app registration (e.g. AzureADMyOrg for single-tenant)."
  type        = string
  default     = "AzureADMyOrg"
}

variable "redirect_uris" {
  description = "OAuth2/OIDC redirect URIs registered for the web platform."
  type        = list(string)
  default     = []
}
