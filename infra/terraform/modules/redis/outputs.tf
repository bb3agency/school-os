output "primary_endpoint" {
  description = "Primary endpoint hostname."
  value       = aws_elasticache_replication_group.this.primary_endpoint_address
}

output "replication_group_id" {
  description = "Replication group ID (CloudWatch dimension)."
  value       = aws_elasticache_replication_group.this.id
}

output "security_group_id" {
  description = "Cache security group."
  value       = aws_security_group.this.id
}

output "user_secret_arns" {
  description = "Connection secret ARN per service user (web, api, worker, beat; JSON keys: host, port, username, password, url). Give each task only its own (P2-06)."
  value       = { for k, s in aws_secretsmanager_secret.user : k => s.arn }
}

output "access_strings" {
  description = "ElastiCache RBAC access string per user (asserted by tests)."
  value       = { for k, u in aws_elasticache_user.this : k => u.access_string }
}

output "posture" {
  description = "Security posture summary (asserted by tests)."
  value = {
    at_rest_encryption = aws_elasticache_replication_group.this.at_rest_encryption_enabled
    transit_encryption = aws_elasticache_replication_group.this.transit_encryption_enabled
    transit_mode       = aws_elasticache_replication_group.this.transit_encryption_mode
    engine             = aws_elasticache_replication_group.this.engine
  }
}

output "slow_log_kms_key_arn" {
  description = "CMK of the slow-log group (a key whose policy grants CloudWatch Logs)."
  value       = aws_cloudwatch_log_group.slow.kms_key_id
}
