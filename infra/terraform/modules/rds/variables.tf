variable "identifier" {
  description = "DB instance identifier, e.g. sos-prod-pg."
  type        = string
}

variable "vpc_id" {
  description = "VPC ID."
  type        = string
}

variable "subnet_ids" {
  description = "Data-tier subnet IDs (no internet route)."
  type        = list(string)
}

variable "allowed_security_groups" {
  description = "Security groups allowed to connect on 5432, keyed by a static name (e.g. api, worker, migrate) so for_each keys are known at plan time."
  type        = map(string)
  default     = {}
}

variable "engine_version" {
  description = "PostgreSQL version. A major-only value (\"16\") lets RDS pick the current minor with auto minor upgrades."
  type        = string
  default     = "16"

  validation {
    condition     = can(regex("^16(\\.[0-9]+)?$", var.engine_version))
    error_message = "SchoolOS targets PostgreSQL 16 (pgvector, pg_trgm, citext)."
  }
}

variable "instance_class" {
  description = "Instance class (Graviton preferred)."
  type        = string
  default     = "db.t4g.medium"
}

variable "allocated_storage_gb" {
  description = "Initial gp3 storage."
  type        = number
  default     = 50
}

variable "max_allocated_storage_gb" {
  description = "Storage autoscaling ceiling."
  type        = number
  default     = 200
}

variable "multi_az" {
  description = "Multi-AZ (Stage 1+)."
  type        = bool
  default     = false
}

variable "db_name" {
  description = "Initial database name."
  type        = string
  default     = "schoolos"
}

variable "master_username" {
  description = "Admin username (used only by the one-off db-bootstrap task and break-glass)."
  type        = string
  default     = "sos_admin"
}

variable "kms_key_arn" {
  description = "CMK for storage, Performance Insights and the managed master secret."
  type        = string
}

variable "backup_retention_days" {
  description = "Automated backup / PITR window (14 days Stage 0, 35 Stage 1+)."
  type        = number
  default     = 14

  validation {
    condition     = var.backup_retention_days >= 7 && var.backup_retention_days <= 35
    error_message = "backup_retention_days must be 7-35."
  }
}

variable "deletion_protection" {
  description = "Deletion protection (always true in prod)."
  type        = bool
  default     = true
}

variable "skip_final_snapshot" {
  description = "Skip final snapshot on destroy (staging only)."
  type        = bool
  default     = false
}

variable "monitoring_interval" {
  description = "Enhanced Monitoring interval in seconds (0 disables)."
  type        = number
  default     = 60
}

variable "log_min_duration_statement_ms" {
  description = "Log statements slower than this (bind values are never logged)."
  type        = number
  default     = 500
}

variable "idle_in_transaction_timeout_ms" {
  description = "Kill sessions idle inside a transaction after this long."
  type        = number
  default     = 60000
}

variable "extra_parameters" {
  description = "Additional DB parameters: name => {value, apply_method}."
  type = map(object({
    value        = string
    apply_method = optional(string, "immediate")
  }))
  default = {}
}

variable "maintenance_window" {
  description = "UTC maintenance window (default Sunday 02:00-03:00 IST)."
  type        = string
  default     = "sat:20:30-sat:21:30"
}

variable "backup_window" {
  description = "UTC backup window (default 01:00-01:30 IST)."
  type        = string
  default     = "19:30-20:00"
}

variable "tags" {
  description = "Extra tags."
  type        = map(string)
  default     = {}
}
