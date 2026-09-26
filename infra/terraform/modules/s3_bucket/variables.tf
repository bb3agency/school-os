variable "name" {
  description = "Globally unique bucket name."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.name))
    error_message = "Bucket name must be 3-63 lowercase letters, digits, dots or hyphens."
  }
}

variable "kms_key_arn" {
  description = "Customer-managed KMS key for SSE-KMS. Null means SSE-S3 (only for log-delivery buckets whose AWS writers cannot use SSE-KMS)."
  type        = string
  default     = null
}

variable "sse_s3_justification" {
  description = "Required when kms_key_arn is null: why this bucket cannot use SSE-KMS (e.g. ALB/S3 access-log delivery)."
  type        = string
  default     = null

  validation {
    condition     = var.sse_s3_justification == null || length(coalesce(var.sse_s3_justification, "")) >= 20
    error_message = "Give a real justification (>= 20 characters)."
  }
}

variable "versioning_enabled" {
  description = "Enable object versioning."
  type        = bool
  default     = true
}

variable "object_lock" {
  description = "Default Object Lock retention. Setting this enables Object Lock at bucket creation (cannot be added later)."
  type = object({
    mode  = string
    days  = optional(number)
    years = optional(number)
  })
  default = null

  validation {
    condition     = var.object_lock == null || contains(["COMPLIANCE", "GOVERNANCE"], try(var.object_lock.mode, ""))
    error_message = "object_lock.mode must be COMPLIANCE or GOVERNANCE."
  }

  validation {
    condition     = var.object_lock == null || ((try(var.object_lock.days, null) == null) != (try(var.object_lock.years, null) == null))
    error_message = "Set exactly one of object_lock.days or object_lock.years."
  }
}

variable "lifecycle_rules" {
  description = "Lifecycle rules. Filter by prefix and/or object tags (tags are used for per-tenant paths such as t/<tenant>/exports/ where the variable part precedes the suffix)."
  type = list(object({
    id                                 = string
    prefix                             = optional(string)
    tags                               = optional(map(string), {})
    expiration_days                    = optional(number)
    noncurrent_version_expiration_days = optional(number)
    abort_incomplete_multipart_days    = optional(number)
    transition_days                    = optional(number)
    transition_storage_class           = optional(string)
    expired_object_delete_marker       = optional(bool)
  }))
  default = []
}

variable "access_logging_enabled" {
  description = "Enable S3 server access logging to access_log_bucket (a plain bool so count is known at plan time)."
  type        = bool
  default     = false
}

variable "access_log_bucket" {
  description = "Target bucket for S3 server access logs (required when access_logging_enabled)."
  type        = string
  default     = null
}

variable "access_log_prefix" {
  description = "Prefix for server access logs in the target bucket."
  type        = string
  default     = null
}

variable "additional_policy_json" {
  description = "Extra bucket policy statements (JSON policy document) merged with the TLS-only baseline."
  type        = string
  default     = null
}

variable "force_destroy" {
  description = "Allow terraform destroy to delete non-empty bucket. Keep false for anything holding school data."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Extra tags (default_tags from the provider apply as well)."
  type        = map(string)
  default     = {}
}
