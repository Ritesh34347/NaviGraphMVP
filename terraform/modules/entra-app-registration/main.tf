terraform {
  required_providers {
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 2.47"
    }
  }
}

resource "azuread_application" "this" {
  display_name     = var.display_name
  sign_in_audience = var.sign_in_audience

  web {
    redirect_uris = var.redirect_uris
  }
}

# Note: azuread_application exposes its app (client) ID as `client_id` in
# azuread provider versions >= 2.44 (renamed from the deprecated
# `application_id`). Pinning `~> 2.47` above to stay on the current
# attribute name.
resource "azuread_service_principal" "this" {
  client_id = azuread_application.this.client_id
}
