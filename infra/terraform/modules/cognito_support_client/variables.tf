variable "name" {
  description = "App client name, e.g. sos-prod-support or sos-ded-<school>-support."
  type        = string
}

variable "user_pool_id" {
  description = "The OPERATOR pool (ADR-0023): support tokens carry the operator issuer, never the staff issuer."
  type        = string
}

variable "callback_urls" {
  description = "BFF support callbacks: https://<school app host>/bff/auth/support/callback (apps/web/src/server/config.ts)."
  type        = list(string)

  validation {
    condition     = length(var.callback_urls) > 0 && alltrue([for u in var.callback_urls : can(regex("^https://[a-z0-9.-]+/bff/auth/support/callback$", u))])
    error_message = "Support callbacks are https://<host>/bff/auth/support/callback (the BFF's support redirect URI)."
  }
}

variable "logout_urls" {
  description = "Post-sign-out URLs: https://<school app host>/signed-out?kind=support."
  type        = list(string)

  validation {
    condition     = length(var.logout_urls) > 0 && alltrue([for u in var.logout_urls : can(regex("^https://[a-z0-9.-]+/signed-out\\?kind=support$", u))])
    error_message = "Support sign-out URLs are https://<host>/signed-out?kind=support."
  }
}

variable "access_token_minutes" {
  description = "Access/ID token lifetime (ADR-0023: 10 minutes)."
  type        = number
  default     = 10

  validation {
    condition     = var.access_token_minutes >= 5 && var.access_token_minutes <= 10
    error_message = "Support access tokens must live 5-10 minutes."
  }
}

variable "refresh_token_hours" {
  description = "Refresh token lifetime = absolute support session. The break-glass grant usually ends earlier; the API checks it on every request."
  type        = number
  default     = 12

  validation {
    condition     = var.refresh_token_hours >= 1 && var.refresh_token_hours <= 12
    error_message = "Support refresh tokens live 1-12 hours (07 §5.2)."
  }
}

variable "secret_name" {
  description = "Secrets Manager name for the client secret, e.g. sos/prod/oidc/support-client-secret."
  type        = string
}

variable "secrets_kms_key_arn" {
  description = "CMK for the client-secret secret."
  type        = string
}

variable "client_secret_version" {
  description = "Bump after regenerating the app client to rewrite its secret into Secrets Manager."
  type        = number
  default     = 1
}

variable "tags" {
  description = "Extra tags."
  type        = map(string)
  default     = {}
}
