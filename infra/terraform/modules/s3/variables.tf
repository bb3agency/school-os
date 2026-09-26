variable "name_prefix" {
  description = "Bucket name prefix, e.g. sos-prod. Buckets: <prefix>-files, <prefix>-audit-archive, <prefix>-logs, <prefix>-artifacts."
  type        = string
}

variable "bucket_suffix" {
  description = "Optional suffix to make names globally unique (e.g. the AWS account ID)."
  type        = string
  default     = ""
}

variable "data_kms_key_arn" {
  description = "CMK for the files and artifacts buckets."
  type        = string
}

variable "audit_kms_key_arn" {
  description = "CMK for the audit archive bucket."
  type        = string
}

variable "audit_object_lock_mode" {
  description = "Object Lock mode for the audit archive (COMPLIANCE in prod; nobody, including root, can delete before expiry)."
  type        = string
  default     = "COMPLIANCE"
}

variable "audit_object_lock_years" {
  description = "Default audit archive retention in years."
  type        = number
  default     = 3
}

variable "audit_object_lock_days" {
  description = "Alternative retention in days (staging uses a short window). Overrides years when set."
  type        = number
  default     = null
}

variable "files_noncurrent_version_days" {
  description = "Days to keep noncurrent versions of files (recovery window for overwrites/deletes)."
  type        = number
  default     = 90
}

variable "logs_retention_days" {
  description = "ALB/S3 access log retention (>= 400 days, docs/10 §5)."
  type        = number
  default     = 400
}

variable "force_destroy" {
  description = "Allow destroying non-empty buckets (staging only; never prod)."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Extra tags."
  type        = map(string)
  default     = {}
}
