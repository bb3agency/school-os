variable "name_prefix" {
  description = "Name prefix, e.g. sos-prod."
  type        = string
}

variable "tenant_domain_prefix" {
  description = "Cognito hosted-domain prefix for the school-staff pool (globally unique in the region)."
  type        = string
}

variable "tenant_callback_urls" {
  description = "BFF OIDC callback URLs: <APP_BASE_URL>/bff/auth/callback (apps/web/README.md)."
  type        = list(string)
}

variable "tenant_logout_urls" {
  description = "Allowed post-logout redirect URLs: <APP_BASE_URL>/signed-out."
  type        = list(string)
}

variable "create_platform_pool" {
  description = "Create the separate platform-operator pool (shared tier only; dedicated hosts have no control plane)."
  type        = bool
  default     = true
}

variable "platform_domain_prefix" {
  description = "Hosted-domain prefix for the platform-operator pool."
  type        = string
  default     = null
}

variable "platform_callback_urls" {
  description = "Platform admin callback URLs: <APP_BASE_URL>/bff/auth/platform/callback."
  type        = list(string)
  default     = []
}

variable "platform_logout_urls" {
  description = "Platform admin logout URLs: <APP_BASE_URL>/signed-out?kind=operator."
  type        = list(string)
  default     = []
}

variable "user_pool_tier" {
  description = "Cognito feature plan (ADR-0018: ESSENTIALS for both pools; needed for access-token customisation). PLUS adds threat protection."
  type        = string
  default     = "ESSENTIALS"

  validation {
    condition     = contains(["LITE", "ESSENTIALS", "PLUS"], var.user_pool_tier)
    error_message = "user_pool_tier must be LITE, ESSENTIALS or PLUS."
  }
}

variable "advanced_security_mode" {
  description = "Threat protection mode on the PLUS plan (ENFORCED | AUDIT). Ignored (OFF) on ESSENTIALS/LITE."
  type        = string
  default     = "ENFORCED"
}

variable "deletion_protection" {
  description = "ACTIVE or INACTIVE."
  type        = string
  default     = "ACTIVE"
}

variable "access_token_minutes" {
  description = "Access/ID token lifetime (07 §5.2: <= 10 minutes)."
  type        = number
  default     = 10

  validation {
    condition     = var.access_token_minutes >= 5 && var.access_token_minutes <= 10
    error_message = "Access tokens must live 5-10 minutes."
  }
}

variable "refresh_token_hours" {
  description = "Refresh token lifetime = absolute session length (07 §5.2: 12 h)."
  type        = number
  default     = 12
}

variable "ses_email_identity_arn" {
  description = "Verified SES identity ARN for invite/reset emails (null = Cognito default sender, low daily quota)."
  type        = string
  default     = null
}

variable "from_email_address" {
  description = "From address when SES is used, e.g. SchoolOS <no-reply@schoolos.in>."
  type        = string
  default     = null
}

variable "secrets_kms_key_arn" {
  description = "CMK for the client-secret secrets."
  type        = string
}

variable "secret_name_prefix" {
  description = "Secrets Manager prefix, e.g. sos/prod."
  type        = string
}

variable "client_secret_version" {
  description = "Bump after regenerating an app client to rewrite its secret into Secrets Manager."
  type        = number
  default     = 1
}

variable "logs_kms_key_arn" {
  description = "CMK for the pre-token Lambda log group (key policy must allow CloudWatch Logs)."
  type        = string
}

variable "log_retention_days" {
  description = "Lambda log retention."
  type        = number
  default     = 400
}

variable "tags" {
  description = "Extra tags."
  type        = map(string)
  default     = {}
}
