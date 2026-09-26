# Cross-region replication of RDS automated backups (snapshots + transaction logs) to ap-south-2
# (docs/10 §9, NFR-AVL-002, NFR-PRV-001). Call this module with the DR-region provider:
#   module "rds_dr" { source = "../rds_backup_replication"  providers = { aws = aws.dr } ... }

variable "source_db_instance_arn" {
  description = "ARN of the source DB instance (ap-south-1)."
  type        = string
}

variable "kms_key_arn" {
  description = "CMK in the destination region (ap-south-2) that encrypts the replicated backups."
  type        = string
}

variable "retention_days" {
  description = "Retention of replicated backups (docs/10 §9: 30 days)."
  type        = number
  default     = 30

  validation {
    condition     = var.retention_days >= 1 && var.retention_days <= 35
    error_message = "retention_days must be 1-35."
  }
}

resource "aws_db_instance_automated_backups_replication" "this" {
  source_db_instance_arn = var.source_db_instance_arn
  kms_key_id             = var.kms_key_arn
  retention_period       = var.retention_days
}

output "id" {
  description = "Replicated automated backups ARN."
  value       = aws_db_instance_automated_backups_replication.this.id
}

output "retention_days" {
  description = "Retention in the DR region."
  value       = aws_db_instance_automated_backups_replication.this.retention_period
}
