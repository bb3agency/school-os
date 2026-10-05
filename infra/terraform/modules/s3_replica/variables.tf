variable "name" {
  description = "Globally unique name of the replica bucket in the backup region (e.g. sos-prod-files-replica-<account>). The replication role is <name>-repl."
  type        = string

  validation {
    condition     = length(var.name) <= 59
    error_message = "name: at most 59 characters (the role <name>-repl must fit IAM's 64)."
  }
}

variable "governance_bypass_principal_arns" {
  description = "Principals allowed s3:BypassGovernanceRetention on the replica (e.g. a two-person break-glass erasure role). Empty = nobody, so a version can only go when its lock ends."
  type        = list(string)
  default     = []
}

variable "source_region" {
  description = "Region of the source bucket (the shared and dedicated tiers run in ap-south-1)."
  type        = string
  default     = "ap-south-1"

  validation {
    condition     = var.source_region == "ap-south-1"
    error_message = "School files live in ap-south-1 (NFR-PRV-001)."
  }
}

variable "source_bucket_id" {
  description = "Name of the versioned source bucket (the files bucket)."
  type        = string
}

variable "source_kms_key_arn" {
  description = "CMK that encrypts the source objects (SSE-KMS); the replication role may only decrypt with it through S3."
  type        = string
}

variable "replica_kms_key_arn" {
  description = "CMK in the backup region that encrypts the replicas (SSE-KMS)."
  type        = string
}

variable "prefix" {
  description = "Key prefix to replicate (every school's objects live under t/)."
  type        = string
  default     = "t/"
}

variable "retention_days" {
  description = "Object Lock (GOVERNANCE) default retention of every replica version, in days: at least the files bucket's 90-day recovery window (docs/08 §7, docs/10 §9)."
  type        = number
  default     = 90

  validation {
    condition     = var.retention_days >= 90 && var.retention_days <= 400
    error_message = "retention_days must cover the 90-day recovery window (90-400)."
  }
}

variable "force_destroy" {
  description = "Allow destroying a non-empty replica (staging only; Object Lock still protects locked versions)."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Extra tags."
  type        = map(string)
  default     = {}
}
