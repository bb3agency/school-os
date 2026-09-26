variable "name_prefix" {
  description = "Alias prefix, e.g. sos-prod. Aliases become alias/<name_prefix>-<key>."
  type        = string
}

variable "keys" {
  description = <<-EOT
    Customer-managed keys to create, keyed by short name (e.g. data, audit, backup).
    allow_cloudwatch_logs: let CloudWatch Logs in this region encrypt log groups with the key.
    service_principals:    AWS services (e.g. cloudwatch.amazonaws.com for encrypted SNS alarms) allowed
                           to use the key on behalf of this account only (aws:SourceAccount).
  EOT
  type = map(object({
    description           = string
    allow_cloudwatch_logs = optional(bool, false)
    service_principals    = optional(list(string), [])
  }))

  validation {
    condition     = alltrue([for k in keys(var.keys) : can(regex("^[a-z0-9-]+$", k))])
    error_message = "Key names must be lowercase letters, digits or hyphens."
  }
}

variable "deletion_window_in_days" {
  description = "Waiting period before a scheduled key deletion completes (crypto-shredding)."
  type        = number
  default     = 30

  validation {
    condition     = var.deletion_window_in_days >= 7 && var.deletion_window_in_days <= 30
    error_message = "deletion_window_in_days must be between 7 and 30."
  }
}

variable "rotation_period_in_days" {
  description = "Automatic key material rotation period (SEC-011: annual)."
  type        = number
  default     = 365
}

variable "admin_role_arns" {
  description = "Optional IAM role ARNs that administer (not use) the keys. The account root statement always exists so IAM policies work."
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Extra tags."
  type        = map(string)
  default     = {}
}
