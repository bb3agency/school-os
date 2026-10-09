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
  description = "CMK for the files bucket."
  type        = string
}

variable "artifacts_kms_key_arn" {
  description = "Own CMK for the release artifacts bucket. Every dedicated host may decrypt it, so it is never the data key (audit 2026-10-05 key separation)."
  type        = string

  validation {
    condition     = var.artifacts_kms_key_arn != var.data_kms_key_arn
    error_message = "The artifacts bucket needs its own CMK, never the data key: every dedicated host may decrypt it."
  }
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

variable "files_upload_origins" {
  description = "Browser origins that upload to the files bucket with presigned POST (the school-facing app, e.g. https://app.schoolos.in). Exact https origins; empty = no CORS rule (browsers cannot upload)."
  type        = list(string)
  default     = []
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
