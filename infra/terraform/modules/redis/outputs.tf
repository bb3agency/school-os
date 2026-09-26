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

output "secret_arn" {
  description = "Connection secret ARN (JSON keys: host, port, auth_token, url)."
  value       = aws_secretsmanager_secret.this.arn
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
