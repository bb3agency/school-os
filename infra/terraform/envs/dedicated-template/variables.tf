variable "aws_region" {
  description = "Host region (guarded to ap-south-1)."
  type        = string
  default     = "ap-south-1"

  validation {
    condition     = var.aws_region == "ap-south-1"
    error_message = "Dedicated hosts run in ap-south-1 (NFR-PRV-001)."
  }
}

variable "backup_region" {
  description = "Backup region (guarded to ap-south-2)."
  type        = string
  default     = "ap-south-2"

  validation {
    condition     = var.backup_region == "ap-south-2"
    error_message = "Backups go to ap-south-2 (NFR-PRV-001)."
  }
}

variable "aws_account_id" {
  description = "AWS account that hosts dedicated schools (normally the prod account)."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id must be 12 digits."
  }
}

variable "owner" {
  description = "Mandatory tag: owner."
  type        = string
}

variable "cost_center" {
  description = "Mandatory tag: cost centre (e.g. the school's billing account)."
  type        = string
}

variable "school_code" {
  description = "Short stable school identifier (matches the control-plane deployment record)."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,30}[a-z0-9]$", var.school_code))
    error_message = "school_code: 3-32 lowercase letters, digits or hyphens."
  }
}

variable "deployment_id" {
  description = "Deployment UUID from the platform panel (Provision school -> Dedicated)."
  type        = string
}

variable "domain" {
  description = "Platform hostname for this school, e.g. svhs-guntur.schoolos.in."
  type        = string
}

variable "custom_domain" {
  description = "Optional school-owned hostname (e.g. office.svhs.edu.in); the school creates an A record to the Elastic IP."
  type        = string
  default     = ""
}

variable "acme_email" {
  description = "ACME (Let's Encrypt) contact email."
  type        = string
}

variable "instance_type" {
  description = "EC2 instance type (Graviton default)."
  type        = string
  default     = "t4g.medium"
}

variable "data_volume_gb" {
  description = "Data volume size."
  type        = number
  default     = 100
}

variable "vpc_cidr" {
  description = "Per-school VPC CIDR (schools are isolated, so overlapping ranges are fine)."
  type        = string
  default     = "10.40.0.0/16"
}

variable "release_version" {
  description = "Initial release (image tag)."
  type        = string
}

variable "bundle_sha256" {
  description = "SHA-256 of the release bundle (from the CI release notes)."
  type        = string
  default     = ""
}

variable "artifacts_bucket" {
  description = "Artifacts bucket of the shared prod stack (terraform output buckets.artifacts)."
  type        = string
}

variable "artifacts_kms_key_arn" {
  description = "KMS key of the artifacts bucket (shared prod stack output kms_key_arns.data)."
  type        = string
}

variable "ecr_account_id" {
  description = "Account that owns the ECR repositories (defaults to aws_account_id)."
  type        = string
  default     = null
}

variable "control_plane_url" {
  description = "Control-plane URL for heartbeats, e.g. https://app.schoolos.in."
  type        = string
}

variable "route53_zone_id" {
  description = "Hosted zone of the platform domain (null = create the A record manually)."
  type        = string
  default     = null
}

variable "walg_enabled" {
  description = "Continuous WAL archiving (RPO <= 15 min)."
  type        = bool
  default     = false
}

variable "backup_object_lock_days" {
  description = "GOVERNANCE-mode Object Lock on backups (ransomware protection); 0 disables."
  type        = number
  default     = 30
}

variable "daily_backup_retention_days" {
  description = "Retention of nightly pg_dump files (daily/ prefix). WAL-G base backups and WAL (wal-g/) are pruned by WAL-G itself (retain 14 full)."
  type        = number
  default     = 35
}

variable "monthly_backup_retention_days" {
  description = "Retention of the first-of-month dump (monthly/ prefix)."
  type        = number
  default     = 400
}

variable "termination_protection" {
  description = "EC2 termination protection (disable only for decommissioning)."
  type        = bool
  default     = true
}
