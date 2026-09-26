output "address" {
  description = "DB hostname."
  value       = aws_db_instance.this.address
}

output "port" {
  description = "DB port."
  value       = aws_db_instance.this.port
}

output "db_name" {
  description = "Database name."
  value       = aws_db_instance.this.db_name
}

output "instance_id" {
  description = "DB instance identifier (CloudWatch dimension)."
  value       = aws_db_instance.this.identifier
}

output "arn" {
  description = "DB instance ARN."
  value       = aws_db_instance.this.arn
}

output "security_group_id" {
  description = "DB security group."
  value       = aws_security_group.this.id
}

output "master_user_secret_arn" {
  description = "ARN of the RDS-managed master secret (JSON keys: username, password). Value never enters state."
  value       = try(aws_db_instance.this.master_user_secret[0].secret_arn, null)
}

output "posture" {
  description = "Security posture summary (asserted by tests)."
  value = {
    storage_encrypted     = aws_db_instance.this.storage_encrypted
    deletion_protection   = aws_db_instance.this.deletion_protection
    publicly_accessible   = aws_db_instance.this.publicly_accessible
    backup_retention_days = aws_db_instance.this.backup_retention_period
    force_ssl             = local.parameters["rds.force_ssl"].value
    managed_master_secret = aws_db_instance.this.manage_master_user_password
  }
}
